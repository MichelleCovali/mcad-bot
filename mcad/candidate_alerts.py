"""Durable opt-in alerts for new AI reports, kept separate from publication.

Enqueueing is transactional and never calls Telegram. Delivery remains at least
once: Telegram has no idempotency key for a crash after send but before commit.
"""
from datetime import timedelta
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import Candidate, CandidateDelivery, CandidateNotification, Incident, Subscriber, utcnow
from .service import safe_url
from .telegram_feedback import send_notification


def create_candidate_notification(session, candidate, review_chat_id=""):
    """Queue a supported AI draft for the review chat or legacy subscribers.

    The caller owns the transaction containing the new candidate. Repeating this
    function never creates another event, adds new recipients, or sends HTTP.
    """
    session.flush()
    if candidate.origin != "ai" or candidate.verification_status != "supported" or candidate.status != "pending" or candidate.baseline:
        return None
    source_url = safe_url(candidate.source_url)
    if not source_url:
        return None
    now, event_id = utcnow(), str(uuid4())
    insert = postgres_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    payload = {"candidateId": candidate.id, "title": candidate.title,
               "sourceUrl": source_url, "verificationStatus": "supported", "reviewRequired": True}
    if review_chat_id:
        payload["reviewChatId"] = review_chat_id
    inserted_id = session.scalar(insert(CandidateNotification).values(
        id=event_id, candidate_id=candidate.id, created_at=now,
        payload=payload,
    ).on_conflict_do_nothing(index_elements=["candidate_id"]).returning(CandidateNotification.id))
    event = session.scalar(select(CandidateNotification).where(CandidateNotification.candidate_id == candidate.id))
    # psycopg can report an unknown (-1) rowcount for INSERT; RETURNING
    # distinguishes a new event from a conflict on both supported databases.
    if inserted_id is None:
        return event
    if review_chat_id:
        # The delivery table references Subscriber. The internal review destination
        # does not acquire a public subscription just by receiving drafts.
        session.execute(insert(Subscriber).values(chat_id=review_chat_id, source="review",
            active=False, subscribed_at=now, updated_at=now).on_conflict_do_nothing(index_elements=["chat_id"]))
        destinations = [review_chat_id]
    else:
        destinations = session.scalars(select(Subscriber.chat_id).where(
            Subscriber.active.is_(True), Subscriber.subscribed_at <= event.created_at,
        ))
    for chat_id in destinations:
        session.add(CandidateDelivery(event_id=event.id, chat_id=chat_id, next_attempt_at=event.created_at))
    session.flush()
    return event


def claim_candidate_delivery(factory, owner, now=None):
    now = now or utcnow()
    eligible = and_(CandidateDelivery.status.in_(["pending", "retry", "sending"]),
        CandidateDelivery.next_attempt_at <= now,
        or_(CandidateDelivery.lease_until.is_(None), CandidateDelivery.lease_until <= now))
    with factory.begin() as session:
        query = select(CandidateDelivery.id).where(eligible).order_by(
            CandidateDelivery.next_attempt_at, CandidateDelivery.id).limit(1)
        if session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        delivery_id = session.scalar(query)
        if delivery_id is None:
            return None
        changed = session.execute(update(CandidateDelivery).where(
            CandidateDelivery.id == delivery_id, eligible,
        ).values(status="sending", lease_owner=owner,
            lease_until=now + timedelta(seconds=90), attempts=CandidateDelivery.attempts + 1))
        return delivery_id if changed.rowcount == 1 else None


def _plain(value):
    return "".join(char for char in str(value or "") if char in "\n\t" or ord(char) >= 32)


def candidate_notification_text(payload, base_url="", message_format="link"):
    if message_format not in {"link", "rich"}:
        raise ValueError("TELEGRAM_MESSAGE_FORMAT must be link or rich.")
    source_url = safe_url(payload.get("sourceUrl"))
    review_url = None
    if base_url:
        review_url = f"{safe_url(base_url).rstrip('/')}/admin?candidate={quote(str(payload['candidateId']), safe='')}"
    if message_format == "link":
        return source_url or review_url or "New AI report — review required"
    lines = ["New AI report — review required", "", _plain(payload.get("title"))[:1200]]
    if source_url:
        lines.extend(["", source_url])
    if review_url:
        lines.extend(["", "Review: " + review_url])
    return "\n".join(lines)[:4096]


