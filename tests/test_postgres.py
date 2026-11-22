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
        response = api.post(f"/api/admin/candidates/{candidate_id}/approve", json={"title": "Human-verified fictional incident", "year": 2025,
            "referenceUrls": ["https://independent.example/incident"], "independentSourcesConfirmed": True}, headers={"Authorization": "Bearer ci-admin"})
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


def test_invitation_redemption_and_last_admin_invariant_are_atomic(postgres_database):
    from concurrent.futures import ThreadPoolExecutor

    from mcad.auth import AccountConflict, InvalidInvite, accept_invite, create_admin, issue_invite, update_member
    from mcad.auth_models import AdminUser

    engine, factory = postgres_database
    migrate(engine)
    password = "disposable-account-password"
    create_admin(factory, "existing@example.test", password)
    _, stale_token = issue_invite(factory, "new@example.test", "admin")
    _, token = issue_invite(factory, "new@example.test", "admin")
    with pytest.raises(InvalidInvite):
        accept_invite(factory, stale_token, password, "192.0.2.10")

    def redeem(address):
        try:
            accept_invite(factory, token, password, address)
        except InvalidInvite:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(redeem, ["192.0.2.11", "192.0.2.12"])) == [False, True]
    with factory() as session:
        member_ids = session.scalars(select(AdminUser.id)).all()
        assert len(member_ids) == 2

    def deactivate(member_id):
        try:
            update_member(factory, member_id, {"active": False})
        except AccountConflict:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(deactivate, member_ids)) == [False, True]
    with factory() as session:
        assert len(session.scalars(select(AdminUser).where(AdminUser.active.is_(True))).all()) == 1
@pytest.mark.parametrize("same_report", [False, True])
def test_competing_reviewed_updates_leave_one_revision(postgres_database, same_report):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from mcad.models import IncidentRevision
    from mcad.service import ReviewConflict, apply_reviewed_incident_update

    engine, factory = postgres_database
    migrate(engine)
    with factory.begin() as session:
        incident, _ = upsert_incident(session, {
            "title": "Fictional incident", "sourceUrl": "https://example.invalid/original",
        })
        reports = [Candidate(title="Fictional report", source_url=f"https://example.invalid/{index}")
                   for index in range(1 if same_report else 2)]
        session.add_all(reports)
        session.flush()
        incident_id, version = incident.id, incident.updated_at.isoformat()
        report_ids = [report.id for report in reports]

    start = Barrier(2, timeout=5)

    def apply(index):
        try:
            with factory.begin() as session:
                start.wait()
                apply_reviewed_incident_update(
                    session, report_ids[0 if same_report else index], incident_id,
                    {"summary": f"Correction {index}", "sources": [
                        {"url": "https://example.invalid/original"}, {"url": "https://example.net/corroboration"},
                    ]},
                    "Reviewed correction", expected_updated_at=version, independent_sources_confirmed=True,
                )
        except ReviewConflict as conflict:
            return conflict.code
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(apply, range(2)))

    assert sorted(results) == sorted(["saved", "report_resolved" if same_report else "stale_target"])
    assert count(factory, IncidentRevision) == 1
    assert count(factory, OutboxEvent) == 0
    with factory() as session:
        states = sorted(session.get(Candidate, report_id).status for report_id in report_ids)
        assert states == (["approved"] if same_report else ["approved", "pending"])


@pytest.mark.parametrize("history", ["main", "feature"])
def test_postgres_upgrade_from_existing_branch_history(postgres_database, history):
    from test_integration_migrations import check_history_upgrade

    engine, _ = postgres_database
    check_history_upgrade(engine, history)
