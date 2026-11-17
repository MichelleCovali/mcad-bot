"""Preview-only Telegram review test: fake clients; no live Telegram requests."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, CandidateDelivery, Incident, ProcessedUpdate, utcnow
from mcad.telegram_review import CallbackReply, parse_preview_action, preview_keyboard
from mcad.workers import SendResult, process_update, seed_subscriber, TelegramClient


def test_keyboard_uses_valid_bounded_callback_data():
    identifier = str(uuid4())
    markup = preview_keyboard(identifier)
    assert markup is not None
    buttons = [b for row in markup["inline_keyboard"] for b in row]
    assert len(buttons) == 3
    for button in buttons:
        assert len(button["callback_data"].encode()) <= 64
        assert "preview" in button["text"].lower()
        assert parse_preview_action(button["callback_data"])[1] == identifier
    assert preview_keyboard("invalid") is None
    assert parse_preview_action("mcad:p:approve:not-a-uuid") is None
    assert parse_preview_action("mcad:p:publish:" + identifier) is None


def test_telegram_http_request_includes_markup_only_when_given(monkeypatch):
    calls = []
    class Response:
        def json(self):
            return {"ok": True, "result": {"message_id": 22}}
    def fake_post(url, json, timeout):
        calls.append((url, json))
        return Response()
    monkeypatch.setattr("mcad.workers.requests.post", fake_post)
    bot = TelegramClient("fixture-token")
    assert bot.send("123", "hello").ok
    assert "reply_markup" not in calls[-1][1]
    markup = preview_keyboard(str(uuid4()))
    assert bot.send("123", "hello", reply_markup=markup).ok
    assert calls[-1][1]["reply_markup"] == markup


def test_reviewer_only_alert_and_read_only_callback(tmp_path):
    engine = make_engine("sqlite:///" + str(tmp_path / "preview.sqlite3"))
    migrate(engine)
    factory = get_session_factory(engine)
    with factory.begin() as session:
        seed_subscriber(session, "123")
        seed_subscriber(session, "999")
        candidate = Candidate(
            title="Synthetic port report", source_url="https://example.invalid/preview",
            origin="ai", verification_status="supported", status="pending",
        )
        session.add(candidate)
        create_candidate_notification(session, candidate)
        identifier = candidate.id

    class FakeClient:
        def __init__(self): self.calls = []
        def send(self, chat_id, text, **kwargs):
            self.calls.append((chat_id, text, kwargs))
            return SendResult(True, message_id="preview")
    client = FakeClient()
    assert deliver_candidate_one(factory, client, now=utcnow() + timedelta(seconds=1),
                                 reviewer_ids=frozenset({123}), review_buttons_enabled=True)
    assert deliver_candidate_one(factory, client, now=utcnow() + timedelta(seconds=1),
                                 reviewer_ids=frozenset({123}), review_buttons_enabled=True)
    assert len(client.calls) == 2
    authorized = next(call for call in client.calls if call[0] == "123")
    other = next(call for call in client.calls if call[0] == "999")
    assert "reply_markup" in authorized[2]
    assert "reply_markup" not in other[2]

    event = lambda user, update_id: {
        "update_id": update_id, "callback_query": {
            "id": f"callback-{update_id}",
            "from": {"id": user},
            "message": {"chat": {"id": user, "type": "private"}},
            "data": f"mcad:p:approve:{identifier}"
        },
    }
    authorized_reply = process_update(factory, event(123, 10), reviewer_ids=frozenset({123}), review_buttons_enabled=True)
    assert isinstance(authorized_reply, CallbackReply)
    assert "Preview only" in authorized_reply.text
    assert process_update(factory, event(123, 10), reviewer_ids=frozenset({123}), review_buttons_enabled=True) is None
    blocked_reply = process_update(factory, event(999, 11), reviewer_ids=frozenset({123}), review_buttons_enabled=True)
    assert "restricted" in blocked_reply.text
    disabled = process_update(factory, event(123, 12), reviewer_ids=frozenset({123}), review_buttons_enabled=False)
    assert "not enabled" in disabled.text

    with factory() as session:
        row = session.get(Candidate, identifier)
        assert row.status == "pending"
        assert row.incident_id is None
        assert session.scalar(select(func.count()).select_from(Incident)) == 0
        assert session.scalar(select(func.count()).select_from(ProcessedUpdate)) == 3
    engine.dispose()
