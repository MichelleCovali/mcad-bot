"""Candidate alerts use isolated databases and fake clients; no external HTTP."""
from datetime import timedelta
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import func, select

from mcad.candidate_alerts import (
    candidate_notification_text, claim_candidate_delivery,
    create_candidate_notification, deliver_candidate_one,
)
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, CandidateDelivery, CandidateNotification, Delivery, Incident, OutboxEvent, Subscriber, utcnow
from mcad.workers import SendResult, WorkerController, seed_subscriber, set_subscription


class FakeClient:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def send(self, chat_id, text):
        self.calls.append((chat_id, text))
        return self.results.pop(0) if self.results else SendResult(True, message_id="fixture-message")


class CandidateAlerts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "alerts.sqlite3"
        self.engine = make_engine("sqlite:///" + str(self.path))
        migrate(self.engine)
        self.factory = get_session_factory(self.engine)
        self.http = patch("requests.sessions.Session.request", side_effect=AssertionError("External HTTP is forbidden"))
        self.request = self.http.start()

    def tearDown(self):
        self.request.assert_not_called()
        self.http.stop()
        self.engine.dispose()
        self.directory.cleanup()

    def subscribe(self, chat_id="123456", **values):
        with self.factory.begin() as session:
            subscriber = seed_subscriber(session, chat_id)
            row = session.get(Subscriber, chat_id)
            for key, value in values.items():
                setattr(row, key, value)

    def create(self, **values):
        with self.factory.begin() as session:
            candidate = Candidate(
                title="Fictional source-checked port report", summary="Test fixture, not a real incident.",
                source_url=values.pop("source_url", "https://example.invalid/new-report"),
                origin=values.pop("origin", "ai"),
                verification_status=values.pop("verification_status", "supported"),
                status=values.pop("status", "pending"), **values,
            )
            session.add(candidate)
            create_candidate_notification(session, candidate)
            session.flush()
            return candidate.id

    def count(self, model):
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model))

    def delivery(self):
        with self.factory() as session:
            return session.scalar(select(CandidateDelivery))

    def test_candidate_and_notification_rollback_atomically(self):
        self.subscribe()
        with self.assertRaisesRegex(RuntimeError, "rollback fixture"):
            with self.factory.begin() as session:
                candidate = Candidate(title="Rollback fixture", source_url="https://example.invalid/rollback",
                                      origin="ai", verification_status="supported", status="pending")
                session.add(candidate)
                create_candidate_notification(session, candidate)
                raise RuntimeError("rollback fixture")
        for model in (Candidate, CandidateNotification, CandidateDelivery, Incident, OutboxEvent, Delivery):
            self.assertEqual(self.count(model), 0)

    def test_one_opt_in_alert_per_new_supported_candidate_and_recipient(self):
        self.subscribe()
        self.subscribe("654321", active=False)
        identifier = self.create()
        self.subscribe("777777")
        with self.factory.begin() as session:
            event = create_candidate_notification(session, session.get(Candidate, identifier))
            self.assertIsNotNone(event)
        self.assertEqual(self.count(CandidateNotification), 1)
        self.assertEqual(self.count(CandidateDelivery), 1)
        client = FakeClient()
        self.assertTrue(deliver_candidate_one(self.factory, client))
        self.assertFalse(deliver_candidate_one(self.factory, client))
        self.assertEqual(client.calls, [("123456", "https://example.invalid/new-report")])
        self.assertEqual(self.delivery().status, "sent")
        self.assertEqual(self.count(Incident), 0)
        self.assertEqual(self.count(OutboxEvent), 0)

    def test_uncertain_conflicting_rss_baseline_and_reviewed_candidates_are_silent(self):
        self.subscribe()
        variants = [
            {"verification_status": "needs_review"}, {"verification_status": "contradicted"},
            {"origin": "rss"}, {"baseline": True}, {"status": "rejected"}, {"status": "approved"},
        ]
        for index, fields in enumerate(variants):
            self.create(source_url=f"https://example.invalid/silent-{index}", **fields)
        self.assertEqual(self.count(Candidate), len(variants))
        self.assertEqual(self.count(CandidateNotification), 0)
        self.assertEqual(self.count(CandidateDelivery), 0)

    def test_no_subscriber_or_later_subscriber_receives_no_backfill(self):
        identifier = self.create()
        self.subscribe()
        with self.factory.begin() as session:
            create_candidate_notification(session, session.get(Candidate, identifier))
        self.assertEqual(self.count(CandidateNotification), 1)
        self.assertEqual(self.count(CandidateDelivery), 0)
        self.assertFalse(deliver_candidate_one(self.factory, FakeClient()))

    def test_candidate_model_defaults_are_applied_before_eligibility_check(self):
        self.subscribe()
        with self.factory.begin() as session:
            candidate = Candidate(title="Default pending fixture", source_url="https://example.invalid/defaults",
                                  origin="ai", verification_status="supported")
            session.add(candidate)
            self.assertIsNotNone(create_candidate_notification(session, candidate))
            self.assertEqual(candidate.status, "pending")
        self.assertEqual(self.count(CandidateDelivery), 1)

    def test_unsubscribe_and_resubscribe_cancels_old_candidate_delivery(self):
        self.subscribe()
        self.create()
        with self.factory.begin() as session:
            set_subscription(session, "123456", False)
        client = FakeClient()
        self.assertTrue(deliver_candidate_one(self.factory, client))
        self.assertEqual(client.calls, [])
        self.assertEqual(self.delivery().status, "cancelled")
        self.assertEqual(self.delivery().last_error, "unsubscribed")

        self.create(source_url="https://example.invalid/before-resubscribe")
        with self.factory.begin() as session:
            set_subscription(session, "123456", True)
        self.assertFalse(deliver_candidate_one(self.factory, client))

    def test_review_or_evidence_change_before_send_cancels_candidate_alert(self):
        for index, fields in enumerate([
            {"status": "rejected"}, {"status": "approved"}, {"verification_status": "needs_review"},
        ]):
            with self.subTest(fields=fields):
                self.subscribe()
                identifier = self.create(source_url=f"https://example.invalid/reviewed-{index}")
                with self.factory.begin() as session:
                    candidate = session.get(Candidate, identifier)
                    for key, value in fields.items():
                        setattr(candidate, key, value)
                client = FakeClient()
                self.assertTrue(deliver_candidate_one(self.factory, client))
                self.assertEqual(client.calls, [])
        with self.factory() as session:
            self.assertTrue(all(row.status == "cancelled" for row in session.scalars(select(CandidateDelivery))))

    def test_parallel_claim_and_expired_lease_recovery(self):
        self.subscribe()
        self.create()
        now = utcnow() + timedelta(seconds=1)
        first = claim_candidate_delivery(self.factory, "first-worker", now)
        self.assertIsNotNone(first)
        self.assertIsNone(claim_candidate_delivery(self.factory, "second-worker", now + timedelta(seconds=1)))
        self.assertEqual(claim_candidate_delivery(self.factory, "recovery-worker", now + timedelta(seconds=91)), first)

    def test_429_persists_retry_across_restart_without_duplicate_success(self):
        self.subscribe()
        self.create()
        now = utcnow() + timedelta(seconds=1)
        client = FakeClient([SendResult(False, error_code=429, retry_after=60)])
        self.assertTrue(deliver_candidate_one(self.factory, client, now=now))
        self.assertEqual(self.delivery().next_attempt_at, now + timedelta(seconds=60))
        self.assertEqual(self.delivery().status, "retry")
        self.engine.dispose()
        self.engine = make_engine("sqlite:///" + str(self.path))
        self.factory = get_session_factory(self.engine)
        self.assertFalse(deliver_candidate_one(self.factory, client, now=now + timedelta(seconds=59)))
        self.assertTrue(deliver_candidate_one(self.factory, client, now=now + timedelta(seconds=61)))
        self.assertFalse(deliver_candidate_one(self.factory, client, now=now + timedelta(seconds=62)))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self.delivery().attempts, 2)
        self.assertEqual(self.delivery().telegram_message_id, "fixture-message")

    def test_blocked_bot_deactivates_subscriber_and_stops_future_alerts(self):
        self.subscribe()
        self.create()
        client = FakeClient([SendResult(False, error_code=403)])
        self.assertTrue(deliver_candidate_one(self.factory, client))
        self.assertEqual(self.delivery().status, "failed")
        with self.factory() as session:
            self.assertFalse(session.get(Subscriber, "123456").active)
        self.create(source_url="https://example.invalid/after-block")
        self.assertEqual(self.count(CandidateDelivery), 1)

    def test_message_format_keeps_default_source_link_and_rich_review_label(self):
        payload = {"candidateId": "fixture/id", "title": "Fictional port report",
                   "sourceUrl": "https://example.invalid/report"}
        self.assertEqual(candidate_notification_text(payload, "https://example.invalid"), payload["sourceUrl"])
        rich = candidate_notification_text(payload, "https://example.invalid", "rich")
        self.assertIn("New AI report — review required", rich)
        self.assertIn("https://example.invalid/admin?candidate=fixture%2Fid", rich)
        self.assertNotIn("verified", rich.lower())
        with self.assertRaises(ValueError):
            candidate_notification_text(payload, message_format="invalid")

    def test_worker_sends_at_most_one_queue_message_per_tick(self):
        controller = WorkerController(self.factory, Settings(telegram_bot_token="fixture-not-used"))
        with patch("mcad.workers.claim_lease", return_value=True), patch("mcad.workers.deliver_one", return_value=True), patch("mcad.workers.deliver_candidate_one") as send_candidate:
            controller.stopped.wait = lambda _seconds: controller.stopped.set()
            controller._delivery_loop()
            send_candidate.assert_not_called()
        controller.stopped.clear()
        with patch("mcad.workers.claim_lease", return_value=True), patch("mcad.workers.deliver_one", return_value=False), patch("mcad.workers.deliver_candidate_one") as send_candidate:
            controller.stopped.wait = lambda _seconds: controller.stopped.set()
            controller._delivery_loop()
            send_candidate.assert_called_once()

    def test_related_candidates_do_not_create_alerts(self):
        self.subscribe()

        with self.factory.begin() as session:
            incident = Incident(title="Existing incident")
            session.add(incident)
            session.flush()
            incident_id = incident.id

        variants = [
            {"relationship_type": "possible_update"},
            {"relationship_type": "possible_duplicate"},
            {"related_incident_id": incident_id},
        ]

        for index, fields in enumerate(variants):
            with self.subTest(fields=fields):
                self.create(
                    source_url=f"https://example.invalid/related-{index}",
                    **fields,
                )

        self.assertEqual(self.count(Candidate), 3)
        self.assertEqual(self.count(CandidateNotification), 0)
        self.assertEqual(self.count(CandidateDelivery), 0)

    def test_relationship_change_cancels_queued_alert(self):
        self.subscribe()

        with self.factory.begin() as session:
            incident = Incident(title="Existing incident")
            session.add(incident)
            session.flush()
            incident_id = incident.id

        variants = [
            {"relationship_type": "possible_update"},
            {"relationship_type": "possible_duplicate"},
            {"related_incident_id": incident_id},
        ]

        client = FakeClient()

        for index, fields in enumerate(variants):
            with self.subTest(fields=fields):
                candidate_id = self.create(
                    source_url=f"https://example.invalid/queued-{index}"
                )

                with self.factory.begin() as session:
                    candidate = session.get(Candidate, candidate_id)
                    for key, value in fields.items():
                        setattr(candidate, key, value)

                self.assertTrue(
                    deliver_candidate_one(self.factory, client)
                )

        self.assertEqual(client.calls, [])
        self.assertEqual(self.count(CandidateDelivery), 3)

        with self.factory() as session:
            deliveries = list(session.scalars(select(CandidateDelivery)))
            self.assertTrue(
                all(item.status == "cancelled" for item in deliveries)
            )
