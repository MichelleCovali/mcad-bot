"""Opt-in checks against a disposable, loopback PostgreSQL test database only."""
from datetime import timedelta
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from mcad.api import create_app
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import MIGRATIONS, migrate
from mcad.models import AIConfig, Candidate, Delivery, Incident, OutboxEvent, SchemaVersion, utcnow
from mcad.service import upsert_incident
from mcad.workers import SendResult, deliver_one, seed_subscriber


POSTGRES_URL = os.environ.get("MCAD_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="Set MCAD_TEST_POSTGRES_URL to a disposable loopback PostgreSQL test database")


@pytest.fixture
def postgres_database():
    url = make_url(POSTGRES_URL)
    if url.get_backend_name() != "postgresql" or url.host not in {"localhost", "127.0.0.1", "::1"} or not (url.database or "").startswith("mcad_ci"):
        pytest.fail("PostgreSQL tests require a loopback database whose name starts with mcad_ci; remote or production databases are refused.")
    schema = "mcad_ci_" + uuid4().hex
    admin_engine = make_engine(POSTGRES_URL)
    with admin_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = make_engine(POSTGRES_URL)

    @event.listens_for(engine, "connect")
    def isolate_schema(dbapi_connection, _record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        dbapi_connection.commit()

    try:
        yield engine, get_session_factory(engine)
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin_engine.dispose()


def count(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_existing_v1_database_survives_complete_upgrade_and_repeat(postgres_database):
    from mcad.migrations.v0001_initial import upgrade
    from mcad.models import CandidateDelivery, CandidateNotification

    engine, factory = postgres_database
    with engine.begin() as connection:
        SchemaVersion.__table__.create(connection)
        upgrade(connection)
        connection.execute(SchemaVersion.__table__.insert().values(version=1, applied_at=utcnow()))
        connection.execute(text("INSERT INTO candidates (id, source_url, title, summary, matched_keywords, status, baseline, first_seen_at) VALUES ('existing-ci-candidate', 'https://example.invalid/pre-upgrade', 'Existing fictional report', 'Keep the original summary', '[]', 'pending', false, :now)"), {"now": utcnow()})
    with factory.begin() as session:
        incident, _ = upsert_incident(session, {
            "title": "Existing historical incident", "year": 2020,
            "referenceNumber": "20200001", "rawFields": {"Original source": "Keep this value"},
            "sourceUrl": "https://example.invalid/historical",
        }, external_id="ci:historical", notify=False)
        incident_id = incident.id
    migrate(engine)
    migrate(engine)
    expected_versions = {version for version, _upgrade in MIGRATIONS}
    assert max(expected_versions) >= 4
    with factory() as session:
        assert set(session.scalars(select(SchemaVersion.version))) == expected_versions
        candidate = session.get(Candidate, "existing-ci-candidate")
        assert candidate.summary == "Keep the original summary"
        assert candidate.origin == "rss"
        assert candidate.evidence == {}
        assert candidate.status == "pending"
        incident = session.get(Incident, incident_id)
        assert incident.reference_number == "20200001"
        assert incident.raw_fields == {"Original source": "Keep this value"}
        assert incident.notification_suppressed is True
        assert session.get(AIConfig, 1).enabled is False
    for model in (OutboxEvent, Delivery, CandidateNotification, CandidateDelivery):
        assert count(factory, model) == 0


def test_candidate_and_alert_commit_together_and_retry_creation_is_idempotent(postgres_database):
    from mcad.candidate_alerts import create_candidate_notification
    from mcad.models import CandidateDelivery, CandidateNotification

    engine, factory = postgres_database
    migrate(engine)
    with factory.begin() as session:
        seed_subscriber(session, "123456")
    with pytest.raises(RuntimeError, match="rollback fixture"):
        with factory.begin() as session:
            candidate = Candidate(title="Rolled back AI candidate", source_url="https://example.invalid/rollback", origin="ai", verification_status="supported")
            session.add(candidate)
            session.flush()
            create_candidate_notification(session, candidate)
            raise RuntimeError("rollback fixture")
    assert count(factory, Candidate) == 0
    assert count(factory, CandidateNotification) == 0
    assert count(factory, CandidateDelivery) == 0
    with factory.begin() as session:
        candidate = Candidate(title="New fictional AI candidate", source_url="https://example.invalid/new-ai", origin="ai", verification_status="supported")
        session.add(candidate)
        session.flush()
        candidate_id = candidate.id
        create_candidate_notification(session, candidate)
    with factory.begin() as session:
        create_candidate_notification(session, session.get(Candidate, candidate_id))
    assert count(factory, CandidateNotification) == 1
    assert count(factory, CandidateDelivery) == 1
    assert count(factory, Incident) == 0
    assert count(factory, OutboxEvent) == 0


def test_delivered_candidate_alert_does_not_repeat_on_human_publication(postgres_database):
    from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
    from mcad.models import CandidateDelivery, CandidateNotification

    engine, factory = postgres_database
    migrate(engine)
    with factory.begin() as session:
        seed_subscriber(session, "123456")
        seed_subscriber(session, "123457")
        candidate = Candidate(title="Human-review workflow fixture", source_url="https://example.invalid/review-flow", origin="ai", verification_status="supported")
        session.add(candidate)
        session.flush()
        candidate_id = candidate.id
        create_candidate_notification(session, candidate)

    class FakeTelegram:
        def __init__(self):
            self.calls = []

        def send(self, chat_id, message):
            self.calls.append((chat_id, message))
            return SendResult(True, message_id=f"fixture-{len(self.calls)}")

    client = FakeTelegram()
    assert deliver_candidate_one(factory, client, now=utcnow() + timedelta(seconds=1))
    alerted_chat = client.calls[0][0]
    settings = Settings(admin_api_token="ci-admin", enable_workers=False)
    with TestClient(create_app(settings, engine)) as api:
        response = api.post(f"/api/admin/candidates/{candidate_id}/approve", json={"title": "Human-verified fictional incident", "year": 2025}, headers={"Authorization": "Bearer ci-admin"})
        assert response.status_code == 200
    with factory() as session:
        queued_chats = list(session.scalars(select(Delivery.chat_id)))
        assert len(queued_chats) == 1
        assert alerted_chat not in queued_chats
        assert session.get(Candidate, candidate_id).status == "approved"
    # Approval cancels unsent review alerts; only the other recipient gets a publication alert.
    deliver_candidate_one(factory, client, now=utcnow() + timedelta(seconds=2))
    assert len(client.calls) == 1
    assert deliver_one(factory, client, now=utcnow() + timedelta(seconds=3))
    assert {chat for chat, _message in client.calls} == {"123456", "123457"}
    assert count(factory, CandidateNotification) == 1
    assert count(factory, Incident) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(CandidateDelivery).where(CandidateDelivery.status == "sent")) == 1
