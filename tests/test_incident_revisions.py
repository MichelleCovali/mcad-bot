from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import mcad.migrations as migrations
from mcad.models import Candidate, Incident, IncidentRevision


def test_revision_migration_preserves_existing_records(monkeypatch):
    engine = create_engine("sqlite:///:memory:")

    try:
        all_migrations = migrations.MIGRATIONS

        # Start with the previous database structure.
        with monkeypatch.context() as patch:
            patch.setattr(
                migrations,
                "MIGRATIONS",
                [
                    item for item in all_migrations
                    if item[0] <= 5
                ],
            )
            migrations.migrate(engine)

        with Session(engine) as session:
            incident = Incident(
                title="Fictional existing incident",
                summary="Original information.",
            )
            session.add(incident)
            session.flush()

            candidate = Candidate(
                title="Fictional update",
                source_url="https://example.com/update",
                relationship_type="possible_update",
                related_incident_id=incident.id,
            )
            session.add(candidate)
            session.flush()

            incident_id = incident.id
            candidate_id = candidate.id
            session.commit()

        # Upgrade, then repeat to check migration tracking.
        migrations.migrate(engine)
        migrations.migrate(engine)

        assert "incident_revisions" in inspect(engine).get_table_names()

        with Session(engine) as session:
            incident = session.get(Incident, incident_id)
            candidate = session.get(Candidate, candidate_id)

            assert incident.summary == "Original information."
            assert candidate.related_incident_id == incident_id
            assert candidate.status == "pending"

            revision = IncidentRevision(
                incident_id=incident_id,
                candidate_id=candidate_id,
                reason="New source confirms operational impact.",
                before_snapshot={"summary": "Original information."},
                after_snapshot={"summary": "Reviewed new information."},
                evidence={"sources": ["https://example.com/update"]},
            )
            session.add(revision)
            session.flush()
            revision_id = revision.id
            session.commit()

        with Session(engine) as session:
            revision = session.get(IncidentRevision, revision_id)
            assert revision.before_snapshot == {
                "summary": "Original information."
            }
            assert revision.after_snapshot == {
                "summary": "Reviewed new information."
            }
            assert revision.candidate_id == candidate_id

            # Saving history alone must not update the incident.
            assert session.get(Incident, incident_id).summary == (
                "Original information."
            )
    finally:
        engine.dispose()