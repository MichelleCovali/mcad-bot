"""Corroboration rechecks use synthetic drafts and never call providers."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import AIRun, Candidate, CandidateDelivery, CandidateNotification, Incident, Subscriber
from mcad.research_followups import (
    bind_followups, persist_followup, record_followup_attempts, select_followups,
)


NOW = datetime(2026, 11, 23, 16)
FIRST = "https://authority.example.org/report?id=1"
SECOND = "https://independent.example.net/port-report"


def iso(value):
    return value.isoformat() + "Z"


@pytest.fixture
def factory():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    migrate(engine)
    yield get_session_factory(engine)
    engine.dispose()


def add_candidate(factory, **changes):
    values = {"title": "Fictional port outage", "summary": "A source reports a fictional outage.",
        "source_url": FIRST, "origin": "ai", "status": "pending", "baseline": False,
        "verification_status": "needs_review", "first_seen_at": NOW - timedelta(hours=12),
        "proposed_fields": {"title": "Fictional port outage", "year": 2026,
                            "sourceUrl": FIRST, "referenceUrls": [FIRST]},
        "evidence": {"eventDate": "2026-11-22", "firstReportedAt": iso(NOW - timedelta(hours=12)),
                     "checkedAt": iso(NOW - timedelta(hours=8)),
                     "sources": [{"url": FIRST, "supports": ["Reported outage"]}]}}
    values.update(changes)
    with factory.begin() as session:
        candidate = Candidate(**values)
        session.add(candidate)
        session.flush()
        return candidate.id


def selected(factory, now=NOW, limit=5):
    return select_followups(factory, now, now - timedelta(hours=48), limit)


def proposal(**changes):
    value = {"title": "  FICTIONAL   PORT OUTAGE  ", "summary": "Freshly checked facts.",
        "source_url": SECOND, "verification_status": "supported",
        "proposed_fields": {"title": "Fictional port outage", "year": 2026,
                            "sourceUrl": SECOND, "referenceUrls": [FIRST, SECOND]},
        "evidence": {"eventDate": "2026-11-22", "firstReportedAt": iso(NOW - timedelta(hours=12)),
                     "checkedAt": iso(NOW), "sources": [{"url": FIRST}, {"url": SECOND}]}}
    value.update(changes)
    return value


def new_run(session):
    run = AIRun()
    session.add(run)
    session.flush()
    return run.id


def test_selects_recent_unresolved_drafts_and_flags_unknown_freshness(factory):
    identifier = add_candidate(factory, evidence={"checkedAt": iso(NOW - timedelta(hours=8)),
        "eventDate": None, "firstReportedAt": None, "sources": [{"url": FIRST}]})
    reports = selected(factory)
    assert len(reports) == 1
    assert reports[0]["followupCandidateId"] == identifier
    assert reports[0]["firstReportedAt"] is None
    assert reports[0]["_followupNeedsDateVerification"] is True
    assert "freshness is unconfirmed" in reports[0]["summary"]
    assert reports[0]["sourceUrls"] == [FIRST]


@pytest.mark.parametrize("changes", [
    {"status": "rejected"}, {"status": "approved"}, {"origin": "rss"}, {"baseline": True},
    {"verification_status": "supported"}, {"rejection_notes": "Reviewer draft comment"},
    {"evidence": {"firstReportedAt": iso(NOW - timedelta(days=10))}},
    {"evidence": {"firstReportedAt": iso(NOW + timedelta(hours=1))}},
    {"evidence": {"checkedAt": iso(NOW - timedelta(hours=5))}},
    {"evidence": {"classificationReviews": [{"field": "method"}]}},
    {"evidence": {"relationshipReviews": [{"decision": "new_incident"}]}},
    {"evidence": {"independentSourcesConfirmed": True}},
])
def test_selection_never_reopens_resolved_manual_old_or_cooled_down_reports(factory, changes):
    add_candidate(factory, **changes)
    assert selected(factory) == []


def test_selection_uses_material_development_date_and_has_a_limit(factory):
    evidence = {"firstReportedAt": "2020-01-01", "eventDate": "2020-01-01",
        "checkedAt": iso(NOW - timedelta(hours=6)),
        "materialUpdate": {"reportedAt": iso(NOW - timedelta(hours=10)),
                           "summary": "Recently confirmed recovery", "supportingUrls": [FIRST]}}
    for index in range(3):
        add_candidate(factory, source_url=FIRST + f"&report={index}",
            verification_status="contradicted", evidence=evidence)
    reports = selected(factory, now=NOW.replace(tzinfo=timezone.utc), limit=2)
    assert len(reports) == 2
    assert all(report["development"]["summary"] == "Recently confirmed recovery" for report in reports)
    assert selected(factory, limit=0) == []


def test_attempt_cooldown_survives_an_empty_or_failed_verification(factory):
    identifier = add_candidate(factory)
    reports = selected(factory)
    assert record_followup_attempts(factory, reports, NOW) == 1
    assert selected(factory) == []
    assert len(selected(factory, NOW + timedelta(hours=6))) == 1
    # Attempt bookkeeping alone does not invalidate a selected content snapshot.
    bound = bind_followups([proposal()], reports)[0]
    with factory.begin() as session:
        assert persist_followup(session, bound, new_run(session), NOW) == "updated"
        assert session.get(Candidate, identifier).verification_status == "supported"


def test_oldest_created_draft_does_not_starve_other_rechecks(factory):
    first = add_candidate(factory)
    second = add_candidate(factory, source_url=SECOND, first_seen_at=NOW - timedelta(hours=10))
    reports = selected(factory, limit=1)
    assert reports[0]["followupCandidateId"] == first
    record_followup_attempts(factory, reports, NOW)
    # Both are eligible at the next scheduled run, but the second draft has
    # waited longer for a check even though it was discovered more recently.
    assert selected(factory, NOW + timedelta(hours=8), limit=1)[0]["followupCandidateId"] == second


def test_binding_matches_canonical_sources_and_does_not_trust_output_ids(factory):
    identifier = add_candidate(factory)
    fresh = proposal()
    fresh["followupCandidateId"] = "provider-guessed-id"
    fresh["_followupCandidateId"] = "provider-injected-id"
    fresh["_followupSnapshot"] = "provider-injected-snapshot"
    fresh["_followupUnmatched"] = True
    fresh["proposed_fields"]["referenceUrls"] = [
        "http://www.authority.example.org:80/report?id=1&utm_source=test", SECOND]
    bound = bind_followups([fresh], selected(factory))[0]
    assert bound["_followupCandidateId"] == identifier
    assert "followupCandidateId" not in bound
    assert "_followupUnmatched" not in bound
    unrelated = proposal(title="Different event", _followupCandidateId=identifier,
                         _followupSnapshot=bound["_followupSnapshot"])
    bind_followups([unrelated], selected(factory))
    assert "_followupCandidateId" not in unrelated
    assert unrelated["_followupUnmatched"] is True


@pytest.mark.parametrize("change", ["title", "url", "date", "year", "development"])
def test_binding_rejects_a_distinct_event_or_development(factory, change):
    add_candidate(factory)
    fresh = proposal()
    if change == "title":
        fresh["title"] = "Unrelated vessel incident"
    elif change == "url":
        fresh["proposed_fields"]["referenceUrls"] = [SECOND]
        fresh["evidence"]["sources"] = [{"url": SECOND}]
    elif change == "date":
        fresh["evidence"]["eventDate"] = "2026-11-23"
    elif change == "year":
        fresh["proposed_fields"]["year"] = 2025
    else:
        fresh["evidence"]["materialUpdate"] = {"reportedAt": iso(NOW), "summary": "A separate update"}
    assert "_followupCandidateId" not in bind_followups([fresh], selected(factory))[0]


def test_binding_requires_one_locally_selected_target(factory):
    add_candidate(factory)
    add_candidate(factory, source_url="https://another.example.com/report")
    assert len(selected(factory)) == 2
    bound = bind_followups([proposal()], selected(factory))[0]
    assert "_followupCandidateId" not in bound
    assert bound["_followupUnmatched"] is True


def test_renamed_followup_and_same_identity_without_anchor_are_quarantined(factory):
    add_candidate(factory)
    renamed = proposal(title="An expanded headline for the fictional port incident")
    no_anchor = proposal(source_url=SECOND)
    no_anchor["proposed_fields"]["referenceUrls"] = [SECOND]
    no_anchor["evidence"]["sources"] = [{"url": SECOND}]
    for report in bind_followups([renamed, no_anchor], selected(factory)):
        assert report["_followupUnmatched"] is True
        assert "_followupCandidateId" not in report


def test_unrelated_new_report_cannot_inject_a_quarantine_flag(factory):
    add_candidate(factory)
    unrelated = proposal(title="A different vessel event", source_url=SECOND, _followupUnmatched=True)
    unrelated["proposed_fields"]["referenceUrls"] = [SECOND]
    unrelated["evidence"]["sources"] = [{"url": SECOND}]
    bound = bind_followups([unrelated], selected(factory))[0]
    assert "_followupUnmatched" not in bound


def test_refresh_preserves_identity_relationships_audit_and_existing_delivery(factory):
    with factory.begin() as session:
        incident = Incident(title="Original incident", source_url="https://old.example.com/report", year=2026)
        session.add(incident)
        session.flush()
        incident_id = incident.id
    identifier = add_candidate(factory, relationship_type="possible_update",
        related_incident_id=incident_id, relationship_reason="Existing local match")
    with factory.begin() as session:
        candidate = session.get(Candidate, identifier)
        candidate.evidence = {**candidate.evidence, "suggestedIncidentId": incident_id, "matchReason": "Existing local match"}
        subscriber = Subscriber(chat_id="12345", active=False)
        session.add(subscriber)
        session.flush()
        event = CandidateNotification(candidate_id=identifier, payload={"candidateId": identifier})
        session.add(event)
        session.flush()
        delivery = CandidateDelivery(event_id=event.id, chat_id=subscriber.chat_id,
                                     status="sent", telegram_message_id="42")
        session.add(delivery)
        session.flush()
        event_id, delivery_id = event.id, delivery.id
    reports = selected(factory)
    record_followup_attempts(factory, reports, NOW)
    fresh = bind_followups([proposal()], reports)[0]
    fresh["evidence"].update(suggestedIncidentId="provider-guessed-target", proposedRelatedIncidentId="provider-guessed-target")
    with factory.begin() as session:
        run_id = new_run(session)
        assert persist_followup(session, fresh, run_id, NOW) == "updated"
        assert persist_followup(session, fresh, run_id, NOW) == "stale"
    with factory() as session:
        candidate = session.get(Candidate, identifier)
        assert candidate.id == identifier and candidate.status == "pending"
        assert candidate.source_url == FIRST and candidate.first_seen_at == NOW - timedelta(hours=12)
        assert (candidate.relationship_type, candidate.related_incident_id, candidate.relationship_reason) == (
            "possible_update", incident_id, "Existing local match")
        assert candidate.evidence["suggestedIncidentId"] == incident_id
        assert "proposedRelatedIncidentId" not in candidate.evidence
        assert candidate.verification_status == "supported" and candidate.ai_run_id == run_id
        assert candidate.proposed_fields["referenceUrls"] == [FIRST, SECOND]
        previous = candidate.evidence["researchRechecks"][0]
        assert previous["previousVerificationStatus"] == "needs_review"
        assert previous["previousFields"]["referenceUrls"] == [FIRST]
        assert previous["previousEvidence"]["sources"] == [{"url": FIRST, "supports": ["Reported outage"]}]
        assert session.scalar(select(func.count()).select_from(Candidate)) == 1
        assert session.scalar(select(func.count()).select_from(CandidateNotification)) == 1
        assert session.get(CandidateNotification, event_id).candidate_id == identifier
        assert session.get(CandidateDelivery, delivery_id).telegram_message_id == "42"


@pytest.mark.parametrize("change", ["status", "fields", "relationship", "classification", "summary"])
def test_intervening_reviewer_change_is_never_overwritten(factory, change):
    identifier = add_candidate(factory)
    reports = selected(factory)
    fresh = bind_followups([proposal()], reports)[0]
    with factory.begin() as session:
        candidate = session.get(Candidate, identifier)
        if change == "status":
            candidate.status = "rejected"
            candidate.rejection_notes = "The reviewer rejected this claim."
        elif change == "fields":
            candidate.proposed_fields = {**candidate.proposed_fields, "method": "GPS jamming"}
        elif change == "relationship":
            candidate.relationship_type = "new_incident"
            candidate.relationship_reason = "Confirmed separate event"
        elif change == "classification":
            candidate.evidence = {**candidate.evidence, "classificationReviews": [{"field": "method"}]}
        else:
            candidate.summary = "Reviewer corrected description"
    assert record_followup_attempts(factory, reports, NOW) == 0
    with factory.begin() as session:
        candidate = session.get(Candidate, identifier)
        before = (candidate.status, deepcopy(candidate.proposed_fields), deepcopy(candidate.evidence),
                  candidate.relationship_type, candidate.relationship_reason, candidate.summary)
        assert persist_followup(session, fresh, new_run(session), NOW) == "stale"
        assert before == (candidate.status, candidate.proposed_fields, candidate.evidence,
                          candidate.relationship_type, candidate.relationship_reason, candidate.summary)


def test_repeated_rechecks_keep_nonrecursive_previous_evidence(factory):
    identifier = add_candidate(factory)
    for hour in (0, 6):
        now = NOW + timedelta(hours=hour)
        fresh = bind_followups([proposal(verification_status="needs_review")], selected(factory, now))[0]
        with factory.begin() as session:
            assert persist_followup(session, fresh, new_run(session), now) == "updated"
    with factory() as session:
        history = session.get(Candidate, identifier).evidence["researchRechecks"]
        assert len(history) == 2
        assert all("researchRechecks" not in entry["previousEvidence"] for entry in history)
