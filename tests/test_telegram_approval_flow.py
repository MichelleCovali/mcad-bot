"""Step 4: approved AI reports can be published only by confirmed private reviewer callbacks."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from mcad.candidate_alerts import create_candidate_notification, deliver_candidate_one
from mcad.config import Settings
from mcad.db import get_session_factory, make_engine
from mcad.migrations import migrate
from mcad.models import (Candidate, Delivery, Incident,
                         OutboxEvent, ProcessedUpdate, Subscriber, utcnow)
from mcad.telegram_review import (approval_confirmation_keyboard, approval_keyboard,
                                  parse_approval_action, valid_approval_confirmation)
from mcad.workers import SendResult, process_update, seed_subscriber


class Bot:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, message, **kwargs):
        self.sent.append((chat_id, message, kwargs))
        return SendResult(True, message_id=str(len(self.sent)))


def source(url, group, *, opened=True, supports=True):
    return {"url": url, "independenceGroup": group, "title": group,
            "snippet": "Fictional source evidence with a detailed security incident description.",
            "openedByVerifier": opened, "supports": ["Incident confirmed"] if supports else [],
            "contradicts": []}


def fixture_candidate(url="https://example.invalid/mcad-port-report", title="Fictional AIS vessel incident"):
    return Candidate(title=title, source_url=url, origin="ai", status="pending",
                     verification_status="supported", relationship_type="new_incident",
                     proposed_fields={"title": title, "year": 2026, "victimType": "Vessel",
                                      "method": "AIS spoofing", "summary": "Fictional test case",
                                      "latitude": 50.0, "longitude": 4.0, "externalId": "DO-NOT-USE",
                                      "status": "pending", "referenceNumber": "NOT-REVIEWED"},
                     evidence={"firstReportedAt": "2026-11-17T08:00:00Z", "contradictions": [],
                               "sources": [source(url, "publisher-A"),
                                           source("https://another.invalid/mcad-second-source", "publisher-B")]})


def setup(tmp_path, *, subscriber_two=True, candidate=None):
    engine = make_engine("sqlite:///" + str(tmp_path / "approval.sqlite3"))
    migrate(engine)
    factory = get_session_factory(engine)
    with factory.begin() as session:
        seed_subscriber(session, "123")
        if subscriber_two:
            seed_subscriber(session, "456")
        candidate = candidate or fixture_candidate()
        session.add(candidate)
        create_candidate_notification(session, candidate)
        cid = candidate.id
    bot = Bot()
    total = 2 if subscriber_two else 1
    for _ in range(total):
        assert deliver_candidate_one(factory, bot, now=utcnow() + timedelta(seconds=1),
                                     reviewer_ids=frozenset({123}), review_buttons_enabled=True,
                                     rejection_actions_enabled=True, approval_actions_enabled=True)
    return engine, factory, bot, cid


def click(seq, uid, data, *, chat_id=None, chat_type="private"):
    return {"update_id": seq, "callback_query": {
        "id": f"cb-{seq}", "from": {"id": uid},
        "message": {"chat": {"id": uid if chat_id is None else chat_id, "type": chat_type}},
        "data": data,
    }}


def do(factory, seq, uid, data, *, enabled=True, secret="test-bot-secret", rejection=True):
    return process_update(factory, click(seq, uid, data), reviewer_ids=frozenset({123}),
                          review_buttons_enabled=True, rejection_actions_enabled=rejection,
                          approval_actions_enabled=enabled, approval_secret=secret)


def confirm_markup(reply):
    return reply.reply_markup["inline_keyboard"][0][0]["callback_data"]


def count(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_approval_button_with_optional_rejection_and_callback_security():
    cid = str(uuid4())
    buttons = approval_keyboard(cid, allow_rejection=True)["inline_keyboard"]
    assert buttons[0][0]["text"].startswith("Approve")
    assert buttons[0][1]["text"].startswith("Reject")
    assert "Correct (preview)" in buttons[1][0]["text"]
    assert parse_approval_action(buttons[0][0]["callback_data"]) == ("start", cid, None, None)
    confirm = approval_confirmation_keyboard(cid, 123, "secret", now=1794911000)
    encoded = confirm["inline_keyboard"][0][0]["callback_data"]
    assert len(encoded.encode()) <= 64
    stage, candidate, tick, sig = parse_approval_action(encoded)
    assert (stage, candidate) == ("confirm", cid)
    assert valid_approval_confirmation(cid, 123, "secret", tick, sig, now=1794911000)
    assert not valid_approval_confirmation(cid, 124, "secret", tick, sig, now=1794911000)
    assert not valid_approval_confirmation(cid, 123, "wrong", tick, sig, now=1794911000)
    assert not valid_approval_confirmation(cid, 123, "secret", tick, sig, now=1794911000 + 16*60)
    assert parse_approval_action("mcad:a:confirm:" + cid) is None
    assert parse_approval_action("mcad:a:c:" + cid + ":bad:s") is None
    assert parse_approval_action("mcad:a:start:not-a-uuid") is None
    assert parse_approval_action(encoded + "Z") is None
    assert approval_keyboard("bad") is None
    assert Settings().enable_telegram_approval_actions is False


def test_authorized_confirmation_publishes_once_and_preserves_ai_evidence(tmp_path):
    engine, factory, bot, cid = setup(tmp_path)
    markup = next(kwargs["reply_markup"] for recipient, _, kwargs in bot.sent if recipient == "123")
    assert markup["inline_keyboard"][0][0]["text"] == "Approve (review details)"
    assert "reply_markup" not in next(kwargs for recipient, _, kwargs in bot.sent if recipient == "456")
    reply = do(factory, 100, 123, f"mcad:a:start:{cid}")
    assert "Sources to verify" in reply.message
    assert "AIS spoofing" in reply.message
    assert "PUBLIC" in reply.message
    assert "example.invalid" in reply.message and "another.invalid" in reply.message
    assert count(factory, Incident) == 0
    confirmed = do(factory, 101, 123, confirm_markup(reply))
    assert "Published" in confirmed.text
    assert count(factory, Incident) == 1
    assert count(factory, OutboxEvent) == 1
    # Both subscribers already received candidate research alerts, so neither
    # gets a second published-incident notification.
    assert count(factory, Delivery) == 0
    with factory() as session:
        candidate = session.get(Candidate, cid)
        incident = session.get(Incident, candidate.incident_id)
        assert candidate.status == "approved"
        assert incident.status == "published"
        assert incident.title == "Fictional AIS vessel incident"
        assert incident.method == "AIS spoofing"
        assert incident.latitude is None and incident.longitude is None
        assert incident.external_id is None
        assert incident.reference_number == ""
        assert len(incident.sources) == 2
        assert incident.raw_fields["_aiReview"]["candidateId"] == cid
        assert incident.raw_fields["_telegramReview"]["reviewerId"] == "123"
        assert candidate.evidence["telegramReviewHistory"][0]["decision"] == "approved"
    again = do(factory, 102, 123, confirm_markup(reply))
    assert "expired or invalid" in again.text
    assert count(factory, Incident) == 1
    assert count(factory, OutboxEvent) == 1
    assert count(factory, ProcessedUpdate) == 3
    engine.dispose()


def test_approval_disabled_and_invalid_actor_no_write(tmp_path):
    engine, factory, bot, cid = setup(tmp_path)
    disabled = do(factory, 1, 123, f"mcad:a:start:{cid}", enabled=False)
    assert "disabled" in disabled.text
    other = do(factory, 2, 456, f"mcad:a:start:{cid}")
    assert "restricted" in other.text
    group = process_update(factory, click(3, 123, f"mcad:a:start:{cid}", chat_id=-22, chat_type="supergroup"),
        reviewer_ids=frozenset({123}), review_buttons_enabled=True,
        approval_actions_enabled=True, approval_secret="test-bot-secret")
    assert "restricted" in group.text
    no_secret = do(factory, 4, 123, f"mcad:a:start:{cid}", secret="")
    assert "disabled" in no_secret.text
    assert count(factory, Incident) == 0
    engine.dispose()


def test_reviewer_must_be_subscribed_and_had_received_candidate(tmp_path):
    engine, factory, bot, cid = setup(tmp_path, subscriber_two=False)
    with factory.begin() as session:
        session.get(Subscriber, "123").active = False
    r = do(factory, 1, 123, f"mcad:a:start:{cid}")
    assert "not delivered" in r.text
    assert count(factory, Incident) == 0
    engine.dispose()


def test_malformed_signature_and_expiration_no_write(tmp_path, monkeypatch):
    engine, factory, bot, cid = setup(tmp_path)
    monkeypatch.setattr("mcad.telegram_review.time.time", lambda: 1794911000)
    start = do(factory, 1, 123, f"mcad:a:start:{cid}")
    signed = confirm_markup(start)
    assert "expired" in do(factory, 2, 123, signed[:-1] + ("A" if signed[-1] != "A" else "B")).text
    monkeypatch.setattr("mcad.telegram_review.time.time", lambda: 1794911000 + 16*60)
    assert "expired" in do(factory, 3, 123, signed).text
    assert count(factory, Incident) == 0
    engine.dispose()


def test_unverified_sources_or_invalid_proposed_fields_require_web_review(tmp_path):
    changes = [
        lambda c: setattr(c, "evidence", {**c.evidence, "sources": c.evidence["sources"][:1]}),
        lambda c: setattr(c, "evidence", {**c.evidence, "contradictions": ["Conflicting evidence"]}),
        lambda c: setattr(c, "proposed_fields", {**c.proposed_fields, "title": "Different title"}),
        lambda c: setattr(c, "proposed_fields", {**c.proposed_fields, "year": "bad"}),
        lambda c: setattr(c, "verification_status", "needs_review"),
        lambda c: setattr(c, "relationship_type", "possible_update"),
    ]
    for index, change in enumerate(changes):
        folder = tmp_path / str(index)
        folder.mkdir()
        engine, factory, bot, cid = setup(folder)
        with factory.begin() as session:
            c = session.get(Candidate, cid)
            change(c)
        r = do(factory, 1, 123, f"mcad:a:start:{cid}")
        assert "web admin panel" in r.text
        assert count(factory, Incident) == 0
        engine.dispose()


def test_existing_source_and_same_title_block_publication(tmp_path):
    for index, same_source in enumerate([True, False]):
        folder = tmp_path / str(index)
        folder.mkdir()
        engine, factory, bot, cid = setup(folder)
        with factory.begin() as session:
            c = session.get(Candidate, cid)
            incident = Incident(title="Other record" if same_source else c.title, year=2020 if same_source else 2026,
                                source_url="https://another.invalid/mcad-second-source" if same_source else "https://third.invalid/record",
                                status="published")
            session.add(incident)
        start = do(factory, 1, 123, f"mcad:a:start:{cid}")
        final = do(factory, 2, 123, confirm_markup(start))
        assert "existing incident" in final.text
        assert count(factory, Incident) == 1
        with factory() as session:
            assert session.get(Candidate, cid).status == "pending"
        engine.dispose()


def test_stale_candidate_rejected_via_web_cannot_publish(tmp_path):
    engine, factory, bot, cid = setup(tmp_path)
    start = do(factory, 1, 123, f"mcad:a:start:{cid}")
    with factory.begin() as session:
        c = session.get(Candidate, cid)
        c.status, c.rejection_reason = "rejected", "duplicate"
    final = do(factory, 2, 123, confirm_markup(start))
    assert "no longer awaiting" in final.text
    assert count(factory, Incident) == 0
    engine.dispose()


def test_cancel_and_unlinked_callbacks_cannot_publish(tmp_path):
    engine, factory, bot, cid = setup(tmp_path)
    start = do(factory, 1, 123, f"mcad:a:start:{cid}")
    assert "cancelled" in do(factory, 2, 123, f"mcad:a:cancel:{cid}").text
    assert "expired or invalid" in do(factory, 3, 123, confirm_markup(start)).text
    assert count(factory, Incident) == 0
    alien = str(uuid4())
    reply = do(factory, 4, 123, f"mcad:a:start:{alien}")
    assert "not delivered" in reply.text
    assert count(factory, Incident) == 0
    engine.dispose()
