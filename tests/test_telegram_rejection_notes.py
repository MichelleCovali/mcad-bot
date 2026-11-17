"""Step 5: private Telegram free-text rejection explanations with explicit confirmation."""
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, Incident, OutboxEvent, WorkerState, utcnow
from mcad.telegram_review import (
    normalize_rejection_note, parse_rejection_action, rejection_confirmation_keyboard,
    rejection_note_session_key,
)
from mcad.workers import MessageReply, SendResult, process_update, seed_subscriber


REVIEWER_IDS = frozenset({123})
OPTIONS = dict(reviewer_ids=REVIEWER_IDS, review_buttons_enabled=True,
               rejection_actions_enabled=True, rejection_notes_enabled=True)


class Bot:
    def send(self, chat_id, text, **kwargs):
        return SendResult(True, message_id="fixture")


def env(tmp_path):
    engine = make_engine("sqlite:///" + str(tmp_path / "rejection-notes.sqlite3"))
    migrate(engine)
    factory = get_session_factory(engine)
    with factory.begin() as session:
        seed_subscriber(session, "123")
        seed_subscriber(session, "456")
        candidate = Candidate(title="Synthetic AI vessel report", source_url="https://example.invalid/note-report",
                              origin="ai", verification_status="supported", relationship_type="new_incident",
                              status="pending")
        session.add(candidate)
        create_candidate_notification(session, candidate)
        candidate_id = candidate.id
    assert deliver_candidate_one(factory, Bot(), now=utcnow() + timedelta(seconds=2),
                                 reviewer_ids=REVIEWER_IDS, review_buttons_enabled=True,
                                 rejection_actions_enabled=True)
    # Deliver one event to an ordinary subscriber as well.
    assert deliver_candidate_one(factory, Bot(), now=utcnow() + timedelta(seconds=2),
                                 reviewer_ids=REVIEWER_IDS, review_buttons_enabled=True,
                                 rejection_actions_enabled=True)
    return engine, factory, candidate_id


def cb(number, user_id, candidate_id, action, reason="d", *, chat_id=None):
    data = (f"mcad:r:{action}:{candidate_id}" if action in {"start", "cancel"} else
            f"mcad:r:{action}:{reason}:{candidate_id}")
    return {"update_id": number, "callback_query": {"id": f"cb-{number}",
            "from": {"id": user_id, "is_bot": False},
            "message": {"chat": {"id": user_id if chat_id is None else chat_id, "type": "private"}},
            "data": data}}


def msg(number, user_id, text, *, reply=True, chat_id=None, from_id=None):
    message = {"chat": {"id": user_id if chat_id is None else chat_id, "type": "private"},
               "from": {"id": user_id if from_id is None else from_id, "is_bot": False},
               "text": text}
    if reply:
        message["reply_to_message"] = {"from": {"is_bot": True},
                                       "text": "MCAD rejection explanation — reply to THIS message"}
    return {"update_id": number, "message": message}


def start(factory, cid, base=10):
    choose = process_update(factory, cb(base, 123, cid, "reason"), **OPTIONS)
    assert "Add explanation" in str(choose.reply_markup)
    prompt = process_update(factory, cb(base+1, 123, cid, "note"), **OPTIONS)
    assert prompt.reply_markup == {"force_reply": True,
                                   "input_field_placeholder": "Explain the rejection"}
    return prompt


