"""Notification compatibility uses fake clients, never Telegram HTTP."""
from datetime import timedelta
import os
import unittest
from unittest.mock import patch

from sqlalchemy import select

from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Delivery, utcnow
from mcad.service import upsert_incident
from mcad.workers import SendResult, WorkerController, deliver_one, notification_text, seed_subscriber


class NotificationFormat(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite+pysqlite:///:memory:")
        migrate(self.engine)
        self.factory = get_session_factory(self.engine)
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("Telegram HTTP is forbidden in tests"))
        self.request = self.network.start()
        with self.factory.begin() as session:
            seed_subscriber(session, "123456")

    def tearDown(self):
        self.request.assert_not_called()
        self.network.stop()
        self.engine.dispose()

    def publish(self, **values):
        with self.factory.begin() as session:
            incident, _ = upsert_incident(session, {"title": "Fictional port incident", "year": utcnow().year, **values}, notify=True)
            return incident.id

    def capture_delivery(self, base_url="", message_format="link"):
        class FakeClient:
            def send(self, chat_id, text):
                self.chat_id, self.text = chat_id, text
                return SendResult(True, message_id="fixture-message")
        client = FakeClient()
        self.assertTrue(deliver_one(self.factory, client, base_url, now=utcnow() + timedelta(seconds=1), message_format=message_format))
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Delivery)).status, "sent")
        return client.text

    def test_default_incident_alert_is_only_article_url_even_when_map_is_configured(self):
        self.assertEqual(Settings().telegram_message_format, "link")
        self.publish(sourceUrl="https://example.invalid/article")
        text = self.capture_delivery("https://example.invalid/map")
        self.assertEqual(text, "https://example.invalid/article")

    def test_rich_alert_remains_available_as_explicit_opt_in(self):
        incident_id = self.publish(sourceUrl="https://example.invalid/article")
        text = self.capture_delivery("https://example.invalid/map", "rich")
        self.assertEqual(text, notification_text({"id": incident_id, "title": "Fictional port incident", "year": utcnow().year, "sourceUrl": "https://example.invalid/article"}, "https://example.invalid/map"))

    def test_missing_article_url_falls_back_to_incident_map_link(self):
        incident_id = self.publish()
        text = self.capture_delivery("https://example.invalid/map")
        self.assertEqual(text, f"https://example.invalid/map/?incident={incident_id}")

    def test_no_links_falls_back_to_nonempty_incident_text(self):
        self.publish()
        text = self.capture_delivery()
        self.assertIn("Fictional port incident", text)
        self.assertIn("New verified MCAD incident", text)

    def test_invalid_format_is_rejected_before_claiming_a_delivery(self):
        self.publish(sourceUrl="https://example.invalid/article")
        with self.assertRaises(ValueError):
            deliver_one(self.factory, object(), message_format="invalid")
        with self.factory() as session:
            self.assertEqual(session.scalar(select(Delivery)).status, "pending")

    def test_environment_format_is_validated_without_echoing_values(self):
        with patch.dict(os.environ, {"TELEGRAM_MESSAGE_FORMAT": " RICH "}), patch("dotenv.load_dotenv"):
            self.assertEqual(Settings.from_env().telegram_message_format, "rich")
        with patch.dict(os.environ, {"TELEGRAM_MESSAGE_FORMAT": "secret-like-invalid-value"}), patch("dotenv.load_dotenv"):
            with self.assertRaisesRegex(ValueError, "^TELEGRAM_MESSAGE_FORMAT must be link or rich[.]$"):
                Settings.from_env()

    def test_worker_passes_configured_format_to_delivery(self):
        controller = WorkerController(self.factory, Settings(telegram_message_format="rich"))
        with patch("mcad.workers.claim_lease", return_value=True), patch("mcad.workers.deliver_one") as deliver:
            controller.stopped.wait = lambda _seconds: controller.stopped.set()
            controller._delivery_loop()
        self.assertEqual(deliver.call_args.kwargs["message_format"], "rich")


if __name__ == "__main__":
    unittest.main()
