"""Step 3: safe, confirmed Telegram rejection using simulated updates only."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, Incident, OutboxEvent, ProcessedUpdate, utcnow
from mcad.telegram_review import (
    REJECTION_REASONS, parse_rejection_action, rejection_keyboard,
    rejection_reason_keyboard, rejection_confirmation_keyboard,
)
from mcad.workers import SendResult, process_update, seed_subscriber


class Bot:
    def __init__(self):
        self.calls = []

    def send(self, chat_id, message, **kwargs):
        self.calls.append((chat_id, message, kwargs))
        return SendResult(True, message_id="fixture")


def callback(update_id, user_id, action, candidate_id, key=None, *, chat_id=None, chat_type="private"):
    if key is None:
        data = f"mcad:r:{action}:{candidate_id}"
    else:
        data = f"mcad:r:{action}:{key}:{candidate_id}"
    return {"update_id": update_id, "callback_query": {
        "id": f"cb-{update_id}", "from": {"id": user_id},
        "message": {"chat": {"id": user_id if chat_id is None else chat_id, "type": chat_type}},
        "data": data,
    }}


def setup(tmp_path, *, second_subscriber=True):
    engine = make_engine("sqlite:///" + str(tmp_path / "telegram-reject.sqlite3"))
    migrate(engine)
    factory = get_session_factory(engine)
    with factory.begin() as session:
        seed_subscriber(session, "123")
        if second_subscriber:
            seed_subscriber(session, "456")
        report = Candidate(title="Synthetic AI vessel report", source_url="https://example.invalid/report-3",
                           origin="ai", verification_status="supported", relationship_type="new_incident",
                           status="pending")
        session.add(report)
        create_candidate_notification(session, report)
        candidate_id = report.id
    bot = Bot()
    delivered = int(deliver_candidate_one(factory, bot, now=utcnow() + timedelta(seconds=2),
                                          reviewer_ids=frozenset({123}), review_buttons_enabled=True,
                                          rejection_actions_enabled=True))
    if second_subscriber:
        delivered += int(deliver_candidate_one(factory, bot, now=utcnow() + timedelta(seconds=2),
                                               reviewer_ids=frozenset({123}), review_buttons_enabled=True,
                                               rejection_actions_enabled=True))
    assert delivered == (2 if second_subscriber else 1)
    return engine, factory, bot, candidate_id


def test_callback_data_format_and_disabled_or_malformed():
    identifier = str(uuid4())
    assert rejection_keyboard(identifier)["inline_keyboard"][0][1]["text"].startswith("Reject")
    assert parse_rejection_action(f"mcad:r:start:{identifier}") == ("start", None, identifier)
    assert parse_rejection_action(f"mcad:r:cancel:{identifier}") == ("cancel", None, identifier)
    for reason in REJECTION_REASONS:
        for action in ("reason", "confirm"):
            encoded = f"mcad:r:{action}:{reason}:{identifier}"
            assert len(encoded.encode()) <= 64
            assert parse_rejection_action(encoded) == (action, reason, identifier)
    for bad in ("mcad:r:start:123", f"mcad:r:reason:invalid:{identifier}",
                f"mcad:r:confirm:z:{identifier}", f"mcad:r:confirm:d:{identifier}:extra",
                f"mcad:r:start:{identifier}:overlong", None):
        assert parse_rejection_action(bad) is None
    assert len(rejection_reason_keyboard(identifier)["inline_keyboard"]) == len(REJECTION_REASONS) + 1
    assert len(rejection_confirmation_keyboard(identifier, "d")["inline_keyboard"]) == 2


def test_authorized_full_confirmation_persists_feedback_without_publication(tmp_path):
    engine, factory, bot, cid = setup(tmp_path)
    reviewer_call = next(call for call in bot.calls if call[0] == "123")
    ordinary_call = next(call for call in bot.calls if call[0] == "456")
    assert "Reject (choose reason)" in str(reviewer_call[2]["reply_markup"])
    assert "reply_markup" not in ordinary_call[2]
    ids = frozenset({123})
    # Opening a menu and choosing a reason must NEVER change candidate state.
    r1 = process_update(factory, callback(100, 123, "start", cid), reviewer_ids=ids,
                        review_buttons_enabled=True, rejection_actions_enabled=True)
    assert "Choose" in r1.message
    assert "inline_keyboard" in r1.reply_markup
    r2 = process_update(factory, callback(101, 123, "reason", cid, "d"), reviewer_ids=ids,
                        review_buttons_enabled=True, rejection_actions_enabled=True)
    assert "Duplicate" in r2.message and "Confirm rejection" in str(r2.reply_markup)
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
    r3 = process_update(factory, callback(102, 123, "confirm", cid, "d"), reviewer_ids=ids,
                        review_buttons_enabled=True, rejection_actions_enabled=True)
    assert "Rejected" in r3.text
    with factory() as session:
        candidate = session.get(Candidate, cid)
        assert candidate.status == "rejected"
        assert candidate.rejection_reason == "duplicate"
        assert candidate.rejection_notes == ""
        review_history = candidate.evidence["telegramReviewHistory"]
        assert len(review_history) == 1
        assert review_history[0]["reviewerId"] == "123"
        assert review_history[0]["reason"] == "duplicate"
        assert session.scalar(select(func.count()).select_from(Incident)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.scalar(select(func.count()).select_from(ProcessedUpdate)) == 3
    # Different update IDs (a second button press) must not overwrite the first rejection.
    retry = process_update(factory, callback(103, 123, "confirm", cid, "m"), reviewer_ids=ids,
                           review_buttons_enabled=True, rejection_actions_enabled=True)
    assert "cannot be rejected" in retry.text.lower()
    assert process_update(factory, callback(102, 123, "confirm", cid, "d"), reviewer_ids=ids,
                          review_buttons_enabled=True, rejection_actions_enabled=True) is None
    with factory() as session:
        assert session.get(Candidate, cid).rejection_reason == "duplicate"
    engine.dispose()


def test_unauthorized_unsubscribed_undelivered_and_group_callbacks_fail(tmp_path):
    engine, factory, bot, cid = setup(tmp_path)
    for i, event in enumerate((
        callback(200, 456, "confirm", cid, "d"),  # subscribed but not reviewer
        callback(201, 123, "confirm", cid, "d", chat_id=456),  # user/chat mismatch
        callback(202, 123, "confirm", cid, "d", chat_type="group"),
    )):
        resp = process_update(factory, event, reviewer_ids=frozenset({123}),
                              review_buttons_enabled=True, rejection_actions_enabled=True)
        assert "restricted" in resp.text
    with factory.begin() as session:
        from mcad.models import Subscriber
        session.get(Subscriber, "123").active = False
    reply = process_update(factory, callback(203, 123, "confirm", cid, "d"),
                           reviewer_ids=frozenset({123}), review_buttons_enabled=True,
                           rejection_actions_enabled=True)
    assert "not delivered" in reply.text
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
    engine.dispose()


def test_disabled_flag_and_cancel_do_not_modify_data(tmp_path):
    engine, factory, bot, cid = setup(tmp_path, second_subscriber=False)
    user_ids = frozenset({123})
    result = process_update(factory, callback(300, 123, "confirm", cid, "d"),
                            reviewer_ids=user_ids, review_buttons_enabled=True,
                            rejection_actions_enabled=False)
    assert "disabled" in result.text
    cancel = process_update(factory, callback(301, 123, "cancel", cid),
                            reviewer_ids=user_ids, review_buttons_enabled=True,
                            rejection_actions_enabled=True)
    assert "cancelled" in cancel.text
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
    engine.dispose()


def test_existing_website_decision_is_not_overwritten(tmp_path):
    engine, factory, bot, cid = setup(tmp_path, second_subscriber=False)
    with factory.begin() as session:
        report = session.get(Candidate, cid)
        report.status = "rejected"
        report.rejection_reason = "incorrect_information"
        report.rejection_notes = "Reviewed through admin panel"
    result = process_update(factory, callback(400, 123, "confirm", cid, "d"),
                            reviewer_ids=frozenset({123}), review_buttons_enabled=True,
                            rejection_actions_enabled=True)
    assert "cannot be rejected" in result.text.lower()
    with factory() as session:
        report = session.get(Candidate, cid)
        assert report.rejection_reason == "incorrect_information"
        assert report.rejection_notes == "Reviewed through admin panel"
    engine.dispose()


def test_undelivered_ai_candidate_cannot_be_rejected(tmp_path):
    engine, factory, bot, cid = setup(tmp_path, second_subscriber=False)
    with factory.begin() as session:
        second = Candidate(title="No notification", source_url="https://example.invalid/not-delivered",
                           origin="ai", verification_status="supported", status="pending")
        session.add(second)
        session.flush()
        second_id = second.id
    reply = process_update(factory, callback(500, 123, "confirm", second_id, "d"),
                           reviewer_ids=frozenset({123}), review_buttons_enabled=True,
                           rejection_actions_enabled=True)
    assert "not delivered" in reply.text
    with factory() as session:
        assert session.get(Candidate, second_id).status == "pending"
    engine.dispose()
