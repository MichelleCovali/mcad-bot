"""Research recall checks use explicit fake web traces, never live providers."""
from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from mcad.ai_monitor import AIResponseError, get_monitor_status, run_monitor_once, update_monitor_settings
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import AIRun, Candidate, CandidateNotification, utcnow
from mcad.service import iso
from test_ai_monitor import FakeClient, URL_A, URL_B, discovery, response, verification


@pytest.fixture
def monitor():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    migrate(engine)
    factory = get_session_factory(engine)
    settings = Settings(enable_workers=False, enable_ai_search=True, openai_api_key="fixture-only")
    update_monitor_settings(factory, settings, {"enabled": True})
    try:
        yield factory, settings
    finally:
        engine.dispose()


def search(found=None, query="maritime cyber incident today"):
    result = response(found if found is not None else {"candidates": []})
    result["output"][0]["action"]["queries"] = [query]
    return result


def test_empty_primary_is_rescued_by_complementary_live_search(monitor):
    factory, settings = monitor
    client = FakeClient(search(), search(discovery(), "interferencia GPS buques hoy"), response(verification(), opened=True))
    result = run_monitor_once(factory, settings, client=client)
    assert result["status"] == "completed" and result["candidatesCreated"] == 1
    assert sum(request["max_tool_calls"] for request in client.requests) == 15
    assert all("allowed_domains" not in request["tools"][0] for request in client.requests)
    assert all("filters" not in request["tools"][0] for request in client.requests)
    audit = result["coverage"]
    assert [item["status"] for item in audit["passes"]] == ["completed", "completed"]
    assert audit["passes"][1]["queries"] == ["interferencia GPS buques hoy"]
    assert audit["completenessGuaranteed"] is False
    daily = get_monitor_status(factory, settings)["coverageToday"]
    assert daily["sweepRunsCompleted"] == 1 and daily["completenessGuaranteed"] is False


def test_cross_pass_repeat_reaches_verifier_once(monitor):
    factory, settings = monitor
    client = FakeClient(search(discovery()), search(discovery(), "port authority cyber outage"), response(verification(), opened=True))
    result = run_monitor_once(factory, settings, client=client)
    prompt = json.loads(client.requests[2]["input"][1]["content"])
    assert len(prompt["leads"]) == 1
    assert result["candidatesCreated"] == 1


def test_gap_failure_keeps_usage_and_marks_total_estimate_unknown(monitor):
    factory, settings = monitor
    update_monitor_settings(factory, settings, {"costRates": {
        "discoveryInput": 1, "discoveryOutput": 2, "verificationInput": 3,
        "verificationOutput": 4, "webCalls": 10}})
    class Provider(FakeClient):
        def create_response(self, payload):
            if len(self.requests) == 1:
                self.requests.append(deepcopy(payload))
                raise AIResponseError("Synthetic provider failure")
            return super().create_response(payload)
    client = Provider(search(discovery()), response(verification(), opened=True))
    result = run_monitor_once(factory, settings, client=client)
    assert result["status"] == "completed" and result["candidatesCreated"] == 1
    assert result["inputTokens"] == 200 and result["knownEstimatedUsd"] > 0
    assert result["estimatedUsd"] is None and result["billableSearchCalls"] is None
    assert result["coverage"]["passes"][1]["status"] == "failed"
    assert get_monitor_status(factory, settings)["coverageToday"]["runsIncomplete"] == 1


def test_pause_after_primary_stops_gap_and_verification(monitor):
    factory, settings = monitor
    client = FakeClient(search(discovery()), on_call=lambda n: update_monitor_settings(factory, settings, {"enabled": False}))
    result = run_monitor_once(factory, settings, client=client)
    assert result["status"] == "failed" and len(client.requests) == 1
    assert result["candidatesCreated"] == 0


@pytest.mark.parametrize("limit", [2, 3, 5])
def test_small_budget_is_explicitly_partial(monitor, limit):
    factory, settings = monitor
    update_monitor_settings(factory, settings, {"maxToolCalls": limit})
    client = FakeClient(search())
    result = run_monitor_once(factory, settings, client=client)
    assert result["status"] == "completed" and len(client.requests) == 1
    assert result["coverage"]["passes"][1]["status"] == "skipped_budget"
    assert get_monitor_status(factory, settings)["coverageToday"]["sweepRunsCompleted"] == 0


def test_short_lookback_still_requests_the_current_utc_day(monitor):
    factory, settings = monitor
    update_monitor_settings(factory, settings, {"lookbackHours": 1})
    result = run_monitor_once(factory, settings, client=FakeClient(search(), search(query="AIS GNSS maritime disruption")))
    now = utcnow()
    assert result["coverage"]["windowStart"] <= iso(now.replace(hour=0, minute=0, second=0, microsecond=0))


def test_legacy_runs_and_repeated_queries_are_not_claimed_as_complete(monitor):
    factory, settings = monitor
    with factory.begin() as session:
        session.add(AIRun(status="completed"))
    result = run_monitor_once(factory, settings, client=FakeClient(search(), search()))
    daily = get_monitor_status(factory, settings)["coverageToday"]
    assert daily["runsWithoutAudit"] == 1 and daily["sweepRunsCompleted"] == 0
    assert any("repeated" in note for note in result["coverage"]["limitations"])


