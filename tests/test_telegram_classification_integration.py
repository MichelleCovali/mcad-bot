"""Opt-in Telegram classification corrections on the shared operations branch."""
from dataclasses import replace

import pytest
from sqlalchemy import select

from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, Incident, WorkerState
from mcad.service import upsert_incident
from mcad.workers import SendResult, TelegramClient, process_update


CHAT = -1001234
REVIEWER = 5001
OTHER_REVIEWER = 5002
MESSAGE = 42
FIRST = "https://port-authority.example/incident"
SECOND = "https://shipping-journal.example/incident"
SETTINGS = Settings(telegram_bot_token="fictional-bot", telegram_review_chat_id=str(CHAT),
                    telegram_reviewer_ids=(str(REVIEWER), str(OTHER_REVIEWER)),
                    enable_telegram_classification_actions=True)


class FakeBot:
    def __init__(self):
        self.reviews = []

    def send_with_review(self, chat_id, text, markup):
        self.reviews.append((chat_id, text, markup))
        return SendResult(True, message_id=str(MESSAGE))


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'telegram-classification.sqlite3'}")
    migrate(engine)
    try:
        yield get_session_factory(engine)
    finally:
        engine.dispose()


def prepare(factory, *, linked=False, fields=None):
    evidence = {"sources": [
        {"url": url, "independenceGroup": f"origin-{index}", "openedByVerifier": True,
         "supports": ["Fictional outage"], "contradicts": []}
        for index, url in enumerate((FIRST, SECOND))]}
    with factory.begin() as session:
        if linked:
            incident, _ = upsert_incident(session, {"title": "Old fictional event",
                "sourceUrl": "https://archives.example/old"})
            evidence["suggestedIncidentId"] = incident.id
            evidence["materialUpdate"] = {"summary": "New verified update", "supportingUrls": [FIRST, SECOND]}
        candidate = Candidate(origin="ai", verification_status="supported",
            title="Fictional incident for testing", summary="Fictional maritime cyber event",
            source_url=FIRST + ("?followup=1" if linked else ""), proposed_fields=fields or {"victimType": "Vessel",
                "method": "AIS spoofing", "referenceUrls": [FIRST, SECOND],
                "summary": "Fictional maritime cyber event"}, evidence=evidence)
        session.add(candidate)
        create_candidate_notification(session, candidate, review_chat_id=str(CHAT))
        return candidate.id


def send(factory, *, enabled=True):
    bot = FakeBot()
    assert deliver_candidate_one(factory, bot, review_chat_id=str(CHAT),
                                 classification_enabled=enabled)
    return bot


def buttons(markup):
    return {button["text"]: button["callback_data"]
            for row in markup["inline_keyboard"] for button in row
            if "callback_data" in button}


def callback(number, data, *, actor=REVIEWER, chat=CHAT, message=MESSAGE):
    return {"update_id": number, "callback_query": {"id": f"query-{number}", "data": data,
        "from": {"id": actor}, "message": {"chat": {"type": "supergroup", "id": chat},
                                             "message_id": message}}}


def start_port_confirmation(factory, bot, *, number=1, actor=REVIEWER):
    start = buttons(bot.reviews[0][2])["Correct classification"]
    first = process_update(factory, callback(number, start, actor=actor), settings=SETTINGS)
    assert "Nothing is published" in first.text
    assert first.chat_id == str(CHAT) and first.message_id == str(MESSAGE)
    field = buttons(first.markup)["Maritime category"]
    second = process_update(factory, callback(number + 1, field, actor=actor), settings=SETTINGS)
    assert "Port" in buttons(second.markup)
    choice = buttons(second.markup)["Port"]
    third = process_update(factory, callback(number + 2, choice, actor=actor), settings=SETTINGS)
    assert "Vessel" in third.text and "Port" in third.text
    return buttons(third.markup)["Save correction (no publication)"]


def test_classification_controls_are_opt_in_and_do_not_appear_for_updates(factory):
    prepare(factory)
    disabled = send(factory, enabled=False)
    assert "Correct classification" not in buttons(disabled.reviews[0][2])
    base = buttons(disabled.reviews[0][2])["Accept"]
    forged = base.removesuffix(":accept") + ":classify"
    assert "cannot review" in process_update(factory, callback(1, forged),
        settings=replace(SETTINGS, enable_telegram_classification_actions=False)).text
    # The bot never offers corrections for an already-linked follow-up.
    second_factory = factory
    prepare(second_factory, linked=True)
    linked = send(second_factory)
    assert "Correct classification" not in buttons(linked.reviews[0][2])


def test_confirmed_correction_is_audited_and_never_publishes(factory):
    candidate_id = prepare(factory)
    bot = send(factory)
    assert set(buttons(bot.reviews[0][2])) == {"Accept", "Reject", "Correct classification"}
    save = start_port_confirmation(factory, bot)
    assert len(save.encode("utf-8")) <= 64
    with factory() as session:
        assert session.get(Candidate, candidate_id).proposed_fields["victimType"] == "Vessel"
        assert session.scalar(select(Incident)) is None
    completed = process_update(factory, callback(4, save), settings=SETTINGS)
    assert "nothing published" in completed.text.lower()
    assert "Accept" in buttons(completed.markup)
    with factory() as session:
        draft = session.get(Candidate, candidate_id)
        assert draft.status == "pending" and draft.incident_id is None
        assert draft.proposed_fields["victimType"] == "Port"
        assert draft.evidence["classificationReviews"][0]["field"] == "victimType"
        assert draft.evidence["classificationReviews"][0]["oldValue"] == "Vessel"
        assert draft.evidence["classificationReviews"][0]["newValue"] == "Port"
        assert draft.evidence["classificationReviews"][0]["reviewerTelegramId"] == str(REVIEWER)
        assert session.scalar(select(Incident)) is None
    assert "no longer valid" in process_update(factory, callback(5, save), settings=SETTINGS).text


