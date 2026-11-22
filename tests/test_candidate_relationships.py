from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mcad.migrations import migrate
from mcad.models import Candidate, Incident
from mcad.service import candidate_dict


def test_candidate_relationship_survives_database_round_trip():
    engine = create_engine("sqlite:///:memory:")
    try:
        migrate(engine)

        with Session(engine) as session:
            incident = Incident(title="Port X cyberattack")
            session.add(incident)
            session.flush()
            incident_id = incident.id

            candidate = Candidate(
                title="New information about Port X",
                source_url="https://example.com/port-x-update",
                relationship_type="possible_update",
                related_incident_id=incident_id,
                relationship_reason="Same event; new impact information.",
            )
            session.add(candidate)
            session.flush()
            candidate_id = candidate.id
            session.commit()

        # A fresh session reads what was actually stored.
        with Session(engine) as session:
            candidate = session.get(Candidate, candidate_id)
            result = candidate_dict(candidate)

            assert result["relationshipType"] == "possible_update"
            assert result["relatedIncidentId"] == incident_id
            assert result["relationshipReason"] == (
                "Same event; new impact information."
            )
            assert result["status"] == "pending"
            assert result["incidentId"] is None
    finally:
        engine.dispose()

def test_related_candidates_cannot_be_published_as_new_incidents(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from mcad.api import create_app
    from mcad.config import Settings
    from mcad.models import Delivery, OutboxEvent, Subscriber

    engine = create_engine(
        f"sqlite:///{tmp_path / 'approval-test.sqlite3'}"
    )
    try:
        migrate(engine)

        with Session(engine) as session:
            incident = Incident(title="Existing Port X incident")
            session.add(incident)
            session.add(Subscriber(chat_id="123456", active=True))
            session.flush()
            incident_id = incident.id

            candidates = [
                Candidate(
                    title="Possible update",
                    source_url="https://example.com/update",
                    relationship_type="possible_update",
                    related_incident_id=incident_id,
                ),
                Candidate(
                    title="Possible duplicate",
                    source_url="https://example.com/duplicate",
                    relationship_type="possible_duplicate",
                ),
                Candidate(
                    title="Linked but unclassified",
                    source_url="https://example.com/linked",
                    related_incident_id=incident_id,
                ),
            ]
            session.add_all(candidates)
            session.flush()
            candidate_ids = [candidate.id for candidate in candidates]
            session.commit()

        settings = Settings(
            admin_api_token="test-admin-token",
            enable_workers=False,
        )
        app = create_app(settings=settings, engine=engine)

        with TestClient(app) as client:
            for candidate_id in candidate_ids:
                response = client.post(
                    f"/api/admin/candidates/{candidate_id}/approve",
                    headers={
                        "Authorization": "Bearer test-admin-token"
                    },
                    json={"title": "Reviewed report"},
                )
                assert response.status_code == 409, response.text

        with Session(engine) as session:
            for candidate_id in candidate_ids:
                candidate = session.get(Candidate, candidate_id)
                assert candidate.status == "pending"
                assert candidate.incident_id is None

            assert session.scalar(
                select(func.count()).select_from(Incident)
            ) == 1
            assert session.get(Incident, incident_id).title == (
                "Existing Port X incident"
            )
            assert session.scalar(
                select(func.count()).select_from(OutboxEvent)
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(Delivery)
            ) == 0
    finally:
        engine.dispose()

def test_reviewer_can_classify_a_candidate_as_a_separate_incident(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from mcad.api import create_app
    from mcad.config import Settings
    from mcad.models import (
        CandidateDelivery, CandidateNotification, Delivery, OutboxEvent
    )

    engine = create_engine(
        f"sqlite:///{tmp_path / 'relationship-review.sqlite3'}"
    )
    try:
        migrate(engine)

        with Session(engine) as session:
            incident = Incident(title="Existing incident")
            session.add(incident)
            session.flush()
            incident_id = incident.id

            candidate = Candidate(
                title="A separate event",
                source_url="https://example.com/separate-event",
                relationship_type="possible_update",
                related_incident_id=incident_id,
                relationship_reason="Similar title and year.",
                verification_status="needs_review",
            )
            session.add(candidate)
            session.flush()
            candidate_id = candidate.id
            session.commit()

        app = create_app(
            settings=Settings(
                admin_api_token="test-admin-token",
                enable_workers=False,
            ),
            engine=engine,
        )
        path = f"/api/admin/candidates/{candidate_id}/relationship"
        headers = {"Authorization": "Bearer test-admin-token"}

        with TestClient(app) as client:
            assert client.post(path, json={
                "decision": "new_incident", "reason": "Different event."
            }).status_code == 401

            assert client.post(path, headers=headers, json={
                "decision": "new_incident", "reason": " "
            }).status_code == 422

            response = client.post(path, headers=headers, json={
                "decision": "new_incident",
                "reason": "Different vessel and event date.",
            })
            assert response.status_code == 200, response.text
            assert response.json()["relationshipType"] == "new_incident"
            assert response.json()["relatedIncidentId"] is None

        with Session(engine) as session:
            candidate = session.get(Candidate, candidate_id)
            assert candidate.relationship_type == "new_incident"
            assert candidate.related_incident_id is None
            assert candidate.status == "pending"
            assert candidate.incident_id is None
            assert candidate.verification_status == "needs_review"

            history = candidate.evidence["relationshipReviews"]
            assert len(history) == 1
            assert history[0]["previousType"] == "possible_update"
            assert history[0]["previousRelatedIncidentId"] == incident_id
            assert history[0]["previousReason"] == "Similar title and year."
            assert history[0]["reason"] == "Different vessel and event date."

            assert session.scalar(
                select(func.count()).select_from(Incident)
            ) == 1
            for model in (
                CandidateNotification, CandidateDelivery,
                OutboxEvent, Delivery,
            ):
                assert session.scalar(
                    select(func.count()).select_from(model)
                ) == 0
    finally:
        engine.dispose()