def deliver_candidate_one(factory, client, base_url="", owner=None, now=None, message_format="link", *,
                          review_chat_id="", classification_enabled=False):
    if message_format not in {"link", "rich"}:
        raise ValueError("TELEGRAM_MESSAGE_FORMAT must be link or rich.")
    owner, now = owner or str(uuid4()), now or utcnow()
    delivery_id = claim_candidate_delivery(factory, owner, now)
    if delivery_id is None:
        return False
    # Lock order matches approval: Candidate first, then its delivery. Holding
    # the candidate row through the bounded HTTP call makes approval see the
    # committed sent status before it decides whether to send publication alerts.
    with factory.begin() as session:
        candidate_id = session.scalar(select(CandidateNotification.candidate_id).join(
            CandidateDelivery, CandidateDelivery.event_id == CandidateNotification.id,
        ).where(CandidateDelivery.id == delivery_id, CandidateDelivery.lease_owner == owner))
        if candidate_id is None:
            return True
        if session.bind.dialect.name == "sqlite":
            # SQLite ignores FOR UPDATE; take its writer lock before the read.
            session.execute(update(Candidate).where(Candidate.id == candidate_id).values(status=Candidate.status))
        candidate = session.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
        delivery = session.scalar(select(CandidateDelivery).where(
            CandidateDelivery.id == delivery_id, CandidateDelivery.lease_owner == owner,
            CandidateDelivery.status == "sending",
        ).with_for_update().execution_options(populate_existing=True))
        if delivery is None:
            return True
        event = session.get(CandidateNotification, delivery.event_id)
        subscriber = session.get(Subscriber, delivery.chat_id)
        editorial_chat = event.payload.get("reviewChatId")
        if editorial_chat:
            active = editorial_chat == review_chat_id == delivery.chat_id
        else:
            active = bool(not review_chat_id and subscriber and subscriber.active and subscriber.subscribed_at <= event.created_at)
        reviewable = bool(candidate and candidate.status == "pending" and candidate.origin == "ai"
                          and candidate.verification_status == "supported" and not candidate.baseline)
        delivery.lease_until, delivery.lease_owner = None, None
        if not active or not reviewable:
            delivery.status = "cancelled"
            if not reviewable:
                delivery.last_error = "candidate_not_pending"
            elif editorial_chat or review_chat_id:
                delivery.last_error = "review_chat_changed"
            else:
                delivery.last_error = "unsubscribed"
            return True
        if editorial_chat:
            from .telegram_review import review_notification

            target_id = (candidate.evidence or {}).get("suggestedIncidentId")
            target = session.get(Incident, target_id) if target_id else None
            text, markup = review_notification(candidate, delivery.id, base_url,
                target.title if target else None, classification_enabled=classification_enabled)
            result = client.send_with_review(delivery.chat_id, text, markup)
        else:
            result = send_notification(client, delivery.chat_id,
                candidate_notification_text(event.payload, base_url, message_format), "c", delivery.id)
        if result.ok:
            delivery.status, delivery.sent_at = "sent", now
            delivery.telegram_message_id, delivery.last_error = result.message_id, None
        elif result.error_code in {400, 403}:
            delivery.status, delivery.last_error = "failed", f"telegram_{result.error_code}"
            if result.error_code == 403 and not editorial_chat:
                subscriber.active = False
        else:
            delivery.status = "retry"
            delay = max(1, min(result.retry_after, 86400)) if result.error_code == 429 and result.retry_after else min(3600, 2 ** min(delivery.attempts, 10) * 5)
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.last_error = f"telegram_{result.error_code}" if isinstance(result.error_code, int) else "network_or_invalid_response"
    return True
