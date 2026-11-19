"""Telegram Step 6: bounded classification corrections without publication."""
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
    classification_keyboard, parse_classification_action, classification_session_key,
)
from mcad.workers import SendResult, process_update, seed_subscriber


REVIEWERS = frozenset({123, 321})
FLAGS = dict(reviewer_ids=REVIEWERS, review_buttons_enabled=True,
             rejection_actions_enabled=True, approval_actions_enabled=True,
             approval_secret="fixture-bot-secret", classification_actions_enabled=True)


class Bot:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SendResult(True, message_id=str(len(self.sent)))


def evidence_source(url, group):
    return {"url": url, "independenceGroup": group, "title": group,
            "snippet": "Fictional evidence", "openedByVerifier": True,
            "supports": ["Fictional incident"], "contradicts": []}


def setup(tmp_path, *, extra_reviewer=True, incident_type="new_incident"):
    engine = make_engine("sqlite:///" + str(tmp_path / "classification.sqlite3"))
    migrate(engine)
    factory = get_session_factory(engine)
    with factory.begin() as session:
        for chat in (["123", "321", "456"] if extra_reviewer else ["123", "456"]):
            seed_subscriber(session, chat)
        first = "https://first.invalid/mcad-mock"
        candidate = Candidate(title="TEST ONLY - AIS spoofing near vessel", source_url=first,
                              origin="ai", status="pending", verification_status="supported",
                              relationship_type=incident_type,
                              proposed_fields={"title": "TEST ONLY - AIS spoofing near vessel",
                                               "year": 2026, "victimType": "Vessel",
                                               "method": "AIS spoofing", "summary": "Synthetic exercise"},
                              evidence={"firstReportedAt": "2026-11-17T08:00:00Z", "contradictions": [],
                                        "sources": [evidence_source(first, "source-A"),
                                                    evidence_source("https://second.invalid/source", "source-B")]})
        session.add(candidate)
        create_candidate_notification(session, candidate)
        cid = candidate.id
    bot = Bot()
    for _ in range(3 if extra_reviewer else 2):
        assert deliver_candidate_one(factory, bot, now=utcnow() + timedelta(seconds=5),
                                     reviewer_ids=REVIEWERS, review_buttons_enabled=True,
                                     approval_actions_enabled=True, rejection_actions_enabled=True,
                                     classification_actions_enabled=True)
    return engine, factory, bot, cid


def callback(n, uid, data, *, actual_chat=None, kind="private"):
    return {"update_id": n, "callback_query": {
        "id": f"cb-{n}", "from": {"id": uid, "is_bot": False},
        "message": {"chat": {"id": uid if actual_chat is None else actual_chat, "type": kind}},
        "data": data,
    }}


def click(factory, n, uid, data, **overrides):
    flags = {**FLAGS, **overrides}
    return process_update(factory, callback(n, uid, data), **flags)


def take_step(factory, candidate_id, *, reviewer=123, start=10, field="v", value="p"):
    a = click(factory, start, reviewer, f"mcad:c:start:{candidate_id}")
    assert "Maritime category" in str(a.reply_markup)
    b = click(factory, start+1, reviewer, f"mcad:c:field:{field}:{candidate_id}")
    assert b.reply_markup is not None
    c = click(factory, start+2, reviewer, f"mcad:c:pick:{field}{value}:{candidate_id}")
    assert "does NOT publish" in c.message
    confirmation = c.reply_markup["inline_keyboard"][0][0]["callback_data"]
    assert len(confirmation.encode()) <= 64
    return confirmation


def test_buttons_and_validated_callback_data():
    cid = str(uuid4())
    menu = classification_keyboard(cid, enabled=True)["inline_keyboard"]
    assert menu[1][0]["text"] == "Correct classification"
    assert parse_classification_action(menu[1][0]["callback_data"]) == ("start", None, cid)
    assert parse_classification_action(f"mcad:c:field:v:{cid}") == ("field", "v", cid)
    assert parse_classification_action(f"mcad:c:pick:mg:{cid}") == ("pick", "mg", cid)
    assert parse_classification_action(f"mcad:c:pick:xx:{cid}") is None
    assert parse_classification_action(f"mcad:c:save:longnotvalid:{cid}") is None
    assert parse_classification_action(f"mcad:c:start:invalid") is None
    assert parse_classification_action("x" * 65) is None
    assert Settings().enable_telegram_classification_actions is False
    assert classification_keyboard(cid, enabled=False)["inline_keyboard"][1][0]["text"] == "Correct (preview)"


def test_delivered_buttons_only_for_reviewers(tmp_path):
    engine, _, bot, _ = setup(tmp_path)
    for chat, _, kwargs in bot.sent:
        if chat == "456":
            assert "reply_markup" not in kwargs
        else:
            assert "Correct classification" in str(kwargs["reply_markup"])
    engine.dispose()


