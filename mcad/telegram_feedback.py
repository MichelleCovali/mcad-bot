"""Record feedback only for a delivered Telegram notification."""

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint, case, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Candidate, CandidateDelivery, CandidateNotification, Delivery, Incident, OutboxEvent, utcnow, uuid


FEEDBACK_OPTIONS = {
    "r": ("relevant", "Relevant"),
    "d": ("duplicate", "Duplicate"),
    "o": ("old_news", "Old news"),
    "m": ("not_maritime", "Not maritime-related"),
    "i": ("incorrect_information", "Incorrect information"),
}
FEEDBACK_REASONS = [reason for reason, _ in FEEDBACK_OPTIONS.values()]
CALLBACK_PATTERN = re.compile(r"mcad:([cp]):([0-9a-f-]{36}):([rdomi])\Z")
CHAT_TYPES = {"private", "group", "supergroup", "channel"}


class TelegramFeedback(Base):
    __tablename__ = "telegram_feedback"
    __table_args__ = (UniqueConstraint("delivery_kind", "delivery_id", "actor_id", name="uq_telegram_feedback_actor"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    delivery_kind: Mapped[str] = mapped_column(String(1))
    delivery_id: Mapped[str] = mapped_column(String(36))
    actor_id: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    review_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    reviewed_at: Mapped[datetime | None]
    editorial_drafted_at: Mapped[datetime | None]


@dataclass(frozen=True)
class CallbackAnswer:
    query_id: str
    text: str
    # A moderation menu can replace the keyboard of the exact delivered draft.
    # None preserves existing answerCallbackQuery-only behavior.
    chat_id: str | None = None
    message_id: str | None = None
    markup: dict | None = None


def feedback_markup(delivery_kind, delivery_id):
    buttons = {code: {"text": label, "callback_data": f"mcad:{delivery_kind}:{delivery_id}:{code}"}
               for code, (_, label) in FEEDBACK_OPTIONS.items()}
    return {"inline_keyboard": [[buttons["r"], buttons["d"], buttons["o"]], [buttons["m"], buttons["i"]]]}


def send_notification(client, chat_id, text, kind, delivery_id):
    sender = getattr(client, "send_with_feedback", None)
    if callable(sender) and re.fullmatch(r"-?\d{1,20}", chat_id):
        return sender(chat_id, text, feedback_markup(kind, delivery_id))
    return client.send(chat_id, text)


def _telegram_id(value):
    return str(value) if type(value) is int and 0 < abs(value) < 10**20 else None


def _identity(message, actor):
    chat = message.get("chat") or {}
    chat_type = chat.get("type")
    chat_id, actor_id = _telegram_id(chat.get("id")), _telegram_id(actor.get("id"))
    if not chat_id or not actor_id or actor.get("is_bot") or chat_type not in CHAT_TYPES:
        return None
    if chat_type == "private" and chat_id != actor_id:
        return None
    return chat_id, actor_id, chat_type


def _subject(session, kind, delivery):
    if kind == "c":
        notification = session.get(CandidateNotification, delivery.event_id)
        candidate = session.get(Candidate, notification.candidate_id) if notification else None
        if candidate:
            return notification.payload.get("title", candidate.title), notification.payload.get("sourceUrl") or candidate.source_url, candidate.summary
    else:
        event = session.get(OutboxEvent, delivery.event_id)
        incident = session.get(Incident, event.incident_id) if event else None
        if incident:
            return event.payload.get("title", incident.title), event.payload.get("sourceUrl") or incident.source_url, incident.summary or incident.description
    return None


def record_callback(session, callback):
    query_id, data = callback.get("id"), callback.get("data")
    if not isinstance(query_id, str) or not 1 <= len(query_id) <= 128:
        return None
    unavailable = CallbackAnswer(query_id, "Feedback is unavailable for this message.")
    match = CALLBACK_PATTERN.fullmatch(data) if isinstance(data, str) else None
    message = callback.get("message") or {}
    identity = _identity(message, callback.get("from") or {})
    message_id = _telegram_id(message.get("message_id"))
    if not match or not identity or not message_id:
        return unavailable
    kind, delivery_id, choice = match.groups()
    chat_id, actor_id, chat_type = identity
    model = CandidateDelivery if kind == "c" else Delivery
    delivery = session.get(model, delivery_id)
    if not delivery or delivery.status != "sent" or delivery.chat_id != chat_id or delivery.telegram_message_id != message_id:
        return unavailable
    subject = _subject(session, kind, delivery)
    if not subject:
        return unavailable
    title, source_url, evidence = subject
    reason = FEEDBACK_OPTIONS[choice][0]
    insert = postgres_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    stmt = insert(TelegramFeedback).values(id=uuid(), delivery_kind=kind, delivery_id=delivery_id,
        actor_id=actor_id, title=title[:500], source_url=source_url, evidence=(evidence or "")[:3000],
        reason=reason, comment="", status="pending", review_notes="", created_at=utcnow())
    saved = session.scalar(stmt.on_conflict_do_update(
        index_elements=["delivery_kind", "delivery_id", "actor_id"],
        set_={"reason": reason, "comment": case((TelegramFeedback.reason == reason, TelegramFeedback.comment), else_="")},
        where=TelegramFeedback.status == "pending",
    ).returning(TelegramFeedback.id))
    if not saved:
        return CallbackAnswer(query_id, "This feedback has already been reviewed.")
    note = " Reply to this alert with /feedback and your comment to add context." if chat_type != "channel" else ""
    return CallbackAnswer(query_id, "Feedback saved." + note)


def record_comment(session, message):
    identity = _identity(message, message.get("from") or {})
    replied_to = message.get("reply_to_message") or {}
    original_id = _telegram_id(replied_to.get("message_id"))
    if not identity or not original_id:
        return None
    chat_id, actor_id, _ = identity
    note = message["text"].split(maxsplit=1)
    if len(note) != 2 or not note[1].strip():
        return chat_id, "Add a short comment after /feedback."
    note = "".join(char for char in note[1] if char in "\n\t" or ord(char) >= 32).strip()[:1000]
    if not note:
        return chat_id, "Add a short comment after /feedback."
    for kind, model in (("c", CandidateDelivery), ("p", Delivery)):
        delivery_id = session.scalar(select(model.id).where(model.chat_id == chat_id,
            model.telegram_message_id == original_id, model.status == "sent"))
        if not delivery_id:
            continue
        saved = session.scalar(update(TelegramFeedback).where(TelegramFeedback.delivery_kind == kind,
            TelegramFeedback.delivery_id == delivery_id, TelegramFeedback.actor_id == actor_id,
            TelegramFeedback.status == "pending").values(comment=note).returning(TelegramFeedback.id))
        if saved:
            return chat_id, "Comment saved."
        return chat_id, "Choose a feedback button on that alert first. Comments can be updated until review."
    return None
