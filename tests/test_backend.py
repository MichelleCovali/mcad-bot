"""Backend invariants. All data is synthetic; no Telegram/feed HTTP is called."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mcad.api import create_app
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, Delivery, ImportBatch, ImportRow, Incident, IncidentSource, OutboxEvent, Subscriber, WorkerState, utcnow
from mcad.service import upsert_incident
from mcad.workers import SendResult, claim_delivery, claim_lease, deliver_one, feed_poll_once, process_update, seed_subscriber, set_subscription


class BackendCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = make_engine(f"sqlite:///{Path(self.temp.name) / 'test.sqlite3'}")
        migrate(self.engine)
        self.factory = get_session_factory(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def count(self, model):
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model))

    def subscribe(self):
        with self.factory.begin() as session:
            seed_subscriber(session, "123456")

    def create(self, title="Synthetic fixture", **kwargs):
        with self.factory.begin() as session:
            incident, _ = upsert_incident(session, {"title": title, **kwargs}, notify=True)
            return incident.id


class Transactions(BackendCase):
    def test_historical_import_is_silent_and_lossless(self):
        self.subscribe()
        with self.factory.begin() as session:
            batch = ImportBatch(source_name="synthetic.xlsx", source_sha256="a" * 64, row_count=1, metadata_json={"catalog": ["one"]})
            session.add(batch)
            session.flush()
            incident, created = upsert_incident(session, {"title": "Historical fixture", "rawFields": {"uncertain date": "unknown", "formula": "=1+1"}, "latitude": None, "longitude": None}, external_id="fixture:1", notify=False, import_batch_id=batch.id, source_row=2)
            self.assertTrue(created)
            self.assertTrue(incident.notification_suppressed)
        self.assertEqual(self.count(OutboxEvent), 0)
        self.assertEqual(self.count(Delivery), 0)
        with self.factory() as session:
            row = session.scalar(select(ImportRow))
            self.assertEqual(row.raw_fields["formula"], "=1+1")

    def test_new_publication_event_is_atomic_with_incident(self):
        self.subscribe()
        with self.assertRaises(RuntimeError):
            with self.factory.begin() as session:
                upsert_incident(session, {"title": "Rolled back fixture"}, notify=True)
                raise RuntimeError("test transaction rollback")
        self.assertEqual(self.count(Incident), 0)
        self.assertEqual(self.count(OutboxEvent), 0)
        self.assertEqual(self.count(Delivery), 0)
        self.create()
        self.assertEqual(self.count(OutboxEvent), 1)
        self.assertEqual(self.count(Delivery), 1)

    def test_duplicates_and_edits_do_not_resend(self):
        self.subscribe()
        payload = {"title": "Source fixture", "sourceUrl": "https://example.invalid/report", "reference_urls": ["https://example.invalid/report"]}
        with self.factory.begin() as session:
            first, created = upsert_incident(session, payload, external_id="fixture:unique", notify=True)
            first_id = first.id
        with self.factory.begin() as session:
            second, created = upsert_incident(session, {**payload, "title": "Updated fixture"}, external_id="fixture:unique", notify=True)
            self.assertEqual(second.id, first_id)
            self.assertFalse(created)
        self.assertEqual(self.count(Incident), 1)
        self.assertEqual(self.count(IncidentSource), 1)
        self.assertEqual(self.count(OutboxEvent), 1)
        self.assertEqual(self.count(Delivery), 1)

    def test_distinct_external_records_can_share_source_url(self):
        for external in ["fixture:first", "fixture:second"]:
            with self.factory.begin() as session:
                upsert_incident(session, {"title": external, "sourceUrl": "https://example.invalid/common"}, external_id=external)
        self.assertEqual(self.count(Incident), 2)
        self.assertEqual(self.count(IncidentSource), 2)

    def test_source_url_fallback_is_idempotent(self):
        for _ in range(2):
            with self.factory.begin() as session:
                upsert_incident(session, {"title": "URL fixture", "sourceUrl": "https://EXAMPLE.invalid/report#fragment"}, notify=True)
        self.assertEqual(self.count(Incident), 1)
        self.assertEqual(self.count(OutboxEvent), 1)


class WorkerBehaviour(BackendCase):
    def test_lease_and_claim_exclude_parallel_workers(self):
        now = utcnow()
        self.assertTrue(claim_lease(self.factory, "test", "owner-a", now=now))
        self.assertFalse(claim_lease(self.factory, "test", "owner-b", now=now))
        self.assertTrue(claim_lease(self.factory, "test", "owner-b", now=now + timedelta(seconds=91)))
        self.subscribe()
        self.create()
        claimed = claim_delivery(self.factory, "owner-a", now=now + timedelta(seconds=1))
        self.assertIsNotNone(claimed)
        self.assertIsNone(claim_delivery(self.factory, "owner-b", now=now + timedelta(seconds=2)))

    def test_429_persists_retry_after_then_sends_once(self):
        self.subscribe()
        self.create(title="<b>Plain-text fixture</b>")
        now = utcnow() + timedelta(seconds=1)
        class FakeClient:
            calls = 0
            def send(self, chat_id, text):
                self.calls += 1
                self.last_text = text
                return SendResult(False, 429, 60) if self.calls == 1 else SendResult(True, message_id="42")
        client = FakeClient()
        self.assertTrue(deliver_one(self.factory, client, "https://example.invalid/map", now=now))
        with self.factory() as session:
            delivery = session.scalar(select(Delivery))
            self.assertEqual(delivery.status, "retry")
            self.assertEqual(delivery.next_attempt_at, now + timedelta(seconds=60))
        self.assertFalse(deliver_one(self.factory, client, now=now + timedelta(seconds=59)))
        self.assertTrue(deliver_one(self.factory, client, now=now + timedelta(seconds=61)))
        self.assertFalse(deliver_one(self.factory, client, now=now + timedelta(seconds=62)))
        self.assertEqual(client.calls, 2)
        self.assertIn("<b>Plain-text fixture</b>", client.last_text)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Delivery)).telegram_message_id, "42")

    def test_subscription_opt_in_replay_offset_and_unsubscribe(self):
        def message(update_id, command):
            return {"update_id": update_id, "message": {"chat": {"id": 77, "type": "private"}, "text": command}}
        process_update(self.factory, message(1, "/start"))
        self.assertEqual(self.count(Subscriber), 0)
        reply = process_update(self.factory, message(2, "/subscribe"))
        self.assertIn("Subscribed", reply[1])
        self.assertIsNone(process_update(self.factory, message(2, "/subscribe")))
        self.create()
        process_update(self.factory, message(3, "/unsubscribe"))
        with self.factory.begin() as session:
            seed_subscriber(session, "77")
        with self.factory() as session:
            self.assertFalse(session.get(Subscriber, "77").active)
            self.assertEqual(session.get(WorkerState, "telegram_offset").value["offset"], 4)
        class NeverSend:
            def send(self, *_):
                raise AssertionError("unsubscribed chat must not receive a message")
        self.assertTrue(deliver_one(self.factory, NeverSend()))
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Delivery)).status, "cancelled")

    def test_feed_is_review_queue_and_first_poll_is_baseline(self):
        articles = [{"title": "Maritime cyberattack fixture", "summary": "Synthetic", "link": "https://example.invalid/first"}]
        self.assertEqual(feed_poll_once(self.factory, articles), 1)
        self.assertEqual(feed_poll_once(self.factory, articles), 0)
        self.assertEqual(feed_poll_once(self.factory, [{**articles[0], "link": "https://example.invalid/new"}]), 1)
        with self.factory() as session:
            candidates = list(session.scalars(select(Candidate).order_by(Candidate.first_seen_at)))
            self.assertTrue(candidates[0].baseline)
            self.assertFalse(candidates[1].baseline)
        self.assertEqual(self.count(Incident), 0)
        self.assertEqual(self.count(OutboxEvent), 0)


class ApiBehaviour(BackendCase):
    def setUp(self):
        super().setUp()
        self.app = create_app(Settings(admin_api_token="test-admin", enable_workers=False), self.engine)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer test-admin"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        super().tearDown()

    def test_writes_require_auth_and_no_secret_in_health(self):
        self.assertEqual(self.client.post("/api/incidents", json={"title": "Fixture"}).status_code, 401)
        self.assertEqual(self.client.post("/api/incidents", json={"title": "Fixture"}, headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/api/admin/candidates").status_code, 401)
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertNotIn("test-admin", health.text)
        self.assertFalse(health.json()["telegram"]["configured"])

    def test_auth_precedes_json_parsing_and_authorized_bodies_are_bounded(self):
        self.assertEqual(self.client.post("/api/incidents", content=b"invalid JSON", headers={"Content-Type": "application/json"}).status_code, 401)
        self.assertEqual(self.client.post("/api/incidents", content=b"x" * (1024 * 1024 + 1), headers={**self.auth, "Content-Type": "application/json"}).status_code, 413)

    def test_validation_mapped_unmapped_details_and_edits(self):
        for bad in [{"title": "Bad", "latitude": 50}, {"title": "Bad", "latitude": 95, "longitude": 10}, {"title": "Bad", "year": 5000}, {"title": "Bad", "sourceUrl": "javascript:alert(1)"}]:
            self.assertEqual(self.client.post("/api/incidents", json=bad, headers=self.auth).status_code, 422)
        result = self.client.post("/api/incidents", json={"title": "Mapped fixture", "latitude": 52, "longitude": 4, "rawFields": {"Original": "Retained"}}, headers=self.auth)
        self.assertEqual(result.status_code, 201)
        identifier = result.json()["id"]
        self.client.post("/api/incidents", json={"title": "Unknown location", "latitude": None, "longitude": None}, headers=self.auth)
        listing = self.client.get("/api/incidents").json()
        self.assertEqual(listing["meta"]["mapped"], 1)
        self.assertEqual(listing["meta"]["unmapped"], 1)
        self.assertEqual(self.client.get(f"/api/incidents/{identifier}").json()["rawFields"]["Original"], "Retained")
        self.client.patch(f"/api/incidents/{identifier}", json={"title": "Edited fixture"}, headers=self.auth)
        self.assertEqual(self.count(OutboxEvent), 2)

    def test_candidate_approval_is_explicit_and_idempotent(self):
        self.subscribe()
        feed_poll_once(self.factory, [{"title": "Maritime fixture", "link": "https://example.invalid/candidate", "summary": "Unverified"}])
        candidate = self.client.get("/api/admin/candidates", headers=self.auth).json()["items"][0]
        url = f"/api/admin/candidates/{candidate['id']}/approve"
        self.assertEqual(self.client.post(url, json={}, headers=self.auth).status_code, 422)
        result = self.client.post(url, json={"title": "Verified fixture", "year": 2020, "incidentCountry": "Netherlands"}, headers=self.auth)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.post(url, json={"title": "Verified fixture"}, headers=self.auth).json()["id"], result.json()["id"])
        self.assertEqual(self.count(Incident), 1)
        self.assertEqual(self.count(OutboxEvent), 1)
        self.assertEqual(self.count(Delivery), 1)

    def test_import_auth_content_type_size_and_silent_contract(self):
        path = "/api/admin/import-workbook"
        content_type = {"Content-Type": "application/octet-stream"}
        self.assertEqual(self.client.post(path, content=b"fake", headers=content_type).status_code, 401)
        self.assertEqual(self.client.post(path, content=b"fake", headers=self.auth).status_code, 415)
        self.assertEqual(self.client.post(path, content=b"", headers={**self.auth, **content_type}).status_code, 400)
        self.assertEqual(self.client.post(path, content=b"not-a-workbook", headers={**self.auth, **content_type}).status_code, 422)
        self.assertEqual(self.client.post(path, content=b"x", headers={**self.auth, **content_type, "Content-Length": str(11 * 1024 * 1024)}).status_code, 413)
        summary = {"source_rows": 2, "accepted_rows": 2, "quarantined_rows": 0, "mapped_rows": 1, "unmapped_rows": 1, "created_records": 2, "existing_records": 0, "reused_batch": False, "notification_events_created": 0}
        with patch("mcad.import_workbook.import_workbook", return_value=summary) as importer:
            response = self.client.post(path, content=b"synthetic-fixture", headers={**self.auth, **content_type})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["notificationEventsCreated"], 0)
            self.assertEqual(response.json()["sourceRows"], 2)
            self.assertFalse(importer.call_args.args[0].exists())


if __name__ == "__main__":
    unittest.main()
