"""Administrator AI controls are usable while paid search remains blocked."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mcad.api import create_app
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.workers import WorkerController


class PausedMonitorAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = make_engine(f"sqlite:///{Path(self.temp.name) / 'paused.sqlite3'}")
        migrate(self.engine)
        self.settings = Settings(
            admin_api_token="fixture-admin", openai_api_key="fixture-key-never-used",
            enable_workers=False, enable_ai_search=False,
        )
        self.factory = get_session_factory(self.engine)
        self.client = TestClient(create_app(self.settings, self.engine))
        self.client.__enter__()
        self.auth = {"Authorization": "Bearer fixture-admin"}
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("No external requests allowed"))
        self.request = self.network.start()

    def tearDown(self):
        self.network.stop()
        self.client.__exit__(None, None, None)
        self.engine.dispose()
        self.temp.cleanup()

    def test_status_settings_and_run_require_administrator(self):
        for method, path, payload in [
            ("GET", "/api/admin/ai-monitor", None),
            ("PATCH", "/api/admin/ai-monitor", {"runsPerDay": 4}),
            ("POST", "/api/admin/ai-monitor/runs", {}),
        ]:
            response = self.client.request(method, path, **({"json": payload} if payload is not None else {}))
            self.assertEqual(response.status_code, 401)
        self.request.assert_not_called()

    def test_key_and_saved_schedule_cannot_start_search(self):
        for runs in (3, 4, 6):
            response = self.client.patch("/api/admin/ai-monitor", json={"runsPerDay": runs}, headers=self.auth)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["runsPerDay"], runs)
            self.assertFalse(response.json()["effectiveEnabled"])
        status = self.client.get("/api/admin/ai-monitor", headers=self.auth)
        self.assertTrue(status.json()["keyConfigured"])
        self.assertFalse(status.json()["serverEnabled"])
        self.assertFalse(status.json()["enabled"])
        self.assertNotIn("fixture-key-never-used", status.text)
        enabled = self.client.patch("/api/admin/ai-monitor", json={"enabled": True}, headers=self.auth)
        self.assertEqual(enabled.status_code, 409, enabled.text)
        run = self.client.post("/api/admin/ai-monitor/runs", json={}, headers=self.auth)
        self.assertEqual(run.status_code, 409, run.text)
        from mcad.models import AIRun, Candidate, Incident, OutboxEvent
        with self.factory() as session:
            for model in (AIRun, Candidate, Incident, OutboxEvent):
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), 0)
        self.request.assert_not_called()

    def test_invalid_settings_do_not_replace_saved_values(self):
        for payload in ({"runsPerDay": 0}, {"runsPerDay": 5}, {"enabled": "true"}, {"serverEnabled": True}):
            result = self.client.patch("/api/admin/ai-monitor", json=payload, headers=self.auth)
            self.assertEqual(result.status_code, 422, result.text)
        state = self.client.get("/api/admin/ai-monitor", headers=self.auth).json()
        self.assertEqual(state["runsPerDay"], 3)
        self.assertFalse(state["effectiveEnabled"])
        self.request.assert_not_called()


class MonitorWorkerGate(unittest.TestCase):
    def test_no_ai_thread_without_explicit_server_gate(self):
        controller = WorkerController(None, Settings(
            enable_feed_polling=False, enable_telegram_polling=False,
            openai_api_key="fixture-key-never-used",
        ))
        with patch("mcad.workers.threading.Thread") as thread:
            controller.start()
            thread.assert_not_called()

    def test_scheduled_run_uses_persisted_due_check(self):
        controller = WorkerController(None, Settings(enable_ai_search=True))
        with patch("mcad.ai_monitor.monitor_due", return_value=False) as due, patch("mcad.ai_monitor.run_monitor_once") as run:
            controller.stopped.wait = lambda _seconds: controller.stopped.set()
            controller._ai_loop()
            due.assert_called_once_with(None, controller.settings)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
