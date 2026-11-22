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

def test_reviewed_update_preserves_sources_and_records_history():
    from sqlalchemy import func, select

    from mcad.models import IncidentSource, OutboxEvent
    from mcad.service import apply_reviewed_incident_update

    engine = create_engine("sqlite:///:memory:")
    try:
        migrations.migrate(engine)

        with Session(engine) as session:
            with session.begin():
                incident = Incident(
                    title="Fictional Port X incident",
                    summary="Original reviewed information.",
                    victim_country="Netherlands",
                    source_url="https://example.com/original",
                    status="published",
                )
                incident.sources.append(IncidentSource(
                    url="https://example.com/original",
                    short_name="Original source",
                    raw_text="Earlier evidence.",
                ))
                session.add(incident)
                session.flush()

                candidate = Candidate(
                    title="Fictional Port X update",
                    source_url="https://example.com/update",
                    relationship_type="possible_update",
                    related_incident_id=incident.id,
                    evidence={"uncertainties": ["Impact needs review."]},
                )
                session.add(candidate)
                session.flush()

                incident_id = incident.id
                candidate_id = candidate.id
                version = incident.updated_at.isoformat()

        with Session(engine) as session:
            with session.begin():
                apply_reviewed_incident_update(
                    session,
                    candidate_id,
                    incident_id,
                    {
                        "summary": "Reviewed three-day shutdown.",
                        "sources": [{
                            "url": "https://example.com/update",
                            "shortName": "New source",
                            "rawText": "New reviewed evidence.",
                        }],
                    },
                    "New reporting confirms the duration.",
                    expected_updated_at=version,
                )

        with Session(engine) as session:
            incident = session.get(Incident, incident_id)
            candidate = session.get(Candidate, candidate_id)
            revision = session.scalar(select(IncidentRevision))

            assert incident.summary == "Reviewed three-day shutdown."
            assert incident.victim_country == "Netherlands"
            assert incident.source_url == "https://example.com/original"
            assert {source.url for source in incident.sources} == {
                "https://example.com/original",
                "https://example.com/update",
            }
            assert next(
                source.raw_text for source in incident.sources
                if source.url == "https://example.com/original"
            ) == "Earlier evidence."

            assert revision.before_snapshot["summary"] == (
                "Original reviewed information."
            )
            assert revision.after_snapshot["summary"] == (
                "Reviewed three-day shutdown."
            )
            assert revision.reason == "New reporting confirms the duration."
            assert revision.evidence == {
                "uncertainties": ["Impact needs review."]
            }
            assert candidate.status == "approved"
            assert candidate.incident_id == incident_id
            assert candidate.relationship_type == "update"

            assert session.scalar(
                select(func.count()).select_from(Incident)
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(OutboxEvent)
            ) == 0
    finally:
        engine.dispose()

def test_stale_updates_and_failed_transactions_leave_records_unchanged():
    import pytest
    from sqlalchemy import func, select

    from mcad.service import apply_reviewed_incident_update

    engine = create_engine("sqlite:///:memory:")
    try:
        migrations.migrate(engine)

        with Session(engine) as session:
            with session.begin():
                incident = Incident(
                    title="Fictional incident",
                    summary="Original information.",
                    status="published",
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
                version = incident.updated_at.isoformat()

        # An outdated version must not be applied.
        with pytest.raises(ValueError, match="changed since"):
            with Session(engine) as session:
                with session.begin():
                    apply_reviewed_incident_update(
                        session,
                        candidate_id,
                        incident_id,
                        {"summary": "Must not be saved."},
                        "Reviewed explanation.",
                        expected_updated_at="outdated-version",
                    )

        # Simulate failure after the update function has finished.
        with pytest.raises(RuntimeError, match="Simulated failure"):
            with Session(engine) as session:
                with session.begin():
                    apply_reviewed_incident_update(
                        session,
                        candidate_id,
                        incident_id,
                        {"summary": "Must be rolled back."},
                        "Reviewed explanation.",
                        expected_updated_at=version,
                    )
                    raise RuntimeError("Simulated failure")

        with Session(engine) as session:
            incident = session.get(Incident, incident_id)
            candidate = session.get(Candidate, candidate_id)

            assert incident.summary == "Original information."
            assert incident.updated_at.isoformat() == version
            assert candidate.status == "pending"
            assert candidate.incident_id is None
            assert candidate.relationship_type == "possible_update"
            assert session.scalar(
                select(func.count()).select_from(IncidentRevision)
            ) == 0
    finally:
        engine.dispose()

def test_update_api_uses_review_version_and_prevents_repeat_application(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select

    from mcad.api import create_app
    from mcad.config import Settings
    from mcad.models import OutboxEvent

    engine = create_engine(
        f"sqlite:///{tmp_path / 'update-api.sqlite3'}"
    )
    try:
        migrations.migrate(engine)

        with Session(engine) as session:
            with session.begin():
                incident = Incident(
                    title="Fictional Port X incident",
                    summary="Original information.",
                    status="published",
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

        app = create_app(
            settings=Settings(
                admin_api_token="test-admin-token",
                enable_workers=False,
            ),
            engine=engine,
        )
        headers = {"Authorization": "Bearer test-admin-token"}
        path = f"/api/admin/candidates/{candidate_id}/apply-update"

        with TestClient(app) as client:
            opened = client.get(f"/api/incidents/{incident_id}")
            assert opened.status_code == 200
            version = opened.json()["reviewVersion"]

            request = {
                "incidentId": incident_id,
                "reviewVersion": version,
                "reason": "Reviewed source confirms the impact.",
                "changes": {"summary": "Reviewed new information."},
            }

            assert client.post(path, json=request).status_code == 401

            stale = {**request, "reviewVersion": "outdated-version"}
            response = client.post(path, headers=headers, json=stale)
            assert response.status_code == 422
            assert "changed since" in response.json()["detail"]

            response = client.post(path, headers=headers, json=request)
            assert response.status_code == 200, response.text
            assert response.json()["id"] == incident_id
            assert response.json()["summary"] == "Reviewed new information."

            # The candidate has already been approved.
            repeated = client.post(path, headers=headers, json=request)
            assert repeated.status_code == 422

        with Session(engine) as session:
            assert session.scalar(
                select(func.count()).select_from(IncidentRevision)
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(Incident)
            ) == 1
            assert session.get(Candidate, candidate_id).status == "approved"
            assert session.scalar(
                select(func.count()).select_from(OutboxEvent)
            ) == 0
    finally:
        engine.dispose()