def test_method_correction_is_used_only_after_separate_approval(factory):
    candidate_id = prepare(factory)
    bot = send(factory)
    start = buttons(bot.reviews[0][2])["Correct classification"]
    menu = process_update(factory, callback(1, start), settings=SETTINGS)
    method = process_update(factory, callback(2, buttons(menu.markup)["Incident method"]), settings=SETTINGS)
    chosen = process_update(factory, callback(3, buttons(method.markup)["GPS spoofing"]), settings=SETTINGS)
    process_update(factory, callback(4, buttons(chosen.markup)["Save correction (no publication)"]), settings=SETTINGS)
    with factory() as session:
        assert session.get(Candidate, candidate_id).proposed_fields["method"] == "GPS spoofing"
        assert session.scalar(select(Incident)) is None
    approved = process_update(factory, callback(5, buttons(bot.reviews[0][2])["Accept"]), settings=SETTINGS)
    assert approved.text == "Incident published."
    with factory() as session:
        draft = session.get(Candidate, candidate_id)
        assert draft.status == "approved" and draft.incident_id
        assert session.get(Incident, draft.incident_id).method == "GPS spoofing"


def test_reviewer_and_delivery_binding_rejects_spoofed_callbacks(factory):
    prepare(factory)
    bot = send(factory)
    action = buttons(bot.reviews[0][2])["Correct classification"]
    for number, change in enumerate(({"actor": 9001}, {"chat": CHAT - 1},
                                     {"message": MESSAGE + 1}), start=1):
        assert "cannot review" in process_update(factory, callback(number, action, **change), settings=SETTINGS).text
    assert "cannot review" in process_update(factory, callback(4, action),
        settings=replace(SETTINGS, telegram_reviewer_ids=())).text
    for number, action_data in enumerate((action.replace(":classify", ":vQ"),
                                           action.replace(":classify", ":save:abcdefgh")), start=5):
        result = process_update(factory, callback(number, action_data), settings=SETTINGS)
        assert "cannot review" in result.text or "no longer valid" in result.text


def test_confirmation_is_reviewer_bound_and_stale_changes_are_rejected(factory):
    candidate_id = prepare(factory)
    bot = send(factory)
    save = start_port_confirmation(factory, bot)
    assert "no longer valid" in process_update(factory, callback(4, save, actor=OTHER_REVIEWER), settings=SETTINGS).text
    with factory.begin() as session:
        draft = session.get(Candidate, candidate_id)
        draft.proposed_fields = {**draft.proposed_fields, "victimType": "Undersea infrastructure"}
    assert "changed" in process_update(factory, callback(5, save), settings=SETTINGS).text
    with factory() as session:
        draft = session.get(Candidate, candidate_id)
        assert draft.proposed_fields["victimType"] == "Undersea infrastructure"
        assert not draft.evidence.get("classificationReviews")


def test_expired_confirmation_does_not_modify_candidate(factory):
    candidate_id = prepare(factory)
    save = start_port_confirmation(factory, send(factory))
    with factory.begin() as session:
        state = session.get(WorkerState, f"tg_classify:{REVIEWER}")
        state.value = {**state.value, "expiresAt": "2000-01-01T00:00:00"}
    assert "expired" in process_update(factory, callback(4, save), settings=SETTINGS).text
    with factory() as session:
        assert session.get(Candidate, candidate_id).proposed_fields["victimType"] == "Vessel"


def test_rejected_candidate_cannot_be_corrected(factory):
    candidate_id = prepare(factory)
    save = start_port_confirmation(factory, send(factory))
    with factory.begin() as session:
        session.get(Candidate, candidate_id).status = "rejected"
    assert "already been reviewed" in process_update(factory, callback(4, save), settings=SETTINGS).text
    with factory() as session:
        assert session.get(Candidate, candidate_id).proposed_fields["victimType"] == "Vessel"


def test_original_keyboard_is_restored_on_cancel(factory):
    prepare(factory)
    bot = send(factory)
    start = buttons(bot.reviews[0][2])["Correct classification"]
    menu = process_update(factory, callback(1, start), settings=SETTINGS)
    cancelled = process_update(factory, callback(2, buttons(menu.markup)["Cancel"]), settings=SETTINGS)
    assert "Correction cancelled" in cancelled.text
    assert "Accept" in buttons(cancelled.markup)
    assert "Correct classification" in buttons(cancelled.markup)


def test_telegram_client_edits_only_message_keyboard(monkeypatch):
    calls = []

    class Response:
        pass

    def fake_post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr("mcad.workers.requests.post", fake_post)
    markup = {"inline_keyboard": [[{"text": "Cancel", "callback_data": "mcad:review:x:cancel"}]]}
    TelegramClient("synthetic-only").edit_review_markup(str(CHAT), str(MESSAGE), markup)
    assert len(calls) == 1
    assert calls[0][0].endswith("/editMessageReplyMarkup")
    assert calls[0][1] == {"chat_id": str(CHAT), "message_id": MESSAGE, "reply_markup": markup}
