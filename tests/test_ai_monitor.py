"""AI monitor tests use synthetic Responses payloads and an isolated SQLite DB.

Every HTTP request is blocked; no OpenAI project key or Telegram token is used.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from mcad.ai_monitor import (
    AIUnavailable, FIELD_TYPES, get_monitor_status, monitor_due,
    run_monitor_once, update_monitor_settings,
)
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import AIConfig, AIRun, Candidate, Incident, OutboxEvent, WorkerLease, utcnow
from mcad.service import iso, upsert_incident


URL_A = "https://port-authority.example.org/incident"
URL_B = "https://independent-report.example.net/incident"


def source(url, group):
    return {"url": url, "title": "Synthetic source", "publisher": group,
        "independenceGroup": group, "isPrimary": group == "authority",
        "publishedAt": iso(utcnow() - timedelta(hours=1)),
        "snippet": "Synthetic source excerpt confirming the fictional port outage.",
        "supports": ["The fictional port experienced a cybersecurity incident."], "contradicts": []}


def discovery():
    return {"candidates": [{"title": "Fictional port incident", "summary": "Synthetic lead.",
        "sourceUrls": [URL_A], "eventDate": None,
        "firstReportedAt": iso(utcnow() - timedelta(hours=1))}]}


def verification():
    fields = {name: None for name in FIELD_TYPES}
    fields.update(title="Fictional port incident", year=utcnow().year,
        month=str(utcnow().month), summary="Synthetic reviewed facts only.",
        victim_identity="Fictional Port", incident_country="Netherlands", method="Ransomware")
    return {"candidates": [{"fields": fields, "assessment": "supported",
        "firstReportedAt": iso(utcnow() - timedelta(hours=1)), "eventDate": None,
        "sources": [source(URL_A, "authority"), source(URL_B, "independent newsroom")],
        "uncertainties": [], "contradictions": []}]}


def response(data, urls=(URL_A, URL_B), *, opened=False):
    tools = [{"type": "web_search_call", "action": {"type": "search", "sources": [{"url": url} for url in urls]}}]
    if opened:
        tools.extend({"type": "web_search_call", "action": {"type": "open_page", "url": url}} for url in urls)
    return {"id": "resp_synthetic", "status": "completed",
        "usage": {"input_tokens": 100, "output_tokens": 40},
        "output": [*tools, {"type": "message", "content": [{"type": "output_text", "text": json.dumps(data), "annotations": []}]}]}


class FakeClient:
    def __init__(self, *responses, on_call=None):
        self.responses, self.requests, self.on_call = list(responses), [], on_call

    def create_response(self, payload):
        self.requests.append(deepcopy(payload))
        if self.on_call:
            self.on_call(len(self.requests))
        return deepcopy(self.responses.pop(0))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite+pysqlite:///:memory:")
        migrate(self.engine)
        self.factory = get_session_factory(self.engine)
        self.settings = Settings(enable_ai_search=True, openai_api_key="synthetic-test-only", enable_workers=False)
        self.block_http = patch("requests.post", side_effect=AssertionError("External HTTP is forbidden in tests"))
        self.block_http.start()

    def tearDown(self):
        self.block_http.stop()
        self.engine.dispose()

    def enable(self, **changes):
        return update_monitor_settings(self.factory, self.settings, {"enabled": True, **changes})

    def count(self, model):
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model))

    def happy_client(self, verified=None, *, opened=True):
        return FakeClient(response(discovery()), response(verified or verification(), opened=opened))

    def test_key_alone_and_each_switch_cannot_start_a_paid_run(self):
        client = self.happy_client()
        for settings in [Settings(openai_api_key="synthetic-test-only"), self.settings]:
            with self.assertRaises(AIUnavailable):
                run_monitor_once(self.factory, settings, client=client)
        self.enable()
        disabled_server = replace(self.settings, enable_ai_search=False)
        self.assertFalse(monitor_due(self.factory, disabled_server))
        with self.assertRaises(AIUnavailable):
            run_monitor_once(self.factory, disabled_server, client=client)
        self.assertEqual(client.requests, [])
        self.assertEqual(self.count(AIRun), 0)

    def test_status_and_settings_are_persistent_and_never_expose_key(self):
        status = update_monitor_settings(self.factory, self.settings, {"runsPerDay": 6, "maxCandidates": 2})
        self.assertFalse(status["effectiveEnabled"])
        self.assertEqual(get_monitor_status(self.factory, self.settings)["runsPerDay"], 6)
        self.assertNotIn("synthetic-test-only", json.dumps(status))
        self.assertNotIn("synthetic-test-only", repr(self.settings))
        for payload in [{"runsPerDay": 5}, {"runsPerDay": True}, {"enabled": "true"}, {"maxToolCalls": 1}, {"lookbackHours": 169}, {"openaiApiKey": "not-accepted"}]:
            with self.assertRaises(ValueError):
                update_monitor_settings(self.factory, self.settings, payload)
        with self.assertRaises(AIUnavailable):
            update_monitor_settings(self.factory, replace(self.settings, enable_ai_search=False), {"enabled": True})

    def test_two_models_independent_research_save_pending_candidate_without_publication(self):
        self.enable()
        client = self.happy_client()
        result = run_monitor_once(self.factory, self.settings, client=client)
        self.assertEqual((result["status"], result["candidatesCreated"]), ("completed", 1))
        self.assertEqual(result["inputTokens"], 200)
        self.assertEqual(result["webSearchCalls"], 4)
        self.assertEqual([p["model"] for p in client.requests], ["gpt-6-luna", "gpt-6.1-sol"])
        self.assertEqual(sum(p["max_tool_calls"] for p in client.requests), 15)
        self.assertTrue(all(p["store"] is False for p in client.requests))
        self.assertTrue(all(p["tool_choice"] == "required" for p in client.requests))
        self.assertNotIn("filters", client.requests[0]["tools"][0])
        with self.factory() as session:
            candidate = session.scalar(select(Candidate))
            self.assertEqual(candidate.status, "pending")
            self.assertEqual(candidate.origin, "ai")
            self.assertEqual(candidate.verification_status, "supported")
            self.assertEqual(candidate.proposed_fields["referenceNumber"], "")
            self.assertIsNone(candidate.proposed_fields["latitude"])
            self.assertTrue(all(source["openedByVerifier"] for source in candidate.evidence["sources"]))
        self.assertEqual(self.count(OutboxEvent), 0)
        self.assertEqual(self.count(Incident), 0)

    def test_search_snippets_or_syndicated_sources_stay_in_review(self):
        self.enable()
        client = self.happy_client(opened=False)
        self.assertEqual(run_monitor_once(self.factory, self.settings, client=client)["status"], "completed")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Candidate)).verification_status, "needs_review")

    def test_shared_source_origin_and_contradictions_do_not_claim_support(self):
        self.enable()
        verified = verification()
        verified["candidates"][0]["sources"][1]["independenceGroup"] = "authority"
        result = run_monitor_once(self.factory, self.settings, client=self.happy_client(verified))
        self.assertEqual(result["status"], "completed")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Candidate)).verification_status, "needs_review")

    def test_refusal_incomplete_and_unknown_fields_fail_closed(self):
        self.enable()
        malformed = response(discovery())
        malformed["status"] = "incomplete"
        unknown = response({"candidates": [], "publish": True})
        refusal = response(discovery())
        refusal["output"][-1]["content"] = [{"type": "refusal", "refusal": "No"}]
        for item in [malformed, unknown, refusal]:
            run = run_monitor_once(self.factory, self.settings, client=FakeClient(item))
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["inputTokens"], 100)
        self.assertEqual(self.count(Candidate), 0)
        self.assertEqual(self.count(OutboxEvent), 0)

    def test_untraceable_or_private_sources_create_nothing(self):
        self.enable(runsPerDay=6)
        for url in ["https://unseen-source.example.com/fake", "http://127.0.0.1/", "http://localhost/", "javascript:alert(1)"]:
            verified = verification()
            verified["candidates"][0]["sources"][0]["url"] = url
            run = run_monitor_once(self.factory, self.settings, client=self.happy_client(verified))
            self.assertEqual(run["status"], "failed")
        self.assertEqual(self.count(Candidate), 0)

    def test_old_reprints_are_not_fresh_candidates(self):
        self.enable()
        verified = verification()
        verified["candidates"][0]["firstReportedAt"] = iso(utcnow() - timedelta(days=100))
        run = run_monitor_once(self.factory, self.settings, client=self.happy_client(verified))
        self.assertEqual((run["status"], run["candidatesCreated"]), ("completed", 0))

    def test_published_title_and_year_with_new_urls_requires_update_review(self):
        self.enable()
        with self.factory.begin() as session:
            incident, _ = upsert_incident(
                session,
                {
                    "title": "Fictional port incident",
                    "year": utcnow().year,
                },
                notify=False,
            )
            incident_id = incident.id

        run = run_monitor_once(
            self.factory,
            self.settings,
            client=self.happy_client(),
        )
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["candidatesCreated"], 1)

        with self.factory() as session:
            candidate = session.scalar(select(Candidate))
            self.assertEqual(
                candidate.relationship_type, "possible_update"
            )
            self.assertEqual(
                candidate.related_incident_id, incident_id
            )
            self.assertEqual(candidate.status, "pending")
            self.assertIsNone(candidate.incident_id)
            self.assertEqual(
                candidate.verification_status, "needs_review"
            )

    def test_rejected_source_alias_is_not_reintroduced_but_shared_background_can_be_retained(self):
        self.enable()
        with self.factory.begin() as session:
            session.add(Candidate(title="Earlier fictional incident", source_url=URL_A, status="rejected",
                proposed_fields={"referenceUrls": [URL_A, URL_B]}))
        run = run_monitor_once(self.factory, self.settings, client=self.happy_client())
        self.assertEqual(run["candidatesCreated"], 0)
        verified = verification()
        unique_url = "https://another-public-source.example.com/new"
        verified["candidates"][0]["sources"][1]["url"] = unique_url
        client = FakeClient(response(discovery()), response(verified, (URL_A, unique_url), opened=True))
        run = run_monitor_once(self.factory, self.settings, client=client)
        self.assertEqual(run["candidatesCreated"], 1)
        with self.factory() as session:
            new = session.scalar(select(Candidate).where(Candidate.status == "pending"))
            self.assertEqual(new.source_url, unique_url)

    def test_pause_during_discovery_prevents_verification_call(self):
        self.enable()
        client = FakeClient(response(discovery()), on_call=lambda _n: update_monitor_settings(self.factory, self.settings, {"enabled": False}))
        run = run_monitor_once(self.factory, self.settings, client=client)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(self.count(Candidate), 0)

    def test_daily_quota_and_concurrent_lease_prevent_paid_calls(self):
        self.enable()
        with self.factory.begin() as session:
            session.add(WorkerLease(name="ai_monitor_run", owner="another-process", expires_at=utcnow() + timedelta(minutes=5)))
        client = self.happy_client()
        with self.assertRaises(AIUnavailable):
            run_monitor_once(self.factory, self.settings, client=client)
        self.assertEqual(client.requests, [])
        with self.factory.begin() as session:
            session.delete(session.get(WorkerLease, "ai_monitor_run"))
            for _ in range(3):
                session.add(AIRun(status="failed", started_at=utcnow(), error="Synthetic completed attempt"))
        with self.assertRaises(AIUnavailable):
            run_monitor_once(self.factory, self.settings, client=client)
        self.assertFalse(monitor_due(self.factory, self.settings))
        self.assertEqual(client.requests, [])

    def test_atomic_scheduled_due_check_prevents_back_to_back_runs(self):
        self.enable()
        first = run_monitor_once(self.factory, self.settings, client=self.happy_client(), trigger="scheduled")
        self.assertEqual(first["status"], "completed")
        second_client = self.happy_client()
        with self.assertRaises(AIUnavailable):
            run_monitor_once(self.factory, self.settings, client=second_client, trigger="scheduled")
        self.assertEqual(second_client.requests, [])

    def test_no_results_with_no_source_urls_is_a_successful_empty_run(self):
        self.enable()
        client = FakeClient(response({"candidates": []}, urls=()))
        result = run_monitor_once(self.factory, self.settings, client=client)
        self.assertEqual((result["status"], result["candidatesCreated"]), ("completed", 0))
        self.assertEqual(len(client.requests), 1)

    def test_pause_while_context_is_prepared_prevents_first_paid_call(self):
        self.enable()
        def context_and_pause(_factory):
            update_monitor_settings(self.factory, self.settings, {"enabled": False})
            return {"knownIncidents": [], "reviewerFeedback": []}
        client = self.happy_client()
        with patch("mcad.ai_monitor._context", side_effect=context_and_pause):
            result = run_monitor_once(self.factory, self.settings, client=client)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(client.requests, [])

    def test_failed_web_tool_does_not_create_candidates(self):
        self.enable()
        item = response(discovery())
        item["output"][0]["status"] = "failed"
        result = run_monitor_once(self.factory, self.settings, client=FakeClient(item))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.count(Candidate), 0)


if __name__ == "__main__":
    unittest.main()
