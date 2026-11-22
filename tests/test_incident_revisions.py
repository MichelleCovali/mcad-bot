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

def test_merge_sources_preserves_existing_evidence():
    from types import SimpleNamespace
    from mcad.service import merge_incident_sources

    incident = SimpleNamespace(
        source_url="https://example.com/main",
        reference_short="Original main reference",
        sources=[
            SimpleNamespace(
                url="https://example.com/earlier",
                short_name="Earlier source",
                raw_text="Original evidence excerpt.",
            )
        ],
    )

    result = merge_incident_sources(incident, [
        {
            "url": "https://example.com/earlier",
            "shortName": "Changed name",
            "rawText": "Different excerpt.",
        },
        {
            "url": "https://example.com/new",
            "shortName": "New source",
            "rawText": "New supporting evidence.",
        },
        {
            "url": "https://example.com/new",
            "shortName": "Repeated source",
            "rawText": "Repeated evidence.",
        },
    ])

    by_url = {source["url"]: source for source in result}

    assert len(result) == 3
    assert by_url["https://example.com/earlier"] == {
        "url": "https://example.com/earlier",
        "short_name": "Earlier source",
        "raw_text": "Original evidence excerpt.",
    }
    assert by_url["https://example.com/main"]["short_name"] == (
        "Original main reference"
    )
    assert by_url["https://example.com/new"]["raw_text"] == (
        "New supporting evidence."
    )

    # Preparing the combined list must not change the original.
    assert len(incident.sources) == 1
    assert incident.sources[0].raw_text == "Original evidence excerpt."