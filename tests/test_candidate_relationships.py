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