def test_note_confirmation_and_exact_persistence(tmp_path):
    engine, factory, cid = env(tmp_path)
    start(factory, cid)
    reply = process_update(factory, msg(12, 123, "  Not a separate incident.  Already recorded. \n See original. "), **OPTIONS)
    assert isinstance(reply, MessageReply)
    assert "Confirm with explanation" in str(reply.reply_markup)
    with factory() as session:
        candidate = session.get(Candidate, cid)
        assert candidate.status == "pending" and candidate.rejection_notes is None
    old = process_update(factory, cb(13, 123, cid, "confirm"), **OPTIONS)
    assert "latest confirmation" in old.text
    accepted = process_update(factory, cb(14, 123, cid, "final"), **OPTIONS)
    assert "Rejected" in accepted.text
    with factory() as session:
        candidate = session.get(Candidate, cid)
        assert candidate.rejection_reason == "duplicate"
        assert candidate.rejection_notes == "Not a separate incident. Already recorded. See original."
        assert candidate.status == "rejected"
        history = candidate.evidence["telegramReviewHistory"]
        assert history[-1]["hasExplanation"] is True
        assert history[-1]["reviewerId"] == "123"
        assert session.get(WorkerState, rejection_note_session_key(123)) is None
        assert session.scalar(select(func.count()).select_from(Incident)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    again = process_update(factory, cb(15, 123, cid, "final"), **OPTIONS)
    assert "cannot be rejected" in again.text
    with factory() as session:
        assert session.get(Candidate, cid).rejection_notes == "Not a separate incident. Already recorded. See original."
    engine.dispose()


def test_note_must_be_direct_reply_from_reviewer(tmp_path):
    engine, factory, cid = env(tmp_path)
    start(factory, cid)
    wrong = process_update(factory, msg(12, 123, "wrong message", reply=False), **OPTIONS)
    assert "Reply directly" in wrong[1]
    wrong2 = process_update(factory, msg(13, 123, "spoofed sender", from_id=456), **OPTIONS)
    assert wrong2 is None
    other = process_update(factory, msg(14, 456, "outsider note"), **OPTIONS)
    assert other is None
    bad = process_update(factory, msg(15, 123, "x" * 1001), **OPTIONS)
    assert "1000 characters" in bad[1]
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
        assert session.get(WorkerState, rejection_note_session_key(123)).value["stage"] == "awaiting_text"
    engine.dispose()


def test_cancel_and_expiration_never_apply_note(tmp_path):
    engine, factory, cid = env(tmp_path)
    start(factory, cid)
    cancelled = process_update(factory, msg(12, 123, "/cancel", reply=False), **OPTIONS)
    assert "cancelled" in cancelled[1]
    assert "expired or unavailable" in process_update(factory, cb(13, 123, cid, "final"), **OPTIONS).text
    start(factory, cid, 20)
    with factory.begin() as session:
        row = session.get(WorkerState, rejection_note_session_key(123))
        row.value = {**row.value, "expiresAt": time.time() - 1}
    expired = process_update(factory, msg(22, 123, "text"), **OPTIONS)
    assert "expired" in expired[1]
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
    engine.dispose()


def test_flag_disabled_keeps_old_rejection_without_notes(tmp_path):
    engine, factory, cid = env(tmp_path)
    opts = {**OPTIONS, "rejection_notes_enabled": False}
    reason = process_update(factory, cb(11, 123, cid, "reason"), **opts)
    assert "Add explanation" not in str(reason.reply_markup)
    disabled = process_update(factory, cb(12, 123, cid, "note"), **opts)
    assert "disabled" in disabled.text
    saved = process_update(factory, cb(13, 123, cid, "confirm"), **opts)
    assert "Rejected" in saved.text
    with factory() as session:
        c = session.get(Candidate, cid)
        assert c.status == "rejected" and c.rejection_notes == ""
    engine.dispose()


def test_already_reviewed_on_website_cannot_be_overwritten(tmp_path):
    engine, factory, cid = env(tmp_path)
    start(factory, cid)
    process_update(factory, msg(12, 123, "Additional documentation is not independent."), **OPTIONS)
    with factory.begin() as session:
        c = session.get(Candidate, cid)
        c.status = "rejected"
        c.rejection_reason = "incorrect_information"
        c.rejection_notes = "Website reviewed first"
    out = process_update(factory, cb(13, 123, cid, "final"), **OPTIONS)
    assert "cannot be rejected" in out.text
    with factory() as session:
        c = session.get(Candidate, cid)
        assert c.rejection_reason == "incorrect_information"
        assert c.rejection_notes == "Website reviewed first"
    engine.dispose()


def test_notes_rejected_if_unsubscribed_after_prompt(tmp_path):
    engine, factory, cid = env(tmp_path)
    start(factory, cid)
    with factory.begin() as session:
        from mcad.models import Subscriber
        session.get(Subscriber, "123").active = False
    out = process_update(factory, msg(12, 123, "Something is not reliable."), **OPTIONS)
    assert "cannot be reviewed" in out[1]
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
    engine.dispose()


def test_note_helpers_validate_input_and_callback_length():
    uid = str(uuid4())
    assert parse_rejection_action(f"mcad:r:note:d:{uid}") == ("note", "d", uid)
    assert parse_rejection_action(f"mcad:r:final:d:{uid}") == ("final", "d", uid)
    assert len(f"mcad:r:final:d:{uid}".encode()) <= 64
    assert parse_rejection_action(f"mcad:r:note:d:{uid}:extra") is None
    assert "Add explanation" in str(rejection_confirmation_keyboard(uid, "d", allow_note=True))
    assert "mcad:r:final" in str(rejection_confirmation_keyboard(uid, "d", with_note=True))
    assert normalize_rejection_note("  one  \n  two ") == "one two"
    for bad in (None, 34, "", "   ", "x" * 1001):
        assert normalize_rejection_note(bad) is None
    assert rejection_note_session_key(123) == "tg_reject_note:123"


def test_setting_default_disabled_and_env_can_opt_in(monkeypatch):
    assert Settings().enable_telegram_rejection_notes is False
    monkeypatch.setenv("ENABLE_TELEGRAM_REJECTION_NOTES", "true")
    assert Settings.from_env().enable_telegram_rejection_notes is True


def test_without_note_can_still_confirm_when_notes_enabled(tmp_path):
    engine, factory, cid = env(tmp_path)
    reason = process_update(factory, cb(10, 123, cid, "reason"), **OPTIONS)
    assert "Confirm without explanation" in str(reason.reply_markup)
    saved = process_update(factory, cb(11, 123, cid, "confirm"), **OPTIONS)
    assert "Rejected" in saved.text
    with factory() as session:
        candidate = session.get(Candidate, cid)
        assert candidate.rejection_reason == "duplicate"
        assert candidate.rejection_notes == ""
    engine.dispose()


def test_expired_draft_does_not_save_even_from_old_confirm_button(tmp_path):
    engine, factory, cid = env(tmp_path)
    start(factory, cid)
    process_update(factory, msg(12, 123, "This is copied from earlier coverage."), **OPTIONS)
    with factory.begin() as session:
        row = session.get(WorkerState, rejection_note_session_key(123))
        row.value = {**row.value, "expiresAt": time.time() - 1}
    stale = process_update(factory, cb(13, 123, cid, "final"), **OPTIONS)
    assert "expired or unavailable" in stale.text
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
    engine.dispose()


def test_restart_discards_previous_draft(tmp_path):
    engine, factory, cid = env(tmp_path)
    start(factory, cid)
    process_update(factory, msg(12, 123, "The source is not independent."), **OPTIONS)
    chosen = process_update(factory, cb(13, 123, cid, "reason", "m"), **OPTIONS)
    assert "Not maritime" in chosen.message
    with factory() as session:
        assert session.get(WorkerState, rejection_note_session_key(123)) is None
    stale = process_update(factory, cb(14, 123, cid, "final"), **OPTIONS)
    assert "expired or unavailable" in stale.text
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
    engine.dispose()
