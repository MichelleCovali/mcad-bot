"""Apply-update workflow tests use fictional incidents and an isolated SQLite DB."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from mcad.api import create_app
from mcad.config import Settings
from mcad.migrations import migrate
from mcad.models import Candidate, Delivery, Incident, IncidentSource, OutboxEvent


@pytest.fixture
def review_case(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'apply-update.sqlite3'}")
    migrate(engine)
    with Session(engine) as session, session.begin():
        incident = Incident(
            title="TEST - Existing Port Alpha breach",
            description="Verified original event.",
            impact="Originally unknown",
            status="published",
            source_url="https://example.invalid/original",
            raw_fields={"importedValue": "preserve"},
            sources=[IncidentSource(url="https://example.invalid/second", short_name="Original source")],
        )
        session.add(incident)
        session.flush()
        candidate = Candidate(
            title="TEST - Port Alpha updated impact",
            source_url="https://example.invalid/follow-up",
            status="pending",
            relationship_type="possible_update",
            related_incident_id=incident.id,
            relationship_reason="Similar event, newer report",
            evidence={"assessment": "Still requires human review"},
        )
        session.add(candidate)
        session.flush()
        ids = (incident.id, candidate.id)
    app = create_app(settings=Settings(admin_api_token="local-test-secret", enable_workers=False), engine=engine)
    try:
        with TestClient(app) as client:
            yield engine, client, ids
    finally:
        engine.dispose()


def endpoint(candidate_id):
    return f"/api/admin/candidates/{candidate_id}/apply-update"


HEADERS = {"Authorization": "Bearer local-test-secret"}


def test_applies_only_reviewed_changes_preserves_existing_sources_and_audits(review_case):
    engine, client, (incident_id, candidate_id) = review_case
    payload = {
        "reason": "Confirmed impacts in the follow-up source.",
        "changes": {"impact": "Verified temporary outage", "additionalInfo": "Additional reviewed details"},
        "sourceUrls": ["https://example.invalid/third", "https://example.invalid/third"],
    }
    # The older API still blocks publication as a new event.
    assert client.post(f"/api/admin/candidates/{candidate_id}/approve", headers=HEADERS, json={"title": "A duplicate"}).status_code == 409
    assert client.post(endpoint(candidate_id), json=payload).status_code == 401
    response = client.post(endpoint(candidate_id), json=payload, headers=HEADERS)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["candidate"]["status"] == "approved"
    assert result["candidate"]["incidentId"] == incident_id
    assert result["incident"]["id"] == incident_id
    assert result["incident"]["title"] == "TEST - Existing Port Alpha breach"
    assert result["incident"]["description"] == "Verified original event."
    assert result["incident"]["impact"] == "Verified temporary outage"
    assert result["incident"]["sourceUrl"] == "https://example.invalid/original"
    assert result["incident"]["rawFields"]["importedValue"] == "preserve"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Incident)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.scalar(select(func.count()).select_from(Delivery)) == 0
        incident = session.get(Incident, incident_id)
        sources = {item.url: item for item in incident.sources}
        assert set(sources) == {
            "https://example.invalid/original",
            "https://example.invalid/second",
            "https://example.invalid/follow-up",
            "https://example.invalid/third",
        }
        assert sources["https://example.invalid/second"].short_name == "Original source"
        review = incident.raw_fields["_updateReviews"][0]
        assert review["candidateId"] == candidate_id
        assert review["reason"] == payload["reason"]
        assert review["changes"]["impact"] == {
            "before": "Originally unknown", "after": "Verified temporary outage",
        }
        assert set(review["sourceUrlsAdded"]) == {"https://example.invalid/follow-up", "https://example.invalid/third"}
        candidate = session.get(Candidate, candidate_id)
        assert candidate.evidence["assessment"] == "Still requires human review"
        assert candidate.evidence["relationshipReviews"][0]["decision"] == "applied_update"
        assert candidate.relationship_type == "possible_update"  # Preserve original AI classification.
    assert client.post(endpoint(candidate_id), headers=HEADERS, json=payload).status_code == 409


@pytest.mark.parametrize("payload", [
    {},
    {"reason": "", "changes": {"impact": "new"}},
    {"reason": "x" * 2001, "changes": {"impact": "new"}},
    {"reason": "Review", "changes": {"status": "pending"}},
    {"reason": "Review", "changes": {"rawFields": {"foo": "evil"}}},
    {"reason": "Review", "changes": {"year": 2026}},
    {"reason": "Review", "changes": {"impact": " "}},
    {"reason": "Review", "changes": []},
    {"reason": "Review", "sourceUrls": ["file:///local"]},
    {"reason": "Review", "sourceUrls": [""]},
    {"reason": "Review", "sourceUrls": "https://example.invalid/new"},
    {"reason": "Review", "changes": {}},
])
def test_invalid_input_never_changes_candidate_or_incident(review_case, payload):
    engine, client, (incident_id, candidate_id) = review_case
    assert client.post(endpoint(candidate_id), json=payload, headers=HEADERS).status_code == 422
    with Session(engine) as session:
        candidate = session.get(Candidate, candidate_id)
        assert candidate.status == "pending"
        assert candidate.incident_id is None
        assert session.get(Incident, incident_id).impact == "Originally unknown"
        assert "_updateReviews" not in (session.get(Incident, incident_id).raw_fields or {})


def test_preconditions_and_no_op_prevent_unreviewed_or_duplicate_writes(review_case):
    engine, client, (incident_id, candidate_id) = review_case
    valid = {"reason": "Confirmed by reviewer", "changes": {"impact": "Updated"}}
    assert client.post(endpoint("missing-id"), json=valid, headers=HEADERS).status_code == 404
    with Session(engine) as session, session.begin():
        candidate = session.get(Candidate, candidate_id)
        candidate.relationship_type = "possible_duplicate"
    assert client.post(endpoint(candidate_id), json=valid, headers=HEADERS).status_code == 409
    with Session(engine) as session, session.begin():
        candidate = session.get(Candidate, candidate_id)
        candidate.relationship_type = "possible_update"
        candidate.related_incident_id = None
    assert client.post(endpoint(candidate_id), json=valid, headers=HEADERS).status_code == 409
    with Session(engine) as session, session.begin():
        candidate = session.get(Candidate, candidate_id)
        candidate.related_incident_id = incident_id
        candidate.source_url = "https://example.invalid/original/"  # Already linked to existing incident.
    assert client.post(endpoint(candidate_id), json={"reason": "Review", "changes": {"impact": "Originally unknown"}}, headers=HEADERS).status_code == 422
    with Session(engine) as session, session.begin():
        candidate = session.get(Candidate, candidate_id)
        candidate.status = "rejected"
    assert client.post(endpoint(candidate_id), json=valid, headers=HEADERS).status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Incident)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_source_only_update_adds_candidate_link_without_replacing_existing_source(review_case):
    engine, client, (incident_id, candidate_id) = review_case
    response = client.post(endpoint(candidate_id), json={"reason": "Verified additional evidence", "sourceUrls": ["https://example.invalid/extra"]}, headers=HEADERS)
    assert response.status_code == 200, response.text
    with Session(engine) as session:
        incident = session.get(Incident, incident_id)
        assert incident.impact == "Originally unknown"
        assert len(incident.raw_fields["_updateReviews"]) == 1
        assert len(incident.sources) == 4
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
