from dataclasses import replace

import pytest
from sqlalchemy import select

from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import Candidate, CandidateDelivery, Incident, Subscriber
from mcad.service import upsert_incident
from mcad.workers import SendResult, deliver_one, process_update


CHAT = -1001234
REVIEWER = 5001
SUBSCRIBER = "9000"
MESSAGE = 42
FIRST = "https://port-authority.example/incident"
SECOND = "https://shipping-journal.example/incident"
SETTINGS = Settings(telegram_bot_token="synthetic-only", telegram_review_chat_id=str(CHAT),
                    telegram_reviewer_ids=(str(REVIEWER),))


class FakeBot:
    def __init__(self):
        self.reviews = []
        self.publications = []

    def send_with_review(self, chat_id, text, markup):
        self.reviews.append((chat_id, text, markup))
        return SendResult(True, message_id=str(MESSAGE))

    def send(self, chat_id, text):
        self.publications.append((chat_id, text))
        return SendResult(True, message_id="99")


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'telegram-review.sqlite3'}")
    migrate(engine)
    try:
        yield get_session_factory(engine)
    finally:
        engine.dispose()


def prepare(factory, *, references=(FIRST, SECOND), suggested_id=None, review_chat_id=str(CHAT)):
    title = "Fictional incident at the test port"
    summary = "A disruption affected the test port's cargo booking system."
    evidence = {"sources": [{"url": url, "independenceGroup": f"origin-{index}", "openedByVerifier": True,
                              "supports": ["Temporary outage"], "contradicts": []}
                             for index, url in enumerate(references)]}
    if suggested_id:
        evidence.update(suggestedIncidentId=suggested_id, materialUpdate={"reportedAt": "2026-11-17",
            "summary": "Cargo bookings resumed after the disruption.", "supportingUrls": list(references)})
    with factory.begin() as session:
        session.add(Subscriber(chat_id=SUBSCRIBER))
        draft = Candidate(title=title, summary=summary, origin="ai", verification_status="supported",
            source_url=FIRST, proposed_fields={"summary": summary, "referenceUrls": list(references)}, evidence=evidence)
        session.add(draft)
        create_candidate_notification(session, draft, review_chat_id=review_chat_id)
        return draft.id


def callback(update_id, data, *, chat=CHAT, actor=REVIEWER, message=MESSAGE):
    return {"update_id": update_id, "callback_query": {"id": f"query-{update_id}", "data": data,
        "from": {"id": actor}, "message": {"chat": {"type": "supergroup", "id": chat}, "message_id": message}}}


def reply(update_id, text, *, actor=REVIEWER, message=MESSAGE):
    return {"update_id": update_id, "message": {"from": {"id": actor}, "text": text,
        "chat": {"type": "supergroup", "id": CHAT}, "reply_to_message": {"message_id": message}}}


def send_review(factory):
    bot = FakeBot()
    assert deliver_candidate_one(factory, bot, "https://mcad.example", review_chat_id=str(CHAT))
    assert len(bot.reviews) == 1
    return bot


def buttons(bot):
    return {button["text"]: button["callback_data"] for row in bot.reviews[0][2]["inline_keyboard"]
            for button in row if "callback_data" in button}


def test_approval_is_bound_to_the_exact_review_chat_delivery_and_reviewer(factory):
    candidate_id = prepare(factory)
    bot = send_review(factory)
    destination, text, _ = bot.reviews[0]
    assert destination == str(CHAT)
    assert all(value in text for value in ["Fictional incident", "cargo booking system", FIRST, SECOND])
    assert not bot.publications
    assert set(buttons(bot)) == {"Accept", "Reject"}
    accept = buttons(bot)["Accept"]
    assert len(accept.encode()) <= 64

    for number, change in enumerate([{"actor": 999}, {"chat": -101}, {"message": MESSAGE + 1}], start=1):
        assert "cannot review" in process_update(factory, callback(number, accept, **change), settings=SETTINGS).text
    disabled = replace(SETTINGS, telegram_reviewer_ids=())
    assert "cannot review" in process_update(factory, callback(4, accept), settings=disabled).text
    with factory() as session:
        assert session.get(Candidate, candidate_id).status == "pending"
        assert session.get(Subscriber, str(CHAT)).active is False
        assert session.scalar(select(Incident)) is None

    approved = callback(5, accept)
    assert process_update(factory, approved, settings=SETTINGS).text == "Incident published."
    assert process_update(factory, approved, settings=SETTINGS) is None
    assert "already been reviewed" in process_update(factory, callback(6, accept), settings=SETTINGS).text
    with factory() as session:
        [incident] = session.scalars(select(Incident)).all()
        assert session.get(Candidate, candidate_id).incident_id == incident.id
        assert {source.url for source in incident.sources} == {FIRST, SECOND}
    assert deliver_one(factory, bot)
    assert bot.publications == [(SUBSCRIBER, FIRST)]


