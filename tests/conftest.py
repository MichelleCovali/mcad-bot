"""Test runs cannot inherit production credentials or call external HTTP APIs."""
import os

import pytest
import requests


# This runs before test collection imports mcad.api's application object. Individual
# tests use explicit Settings and injected clients to exercise enabled features.
for name, value in {
    "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    "ADMIN_API_TOKEN": "test-only-admin",
    "ENABLE_BACKGROUND_WORKERS": "false",
    "ENABLE_FEED_POLLING": "false",
    "ENABLE_TELEGRAM_POLLING": "false",
    "ENABLE_AI_SEARCH": "false",
    "OPENAI_API_KEY": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "TELEGRAM_MESSAGE_FORMAT": "link",
}.items():
    os.environ[name] = value


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    def blocked_request(*_args, **_kwargs):
        raise AssertionError("External HTTP is blocked in automated tests; inject a fake client.")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked_request)
