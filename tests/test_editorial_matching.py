"""Exercise update discovery and validation with synthetic research only."""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from mcad.ai_monitor import run_monitor_once, update_monitor_settings
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, Incident, utcnow
from mcad.service import iso, upsert_incident
from test_ai_monitor import FakeClient, URL_A, URL_B, discovery, response, source, verification


DEVELOPMENT = "The independently audited restore of the port booking system is complete."


@pytest.fixture
def research():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    migrate(engine)
    factory = get_session_factory(engine)
    settings = Settings(enable_ai_search=True, openai_api_key="synthetic-test-only", enable_workers=False)
    update_monitor_settings(factory, settings, {"enabled": True})
    try:
        yield factory, settings
    finally:
        engine.dispose()


def archived_incident(factory, *, summary="Booking operations at the fictional port were disrupted.", url=URL_A):
    event_date = date(2024, 5, 9)
    with factory.begin() as session:
        record, _ = upsert_incident(session, {
            "title": "Fictional port incident", "sourceUrl": url,
            "year": event_date.year, "month": str(event_date.month),
            "dateStart": iso(event_date), "datePrecision": "day",
            "victimIdentity": "Fictional Port", "summary": summary,
        })
        return record.id, event_date


def development_run(incident_id, event_date, *, original_link_is_current=True, text=DEVELOPMENT,
                    lead_urls=(URL_A,), sources=(URL_A, URL_B)):
    reporting_date = iso(utcnow() - timedelta(hours=1))
    update = {"reportedAt": reporting_date, "summary": text}
    original_date = iso(event_date)
    found = discovery()
    found["candidates"][0].update(eventDate=original_date, firstReportedAt=original_date,
        development=update, sourceUrls=list(lead_urls))
    verified = verification()
    row = verified["candidates"][0]
    row.update(eventDate=original_date, firstReportedAt=original_date,
        development={**update, "relatedIncidentId": incident_id, "supportingUrls": list(sources)})
    row["fields"].update(year=event_date.year, month=str(event_date.month), summary=text)
    row["sources"] = [source(sources[0], "authority"), source(sources[1], "independent newsroom")]
    for item in row["sources"]:
        item.update(snippet=text, supports=[text])
    if not original_link_is_current:
        row["sources"][0]["publishedAt"] = original_date
    return FakeClient(response(found, urls=lead_urls), response(verified, urls=sources, opened=True))


def test_fresh_update_at_known_link_preserves_original_event_date(research):
    factory, settings = research
    incident_id, event_date = archived_incident(factory)
    client = development_run(incident_id, event_date)
    outcome = run_monitor_once(factory, settings, client=client)
    assert outcome["status"] == "completed"
    assert outcome["candidatesCreated"] == 1
    assert len(client.requests) == 2
    request = json.loads(client.requests[1]["input"][1]["content"])
    assert request["possibleExistingIncidents"][0]["id"] == incident_id
    with factory() as session:
        candidate = session.scalar(select(Candidate))
        assert candidate.status == "pending"
        assert candidate.verification_status == "supported"
        assert candidate.evidence["suggestedIncidentId"] == incident_id
        assert candidate.evidence["eventDate"] == iso(event_date)
        assert candidate.evidence["materialUpdate"]["supportingUrls"] == [URL_A, URL_B]
        original = session.get(Incident, incident_id)
        assert original.date_start == event_date
        assert original.summary != DEVELOPMENT


def test_old_coverage_cannot_act_as_second_source_for_new_facts(research):
    factory, settings = research
    incident_id, event_date = archived_incident(factory)
    client = development_run(incident_id, event_date, original_link_is_current=False)
    assert run_monitor_once(factory, settings, client=client)["candidatesCreated"] == 1
    with factory() as session:
        candidate = session.scalar(select(Candidate))
        assert candidate.verification_status == "needs_review"
        assert candidate.evidence["materialUpdate"]["supportingUrls"] == [URL_B]
        assert candidate.evidence["suggestedIncidentId"] == incident_id


def test_reworded_republication_cannot_create_a_development(research):
    factory, settings = research
    incident_id, event_date = archived_incident(factory, summary=DEVELOPMENT)
    client = development_run(incident_id, event_date, text=DEVELOPMENT.upper().rstrip("."))
    assert run_monitor_once(factory, settings, client=client)["candidatesCreated"] == 0
    with factory() as session:
        assert session.scalar(select(Candidate)) is None


def test_previous_updates_are_available_to_verifier_and_not_requeued(research):
    factory, settings = research
    incident_id, event_date = archived_incident(factory)
    with factory.begin() as session:
        session.get(Incident, incident_id).raw_fields = {
            "_incidentUpdates": [{"summary": DEVELOPMENT, "reportedAt": "2024-06-01"}]
        }
    client = development_run(incident_id, event_date)
    assert run_monitor_once(factory, settings, client=client)["candidatesCreated"] == 0
    request = json.loads(client.requests[1]["input"][1]["content"])
    assert request["possibleExistingIncidents"][0]["knownDevelopments"] == [
        {"summary": DEVELOPMENT, "reportedAt": "2024-06-01"}
    ]


def test_model_suggestion_with_wrong_event_date_requires_manual_matching(research):
    factory, settings = research
    incident_id, event_date = archived_incident(factory)
    client = development_run(incident_id, event_date + timedelta(days=5))
    assert run_monitor_once(factory, settings, client=client)["candidatesCreated"] == 1
    with factory() as session:
        candidate = session.scalar(select(Candidate))
        assert candidate.verification_status == "needs_review"
        assert "suggestedIncidentId" not in candidate.evidence
        assert "proposedRelatedIncidentId" not in candidate.evidence


def test_research_can_match_updates_with_new_sources_to_archived_event(research):
    factory, settings = research
    incident_id, event_date = archived_incident(factory, url="https://archive.example.com/port-incident")
    client = development_run(incident_id, event_date)
    assert run_monitor_once(factory, settings, client=client)["candidatesCreated"] == 1
    with factory() as session:
        candidate = session.scalar(select(Candidate))
        assert candidate.verification_status == "supported"
        assert candidate.evidence["suggestedIncidentId"] == incident_id