def test_telegram_rejection_requires_a_reason_from_an_authorized_reviewer(factory):
    candidate_id = prepare(factory)
    reject = buttons(send_review(factory))["Reject"]
    assert "/reject" in process_update(factory, callback(1, reject), settings=SETTINGS).text
    reason = "/reject The article describes a drill. It does not report a real outage."
    assert process_update(factory, reply(2, reason, actor=9001), settings=SETTINGS) is None
    assert process_update(factory, reply(3, reason, message=88), settings=SETTINGS) is None
    assert "explain" in process_update(factory, reply(4, "/reject"), settings=SETTINGS)[1]
    with factory() as session:
        assert session.get(Candidate, candidate_id).status == "pending"
    assert "explanation was saved" in process_update(factory, reply(5, reason), settings=SETTINGS)[1]
    with factory() as session:
        draft = session.get(Candidate, candidate_id)
        assert draft.status == "rejected"
        assert draft.rejection_notes == reason.removeprefix("/reject ")
        assert session.scalar(select(Incident)) is None


def test_telegram_cannot_publish_single_source_even_with_a_forged_accept_button(factory):
    candidate_id = prepare(factory, references=(FIRST,))
    available = buttons(send_review(factory))
    assert set(available) == {"Reject"}
    accept = available["Reject"].removesuffix(":reject") + ":accept"
    assert "Confirm the sources" in process_update(factory, callback(1, accept), settings=SETTINGS).text
    with factory() as session:
        assert session.get(Candidate, candidate_id).status == "pending"
        assert session.scalar(select(Incident)) is None


def test_telegram_shows_two_independent_links_within_message_limits(factory):
    same_publisher = "https://press.port-authority.example/second-entry"
    first = FIRST + "?id=" + "a" * 720
    second = SECOND + "?id=" + "b" * 720
    candidate_id = prepare(factory, references=(first, same_publisher, second))
    with factory.begin() as session:
        draft = session.get(Candidate, candidate_id)
        draft.proposed_fields = {**draft.proposed_fields, "summary": "⚓️🛳️" * 1000}
    bot = send_review(factory)
    text = bot.reviews[0][1]
    assert first in text and second in text
    assert same_publisher not in text
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert "Accept" in buttons(bot)


def test_oversize_corroborating_link_requires_web_review(factory):
    long_url = SECOND + "?id=" + "s" * 1000
    candidate_id = prepare(factory, references=(FIRST, long_url))
    bot = send_review(factory)
    available = buttons(bot)
    assert set(available) == {"Reject"}
    assert "full link in MCAD" in bot.reviews[0][1]
    accept = available["Reject"].removesuffix(":reject") + ":accept"
    assert "web review queue" in process_update(factory, callback(1, accept), settings=SETTINGS).text
    with factory() as session:
        assert session.get(Candidate, candidate_id).status == "pending"


def test_telegram_updates_the_existing_incident_instead_of_publishing_a_duplicate(factory):
    with factory.begin() as session:
        incident, _ = upsert_incident(session, {"title": "Existing test port incident", "summary": "Original outage.",
            "sourceUrl": "https://archives.example/initial"})
        incident_id = incident.id
    prepare(factory, suggested_id=incident_id)
    bot = send_review(factory)
    assert "Existing test port incident" in bot.reviews[0][1]
    assert "Cargo bookings resumed" in bot.reviews[0][1]
    assert set(buttons(bot)) == {"Apply update", "Reject"}
    assert process_update(factory, callback(1, buttons(bot)["Apply update"]), settings=SETTINGS).text == "Existing incident updated."
    with factory() as session:
        [incident] = session.scalars(select(Incident)).all()
        assert incident.id == incident_id
        assert incident.summary == "Original outage."
        assert incident.raw_fields["_incidentUpdates"][0]["summary"] == "Cargo bookings resumed after the disruption."
    assert not deliver_one(factory, bot)


def test_changing_review_destination_cancels_old_unsent_drafts(factory):
    prepare(factory)
    bot = FakeBot()
    assert deliver_candidate_one(factory, bot, review_chat_id="-1008976")
    assert bot.reviews == bot.publications == []
    with factory() as session:
        delivery = session.scalar(select(CandidateDelivery))
        assert delivery.status == "cancelled"
        assert delivery.last_error == "review_chat_changed"