def test_pending_single_source_draft_is_rechecked_in_place(monitor):
    factory, settings = monitor
    fields = {"title": "Fictional port incident", "year": utcnow().year, "referenceUrls": [URL_A]}
    with factory.begin() as session:
        draft = Candidate(title=fields["title"], source_url=URL_A, origin="ai", proposed_fields=fields,
            verification_status="needs_review", first_seen_at=utcnow() - timedelta(hours=12),
            evidence={"firstReportedAt": iso(utcnow() - timedelta(hours=12)), "eventDate": None,
                "checkedAt": iso(utcnow() - timedelta(hours=8)), "sources": [{"url": URL_A}]})
        session.add(draft)
        session.flush()
        draft_id = draft.id
    client = FakeClient(search(), search(query="maritime AIS disruption"), response(verification(), opened=True))
    result = run_monitor_once(factory, settings, client=client)
    assert result["candidatesCreated"] == 0 and result["coverage"]["verification"]["updatedReports"] == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Candidate)) == 1
        assert session.get(Candidate, draft_id).verification_status == "supported"
        assert session.scalar(select(func.count()).select_from(CandidateNotification)) == 0


def test_deferred_leads_survive_candidate_cap_and_a_failed_search(monitor):
    factory, settings = monitor
    update_monitor_settings(factory, settings, {"maxCandidates": 1, "runsPerDay": 6})
    first, extra = discovery(), discovery()
    extra["candidates"][0].update(title="Second fictional port incident", sourceUrls=[URL_B])
    result = run_monitor_once(factory, settings, client=FakeClient(search(first), search(extra, "other port outage"), response(verification(), opened=True)))
    assert result["coverage"]["verification"]["leadsDeferred"] == 1
    assert result["coverage"]["deferredLeads"][0]["title"] == "Second fictional port incident"
    failed = run_monitor_once(factory, settings, client=FakeClient())
    assert failed["status"] == "failed"
    assert failed["coverage"]["deferredLeads"][0]["title"] == "Second fictional port incident"


def test_renamed_followup_is_quarantined_instead_of_creating_a_duplicate(monitor):
    factory, settings = monitor
    with factory.begin() as session:
        session.add(Candidate(title="Fictional port incident", source_url=URL_A, origin="ai",
            proposed_fields={"title": "Fictional port incident", "year": utcnow().year, "referenceUrls": [URL_A]},
            verification_status="needs_review", first_seen_at=utcnow() - timedelta(hours=12),
            evidence={"firstReportedAt": iso(utcnow() - timedelta(hours=12)),
                "checkedAt": iso(utcnow() - timedelta(hours=8)), "sources": [{"url": URL_A}]}))
    verified = verification()
    verified["candidates"][0]["fields"]["title"] = "A different headline for the same port attack"
    result = run_monitor_once(factory, settings, client=FakeClient(search(),
        search(query="shipping ransomware"), response(verified, opened=True)))
    assert result["status"] == "completed" and result["candidatesCreated"] == 0
    assert result["coverage"]["unmatchedFollowups"][0]["title"] == "A different headline for the same port attack"
    assert result["coverage"]["verification"]["leadsDeferred"] == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Candidate)) == 1
        assert session.scalar(select(Candidate)).verification_status == "needs_review"


def test_utc_daily_report_does_not_include_previous_day_or_future_rows(monitor):
    factory, settings = monitor
    now = utcnow().replace(hour=0, minute=5, second=0, microsecond=0)
    with factory.begin() as session:
        for instant in (now - timedelta(minutes=10), now, now + timedelta(days=1)):
            session.add(AIRun(status="completed", started_at=instant))
    with patch("mcad.ai_monitor.utcnow", return_value=now):
        status = get_monitor_status(factory, settings)
    assert status["usageToday"]["runs"] == 1
    assert status["coverageToday"]["dateUTC"] == now.date().isoformat()
    assert status["coverageToday"]["runsWithoutAudit"] == 1


def test_verifier_batch_reserves_room_for_corroboration_and_retains_omitted_leads(monitor):
    factory, settings = monitor
    found = discovery()
    for index in range(1, 5):
        found["candidates"].append({**found["candidates"][0], "title": f"Fictional distinct port {index}",
            "sourceUrls": [f"https://port-{index}.example.org/report"]})
    first = search(found)
    first["output"][0]["action"]["sources"] += [{"url": row["sourceUrls"][0]} for row in found["candidates"][1:]]
    client = FakeClient(first, search(query="other maritime incidents"), response(verification(), opened=True))
    result = run_monitor_once(factory, settings, client=client)
    prompt = json.loads(client.requests[2]["input"][1]["content"])
    assert len(prompt["leads"]) == prompt["maxCandidates"] == 3
    assert result["coverage"]["verification"]["leadsDeferred"] == 4
    assert len(result["coverage"]["deferredLeads"]) == 4