def test_confirm_category_change_saves_audit_but_does_not_publish(tmp_path):
    engine, factory, _, cid = setup(tmp_path)
    confirmation = take_step(factory, cid)
    with factory() as session:
        c = session.get(Candidate, cid)
        assert c.proposed_fields["victimType"] == "Vessel" and c.status == "pending"
    result = click(factory, 13, 123, confirmation)
    assert "still pending" in result.text
    with factory() as session:
        c = session.get(Candidate, cid)
        assert c.status == "pending" and c.incident_id is None
        assert c.proposed_fields["victimType"] == "Port"
        assert c.proposed_fields["method"] == "AIS spoofing"
        assert c.evidence["classificationReviews"] == [{
            **{k:v for k,v in c.evidence["classificationReviews"][0].items() if k in ("reviewedAt",)},
            "source": "telegram", "reviewerId": "123", "field": "victimType",
            "previousValue": "Vessel", "newValue": "Port",
        }]
        assert session.scalar(select(func.count()).select_from(Incident)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.get(WorkerState, classification_session_key(123)) is None
    stale = click(factory, 14, 123, confirmation)
    assert "expired" in stale.text or "outdated" in stale.text
    with factory() as session:
        assert len(session.get(Candidate, cid).evidence["classificationReviews"]) == 1
    engine.dispose()


def test_method_correction_and_approval_uses_updated_fields(tmp_path):
    engine, factory, _, cid = setup(tmp_path)
    confirm = take_step(factory, cid, field="m", value="g")
    assert "saved" in click(factory, 13, 123, confirm).text.lower()
    preview = click(factory, 14, 123, f"mcad:a:start:{cid}")
    assert "GPS spoofing" in preview.message
    published = click(factory, 15, 123, preview.reply_markup["inline_keyboard"][0][0]["callback_data"])
    assert "Published" in published.text
    with factory() as session:
        inc = session.scalar(select(Incident))
        assert inc.method == "GPS spoofing"
        c = session.get(Candidate, cid)
        assert c.status == "approved"
        assert c.evidence["classificationReviews"][0]["newValue"] == "GPS spoofing"
    engine.dispose()


def test_prior_approval_confirmation_invalidated_after_other_reviewer_corrects(tmp_path):
    engine, factory, _, cid = setup(tmp_path)
    approval = click(factory, 1, 321, f"mcad:a:start:{cid}")
    old_confirm = approval.reply_markup["inline_keyboard"][0][0]["callback_data"]
    confirm = take_step(factory, cid, start=10)
    click(factory, 13, 123, confirm)
    stale = click(factory, 14, 321, old_confirm)
    assert "changed" in stale.text.lower()
    with factory() as session:
        assert session.get(Candidate, cid).status == "pending"
        assert session.scalar(select(func.count()).select_from(Incident)) == 0
    engine.dispose()


def test_unauthorized_and_not_delivered_or_disabled(tmp_path):
    engine, factory, _, cid = setup(tmp_path)
    not_allowed = click(factory, 1, 456, f"mcad:c:start:{cid}")
    assert "restricted" in not_allowed.text
    not_enabled = click(factory, 2, 123, f"mcad:c:start:{cid}", classification_actions_enabled=False)
    assert "disabled" in not_enabled.text
    forged = process_update(factory, callback(3, 123, f"mcad:c:start:{cid}", actual_chat=321), **FLAGS)
    assert "restricted" in forged.text
    outsider = click(factory, 4, 222, f"mcad:c:start:{cid}", reviewer_ids=frozenset({222}))
    assert "not delivered" in outsider.text
    engine.dispose()


def test_no_candidate_mutation_before_confirmation_and_cancel(tmp_path):
    engine, factory, _, cid = setup(tmp_path)
    confirmation = take_step(factory, cid)
    cancelled = click(factory, 13, 123, f"mcad:c:cancel:{cid}")
    assert "cancelled" in cancelled.text
    cannot_save = click(factory, 14, 123, confirmation)
    assert "expired" in cannot_save.text or "outdated" in cannot_save.text
    with factory() as session:
        c = session.get(Candidate, cid)
        assert c.proposed_fields["victimType"] == "Vessel"
        assert not c.evidence.get("classificationReviews")
    engine.dispose()


def test_expired_session_and_change_by_website_blocks_old_correction(tmp_path):
    engine, factory, _, cid = setup(tmp_path)
    confirm = take_step(factory, cid)
    with factory.begin() as session:
        state = session.get(WorkerState, classification_session_key(123))
        state.value = {**state.value, "expiresAt": time.time() - 1}
    assert "expired" in click(factory, 13, 123, confirm).text
    confirm = take_step(factory, cid, start=20)
    with factory.begin() as session:
        c = session.get(Candidate, cid)
        c.proposed_fields = {**c.proposed_fields, "victimType": "Offshore infrastructure"}
    assert "changed" in click(factory, 23, 123, confirm).text
    with factory() as session:
        assert session.get(Candidate, cid).proposed_fields["victimType"] == "Offshore infrastructure"
    engine.dispose()


def test_relationship_or_review_change_blocks_correction(tmp_path):
    engine, factory, _, cid = setup(tmp_path)
    confirm = take_step(factory, cid)
    with factory.begin() as session:
        session.get(Candidate, cid).status = "rejected"
    assert "web admin" in click(factory, 13, 123, confirm).text
    with factory() as session:
        assert session.get(Candidate, cid).proposed_fields["victimType"] == "Vessel"
    engine.dispose()
