"""Exercise research → Telegram → human publication without external services."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mcad.ai_monitor import run_monitor_once, update_monitor_settings
from mcad.api import create_app
from mcad.candidate_alerts import deliver_candidate_one
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import AIRun, Candidate, CandidateDelivery, CandidateNotification, Delivery, Incident, OutboxEvent, utcnow
from mcad.workers import SendResult, deliver_one, seed_subscriber
from test_ai_monitor import FakeClient, discovery, response, verification


class ApplicationFlow(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine("sqlite+pysqlite:///:memory:")
        migrate(self.engine)
        self.factory = get_session_factory(self.engine)
        self.settings = Settings(admin_api_token="fixture-admin", enable_workers=False,
            enable_ai_search=True, openai_api_key="fixture-only-key",
            telegram_bot_token="fixture-only-token", telegram_chat_id="123456")
        self.client = TestClient(create_app(self.settings, self.engine))
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer fixture-admin"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.engine.dispose()

    def count(self, model):
        with self.factory() as session:
            return session.scalar(select(func.count()).select_from(model))

    def test_new_researched_candidate_notifies_once_and_approval_does_not_repeat(self):
        with self.factory.begin() as session:
            for chat in ("123456", "654321"):
                seed_subscriber(session, chat)
        update_monitor_settings(self.factory, self.settings, {"enabled": True})
        provider = FakeClient(response(discovery()), response(verification(), opened=True))
        with patch("mcad.ai_monitor.OpenAIResponsesClient", return_value=provider):
            run = self.client.post("/api/admin/ai-monitor/runs", headers=self.auth)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["candidatesCreated"], 1)
        self.assertEqual(self.count(CandidateNotification), 1)
        self.assertEqual(self.count(CandidateDelivery), 2)
        self.assertEqual(self.count(Incident), 0)
        self.assertEqual(self.count(OutboxEvent), 0)
        candidate = self.client.get("/api/admin/candidates", headers=self.auth).json()["items"][0]
        linked = self.client.get(f"/api/admin/candidates/{candidate['id']}", headers=self.auth)
        self.assertEqual(linked.json()["id"], candidate["id"])

        class Sender:
            calls = []
            def send(self, chat_id, text):
                self.calls.append((chat_id, text))
                return SendResult(True, message_id=str(len(self.calls)))
        sender = Sender()
        self.assertTrue(deliver_candidate_one(self.factory, sender))
        approved = self.client.post(f"/api/admin/candidates/{candidate['id']}/approve",
            headers=self.auth, json={"title": "Human-reviewed fictional port incident", "year": utcnow().year})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.count(Delivery), 1)
        self.assertTrue(deliver_candidate_one(self.factory, sender))  # Remaining research alert cancels.
        self.assertTrue(deliver_one(self.factory, sender))
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(len({chat for chat, _text in sender.calls}), 2)
        self.assertTrue(all(text.startswith("https://") for _chat, text in sender.calls))
        self.assertFalse(deliver_candidate_one(self.factory, sender))
        self.assertFalse(deliver_one(self.factory, sender))
        self.assertEqual(self.client.post(f"/api/admin/candidates/{candidate['id']}/approve",
            headers=self.auth, json={"title": "Same reviewed report"}).json()["id"], approved.json()["id"])
        repeated = run_monitor_once(self.factory, self.settings,
            client=FakeClient(response(discovery()), response(verification(), opened=True)))
        self.assertEqual(repeated["candidatesCreated"], 0)
        self.assertEqual(self.count(CandidateNotification), 1)

    def test_telegram_test_is_authenticated_explicit_and_does_not_run_research(self):
        path = "/api/admin/telegram/test"
        self.assertEqual(self.client.post(path).status_code, 401)
        self.assertEqual(self.client.get("/api/admin/telegram").status_code, 401)
        with patch("mcad.workers.TelegramClient.send", return_value=SendResult(True, message_id="fixture-test")) as send, patch("mcad.ai_monitor.OpenAIResponsesClient") as provider:
            status = self.client.get("/api/admin/telegram", headers=self.auth)
            self.assertEqual(status.status_code, 200)
            send.assert_not_called()
            result = self.client.post(path, headers=self.auth)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(send.call_args.args[0], "123456")
            self.assertIn("test notification", send.call_args.args[1])
            self.assertEqual(self.client.post(path, headers=self.auth).status_code, 429)
            self.assertEqual(send.call_count, 1)
            provider.assert_not_called()
        for model in (AIRun, Incident, Candidate, OutboxEvent, CandidateNotification):
            self.assertEqual(self.count(model), 0)

    def test_unconfigured_and_failed_telegram_test_return_bounded_errors(self):
        with TestClient(create_app(replace(self.settings, telegram_chat_id=""), self.engine)) as client:
            self.assertEqual(client.post("/api/admin/telegram/test", headers=self.auth).status_code, 409)
        with patch("mcad.workers.TelegramClient.send", return_value=SendResult(False, error_code=403)):
            response = self.client.post("/api/admin/telegram/test", headers=self.auth)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("fixture-only-token", response.text)
        self.assertEqual(self.client.get("/api/admin/candidates/missing", headers=self.auth).status_code, 404)


if __name__ == "__main__":
    unittest.main()
