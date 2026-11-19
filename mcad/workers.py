"""Database-backed feed review queue, subscriptions, polling and notification outbox.

No network work runs on module import. Each worker has its own DB sessions and
holds a renewable lease, including across overlapping hosted deployments.
"""
import logging
import re
import time
import threading
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4
from urllib.parse import quote

import requests
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import (Candidate, CandidateDelivery, CandidateNotification, Delivery, Incident, IncidentSource,
                     OutboxEvent, ProcessedUpdate, Subscriber, WorkerLease, WorkerState, utcnow)
from .service import safe_url, upsert_incident
from .candidate_alerts import deliver_candidate_one
from .telegram_review import (CallbackReply, REJECTION_REASONS, is_authorized_review_callback,
                              parse_preview_action, parse_rejection_action, parse_approval_action,
                              valid_approval_confirmation, approval_confirmation_keyboard,
                              verified_evidence_sources, telegram_approval_payload, approval_review_message,
                              approval_session_key,
                              rejection_confirmation_keyboard, rejection_reason_keyboard,
                              rejection_note_session_key, normalize_rejection_note,
                              classification_session_key, parse_classification_action,
                              classification_fields_keyboard, classification_choices_keyboard,
                              classification_confirmation_keyboard, CLASSIFICATION_CHOICES,
                              CLASSIFICATION_FIELDS, new_classification_nonce,
                              approval_fields_fingerprint)


logger = logging.getLogger("mcad.workers")


def insert_ignore(session, model, values, key_fields):
    insert = postgres_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    return session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=key_fields))


def claim_lease(factory, name, owner, seconds=90, now=None):
    now = now or utcnow()
    with factory.begin() as session:
        insert_ignore(session, WorkerLease, {"name": name, "owner": owner, "expires_at": now}, ["name"])
        result = session.execute(update(WorkerLease).where(WorkerLease.name == name, or_(WorkerLease.owner == owner, WorkerLease.expires_at <= now)).values(owner=owner, expires_at=now + timedelta(seconds=seconds)))
        return result.rowcount == 1


def release_lease(factory, name, owner):
    with factory.begin() as session:
        session.execute(delete(WorkerLease).where(WorkerLease.name == name, WorkerLease.owner == owner))


def seed_subscriber(session, chat_id):
    chat_id = str(chat_id).strip()
    if not re.fullmatch(r"-?\d{1,20}|@[A-Za-z0-9_]{5,32}", chat_id):
        raise ValueError("TELEGRAM_CHAT_ID must be a Telegram chat identifier")
    # Preserve an explicit unsubscribe across deploys; do not reactivate on startup.
    insert_ignore(session, Subscriber, {"chat_id": chat_id, "active": True, "source": "configured", "subscribed_at": utcnow(), "updated_at": utcnow()}, ["chat_id"])


def set_subscription(session, chat_id, active):
    chat_id = str(chat_id)
    subscriber = session.get(Subscriber, chat_id)
    if subscriber is None:
        subscriber = Subscriber(chat_id=chat_id, active=active)
        session.add(subscriber)
    elif active and not subscriber.active:
        subscriber.active, subscriber.subscribed_at = True, utcnow()
    else:
        subscriber.active = active
    return subscriber


def clean_text(value):
    return "".join(char for char in str(value or "") if char in "\n\t" or ord(char) >= 32)


def notification_text(payload, base_url=""):
    lines = ["New verified MCAD incident", "", clean_text(payload.get("title"))[:1200]]
    context = [str(payload["year"]) if payload.get("year") else "", clean_text(payload.get("incidentCountry"))[:300]]
    if any(context):
        lines.extend(["", " · ".join(x for x in context if x)])
    if base_url:
        url = safe_url(base_url)
        lines.extend(["", f"{url.rstrip('/')}/?incident={quote(str(payload['id']), safe='')}"])
    elif payload.get("sourceUrl"):
        lines.extend(["", safe_url(payload["sourceUrl"]) or ""])
    return "\n".join(lines)[:4096]


@dataclass(frozen=True)
class MessageReply:
    chat_id: str
    text: str
    reply_markup: dict | None = None


@dataclass
class SendResult:
    ok: bool
    error_code: int | None = None
    retry_after: int | None = None
    message_id: str | None = None


class TelegramClient:
    def __init__(self, token):
        self.token = token

    def send(self, chat_id, text, reply_markup=None):
        body = {"chat_id": chat_id, "text": clean_text(text)[:4096], "link_preview_options": {"is_disabled": True}}
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json=body,
                timeout=20,
            )
            data = response.json()
            if not isinstance(data, dict):
                return SendResult(False)
            if data.get("ok"):
                return SendResult(True, message_id=str(data.get("result", {}).get("message_id", "")))
            retry_after = data.get("parameters", {}).get("retry_after")
            return SendResult(False, error_code=data.get("error_code", response.status_code), retry_after=retry_after if isinstance(retry_after, int) else None)
        except (requests.RequestException, ValueError):
            # Never log the request URL: it contains the bot token.
            return SendResult(False)

    def answer_callback(self, callback_id: str, message: str) -> None:
        """Acknowledge a button tap; never expose the Telegram token in logs."""
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": clean_text(message)[:200], "show_alert": True},
                timeout=10,
            )
        except requests.RequestException:
            logger.warning("MCAD Telegram callback acknowledgment failed.")

    def get_updates(self, offset):
        try:
            response = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params={"offset": offset, "timeout": 25, "allowed_updates": '["message","callback_query"]'}, timeout=35)
            data = response.json()
            return data if isinstance(data, dict) else {"ok": False}
        except (requests.RequestException, ValueError):
            return {"ok": False}


def claim_delivery(factory, owner, now=None):
    now = now or utcnow()
    eligible = and_(Delivery.status.in_(["pending", "retry", "sending"]), Delivery.next_attempt_at <= now, or_(Delivery.lease_until.is_(None), Delivery.lease_until <= now))
    with factory.begin() as session:
        query = select(Delivery.id).where(eligible).order_by(Delivery.next_attempt_at, Delivery.id).limit(1)
        if session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        delivery_id = session.scalar(query)
        if delivery_id is None:
            return None
        result = session.execute(update(Delivery).where(Delivery.id == delivery_id, eligible).values(status="sending", lease_owner=owner, lease_until=now + timedelta(seconds=90), attempts=Delivery.attempts + 1))
        return delivery_id if result.rowcount == 1 else None


def deliver_one(factory, client, base_url="", owner=None, now=None, message_format="link"):
    if message_format not in {"link", "rich"}:
        raise ValueError("TELEGRAM_MESSAGE_FORMAT must be link or rich.")
    owner, now = owner or str(uuid4()), now or utcnow()
    delivery_id = claim_delivery(factory, owner, now)
    if not delivery_id:
        return False
    with factory() as session:
        delivery = session.get(Delivery, delivery_id)
        subscriber = session.get(Subscriber, delivery.chat_id)
        event = session.get(OutboxEvent, delivery.event_id)
        active = bool(subscriber and subscriber.active and subscriber.subscribed_at <= event.created_at)
        chat_id, payload = delivery.chat_id, event.payload
    result = None
    if active:
        # The original bot sends only the article URL. Keep that observable
        # format while the database and review workflow are upgraded.
        if message_format == "rich":
            text = notification_text(payload, base_url)
        elif payload.get("sourceUrl"):
            text = safe_url(payload["sourceUrl"])
        elif base_url:
            text = f"{safe_url(base_url).rstrip('/')}/?incident={quote(str(payload['id']), safe='')}"
        else:
            text = notification_text(payload)
        result = client.send(chat_id, text)
    with factory.begin() as session:
        delivery = session.scalar(select(Delivery).where(Delivery.id == delivery_id, Delivery.lease_owner == owner).with_for_update())
        if delivery is None:
            return True
        delivery.lease_until, delivery.lease_owner = None, None
        if not active:
            delivery.status = "cancelled"
        elif result.ok:
            delivery.status, delivery.sent_at = "sent", now
            delivery.telegram_message_id, delivery.last_error = result.message_id, None
        elif result.error_code in {400, 403}:
            delivery.status, delivery.last_error = "failed", f"telegram_{result.error_code}"
            if result.error_code == 403:
                session.get(Subscriber, chat_id).active = False
        else:
            delivery.status = "retry"
            delay = max(1, min(result.retry_after, 86400)) if result.error_code == 429 and result.retry_after else min(3600, 2 ** min(delivery.attempts, 10) * 5)
            delivery.next_attempt_at = now + timedelta(seconds=delay)
            delivery.last_error = f"telegram_{result.error_code}" if isinstance(result.error_code, int) else "network_or_invalid_response"
    return True


HELP = "MCAD incident alerts\n\n/subscribe — receive new source-checked AI reports and published incidents\n/unsubscribe — stop alerts\n/latest — latest verified database incident\n/map — open the incident map\n/help — show commands\n\nAI reports require human review. Historical imports do not send alerts."


def process_update(factory, update_data, base_url="", *, reviewer_ids=frozenset(),
                   review_buttons_enabled=False, rejection_actions_enabled=False,
                   approval_actions_enabled=False, approval_secret="",
                   rejection_notes_enabled=False, classification_actions_enabled=False):
    """Persist command effects and offset before replying; replayed updates are silent."""
    update_id = update_data.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        return None
    with factory.begin() as session:
        if session.get(ProcessedUpdate, update_id):
            return None
        session.add(ProcessedUpdate(update_id=update_id))
        state = session.get(WorkerState, "telegram_offset")
        next_offset = max(update_id + 1, (state.value.get("offset", 0) if state else 0))
        if state:
            state.value = {"offset": next_offset}
        else:
            session.add(WorkerState(name="telegram_offset", value={"offset": next_offset}))
        callback = update_data.get("callback_query")
        if isinstance(callback, dict):
            callback_id = callback.get("id")
            if not isinstance(callback_id, str) or not callback_id:
                return None
            if not review_buttons_enabled:
                return CallbackReply(callback_id, "Telegram review is not enabled. Use the MCAD admin panel.")
            if not is_authorized_review_callback(callback, reviewer_ids):
                return CallbackReply(callback_id, "Reviewer access is restricted.")
            # Stage 4 adds separately gated, expiring two-step PUBLIC approval.
            # Stage 3 structured rejection continues unchanged.
            approval = parse_approval_action(callback.get("data"))
            if approval is not None:
                if not approval_actions_enabled or not approval_secret:
                    return CallbackReply(callback_id, "Telegram approval is disabled. Use the MCAD admin panel.")
                stage, candidate_id, tick, signature = approval
                reviewer_id = callback["from"]["id"]
                chat_id = str(reviewer_id)
                delivered = session.scalar(select(CandidateDelivery.id).join(
                    CandidateNotification, CandidateDelivery.event_id == CandidateNotification.id
                ).join(Subscriber, CandidateDelivery.chat_id == Subscriber.chat_id).where(
                    CandidateNotification.candidate_id == candidate_id,
                    CandidateDelivery.chat_id == chat_id,
                    CandidateDelivery.status == "sent",
                    Subscriber.active.is_(True),
                ).limit(1))
                if not delivered:
                    return CallbackReply(callback_id, "This report was not delivered to your private subscription.")
                state_key = approval_session_key(candidate_id, reviewer_id)
                approval_state = session.get(WorkerState, state_key)
                if stage == "cancel":
                    if approval_state is not None:
                        session.delete(approval_state)
                    return CallbackReply(callback_id, "Publication cancelled. No changes saved.")
                # A signed callback alone does not authorize publication: it
                # must still match a saved, uncancelled review confirmation.
                if stage == "confirm" and (not valid_approval_confirmation(
                    candidate_id, reviewer_id, approval_secret, tick, signature
                ) or approval_state is None or
                    approval_state.value.get("callbackData") != callback.get("data")):
                    return CallbackReply(callback_id, "Approval confirmation expired or invalid. Start review again.")
                if session.bind.dialect.name == "sqlite" and stage == "confirm":
                    # Acquire a SQLite writer lock: SELECT FOR UPDATE is a no-op.
                    session.execute(update(Candidate).where(Candidate.id == candidate_id).values(status=Candidate.status))
                candidate = session.scalar(select(Candidate).where(
                    Candidate.id == candidate_id
                ).with_for_update())
                if candidate is None or candidate.status != "pending":
                    return CallbackReply(callback_id, "This report is no longer awaiting review.")
                if stage == "confirm" and approval_state.value.get("fieldsFingerprint") != approval_fields_fingerprint(candidate):
                    return CallbackReply(callback_id, "The report details changed. Review the latest details before confirming.")
                approved_payload = telegram_approval_payload(candidate, reviewer_id)
                if approved_payload is None:
                    return CallbackReply(callback_id, "This report needs detailed verification in the web admin panel.")
                source_items = verified_evidence_sources(candidate)
                if stage == "start":
                    keyboard = approval_confirmation_keyboard(candidate_id, reviewer_id, approval_secret)
                    confirm_data = keyboard["inline_keyboard"][0][0]["callback_data"]
                    if approval_state is None:
                        session.add(WorkerState(name=state_key, value={"callbackData": confirm_data,
                                                                        "fieldsFingerprint": approval_fields_fingerprint(candidate)}))
                    else:
                        approval_state.value = {"callbackData": confirm_data,
                                                "fieldsFingerprint": approval_fields_fingerprint(candidate)}
                    return CallbackReply(callback_id, "Check the sources before confirming publication.",
                                         chat_id, approval_review_message(candidate, source_items), keyboard)
                # A stale relationship/matching decision is not permission to
                # overwrite an already-published record. Require web review.
                source_urls = approved_payload["referenceUrls"]
                collisions = session.scalar(select(Incident.id).where(or_(
                    Incident.source_url.in_(source_urls),
                    and_(func.lower(Incident.title) == approved_payload["title"].casefold(),
                         Incident.year == approved_payload.get("year")),
                )).limit(1))
                reference_collision = session.scalar(select(IncidentSource.incident_id).where(
                    IncidentSource.url.in_(source_urls)
                ).limit(1))
                if collisions or reference_collision:
                    return CallbackReply(callback_id, "An existing incident may match. Review it in the MCAD admin panel.")
                # Same conditional transition used for web approval. For Postgres
                # the candidate row is also held FOR UPDATE through the write.
                transitioned = session.execute(update(Candidate).where(
                    Candidate.id == candidate_id, Candidate.status == "pending"
                ).values(status="approved").execution_options(synchronize_session=False))
                if transitioned.rowcount != 1:
                    return CallbackReply(callback_id, "This report was already reviewed. No changes saved.")
                incident, created = upsert_incident(session, approved_payload, notify=True,
                                                    reviewed_candidate_id=candidate.id)
                if not created:
                    # A newly discovered match during the insert is an error, not
                    # grounds to silently change an existing publication.
                    raise RuntimeError("Telegram approval matched an existing incident unexpectedly")
                candidate.status, candidate.incident_id = "approved", incident.id
                evidence = dict(candidate.evidence or {})
                history = list(evidence.get("telegramReviewHistory", []))
                history.append({"decision": "approved", "reviewerId": chat_id,
                                "reviewedAt": utcnow().isoformat() + "Z", "incidentId": incident.id})
                evidence["telegramReviewHistory"] = history
                candidate.evidence = evidence
                session.delete(approval_state)
                return CallbackReply(callback_id, "Published to MCAD. Incident ID: " + incident.id)
            classification = parse_classification_action(callback.get("data"))
            if classification is not None:
                if not classification_actions_enabled:
                    return CallbackReply(callback_id, "Classification corrections are disabled. Use the MCAD admin panel.")
                stage, selection, candidate_id = classification
                reviewer_id = callback["from"]["id"]
                chat_id = str(reviewer_id)
                state_key = classification_session_key(reviewer_id)
                draft_row = session.get(WorkerState, state_key)
                if stage == "cancel":
                    if draft_row is not None:
                        session.delete(draft_row)
                    return CallbackReply(callback_id, "Classification correction cancelled; nothing changed.")
                delivered = session.scalar(select(CandidateDelivery.id).join(
                    CandidateNotification, CandidateDelivery.event_id == CandidateNotification.id
                ).join(Subscriber, CandidateDelivery.chat_id == Subscriber.chat_id).where(
                    CandidateNotification.candidate_id == candidate_id,
                    CandidateDelivery.chat_id == chat_id,
                    CandidateDelivery.status == "sent", Subscriber.active.is_(True),
                ).limit(1))
                if not delivered:
                    return CallbackReply(callback_id, "This report was not delivered to your private subscription.")
                if stage == "save" and session.bind.dialect.name == "sqlite":
                    session.execute(update(Candidate).where(Candidate.id == candidate_id).values(status=Candidate.status))
                candidate = session.scalar(select(Candidate).where(Candidate.id == candidate_id).with_for_update())
                if (candidate is None or candidate.status != "pending" or candidate.baseline or
                    candidate.origin != "ai" or candidate.verification_status != "supported" or
                    candidate.relationship_type not in {"unclassified", "new_incident"} or
                    candidate.related_incident_id is not None):
                    return CallbackReply(callback_id, "This candidate needs review in the web admin panel.")
                if stage == "start":
                    value = {"candidateId": candidate_id, "stage": "select", "expiresAt": time.time() + 900}
                    if draft_row is None:
                        session.add(WorkerState(name=state_key, value=value))
                    else:
                        draft_row.value = value
                    return CallbackReply(callback_id, "Select a field to correct.", chat_id,
                                         "Choose the classification field you want to correct. Nothing is published.",
                                         classification_fields_keyboard(candidate_id))
                draft = dict(draft_row.value or {}) if draft_row else {}
                if (draft.get("candidateId") != candidate_id or
                    not isinstance(draft.get("expiresAt"), (int, float)) or
                    draft["expiresAt"] < time.time()):
                    if draft_row is not None:
                        session.delete(draft_row)
                    return CallbackReply(callback_id, "Correction expired. Start again from the notification.")
                if stage == "field":
                    if draft.get("stage") not in {"select", "choices"}:
                        return CallbackReply(callback_id, "Start the correction again.")
                    draft_row.value = {**draft, "stage": "choices", "fieldKey": selection}
                    return CallbackReply(callback_id, "Choose a correction.", chat_id,
                                         "Select the corrected value (other categories can be edited on the website).",
                                         classification_choices_keyboard(candidate_id, selection))
                if stage == "pick":
                    field_key, choice_key = selection
                    if draft.get("stage") != "choices" or draft.get("fieldKey") != field_key:
                        return CallbackReply(callback_id, "Choose a field before selecting its value.")
                    field = CLASSIFICATION_FIELDS[field_key]
                    original = (candidate.proposed_fields or {}).get(field)
                    replacement = CLASSIFICATION_CHOICES[field_key][choice_key]
                    if original == replacement:
                        return CallbackReply(callback_id, "This value is already selected. Choose a different value.")
                    nonce = new_classification_nonce()
                    draft_row.value = {**draft, "stage": "confirm", "field": field,
                                       "oldValue": original, "newValue": replacement,
                                       "nonce": nonce, "expiresAt": time.time() + 900}
                    text = f"Confirm {field}: {str(original or 'not specified')[:120]} -> {replacement}. This does NOT publish the incident."
                    return CallbackReply(callback_id, "Review the proposed correction.", chat_id, text,
                                         classification_confirmation_keyboard(candidate_id, nonce))
                if stage != "save" or draft.get("stage") != "confirm" or draft.get("nonce") != selection:
                    return CallbackReply(callback_id, "Invalid or outdated confirmation. Start again.")
                field = draft.get("field")
                new_value = draft.get("newValue")
                old_value = (candidate.proposed_fields or {}).get(field)
                if (field not in CLASSIFICATION_FIELDS.values() or
                    new_value not in CLASSIFICATION_CHOICES["v" if field == "victimType" else "m"].values() or
                    old_value != draft.get("oldValue")):
                    return CallbackReply(callback_id, "The classification changed. Review the current candidate again.")
                proposed = dict(candidate.proposed_fields or {})
                proposed[field] = new_value
                candidate.proposed_fields = proposed
                evidence = dict(candidate.evidence or {})
                history = list(evidence.get("classificationReviews", []))
                history.append({"source": "telegram", "reviewerId": chat_id,
                                "reviewedAt": utcnow().isoformat() + "Z", "field": field,
                                "previousValue": old_value, "newValue": new_value})
                evidence["classificationReviews"] = history
                candidate.evidence = evidence
                session.delete(draft_row)
                return CallbackReply(callback_id, "Classification saved. The candidate is still pending and NOT published.")
            rejection = parse_rejection_action(callback.get("data"))
            if rejection is not None:
                if not rejection_actions_enabled:
                    return CallbackReply(callback_id, "Telegram rejection is disabled. Use the MCAD admin panel.")
                stage, reason_key, candidate_id = rejection
                reviewer_id = callback["from"]["id"]
                chat_id = str(reviewer_id)
                note_state = (session.get(WorkerState, rejection_note_session_key(reviewer_id))
                              if rejection_notes_enabled else None)
                expired_note = False
                if note_state is not None and (note_state.value or {}).get("expiresAt", 0) < time.time():
                    expired_note = True
                    session.delete(note_state)
                    note_state = None
                # A reviewer must actually have received this specific candidate
                # alert in their private subscription; knowing a UUID is not enough.
                delivered = session.scalar(select(CandidateDelivery.id).join(
                    CandidateNotification, CandidateDelivery.event_id == CandidateNotification.id
                ).join(Subscriber, CandidateDelivery.chat_id == Subscriber.chat_id).where(
                    CandidateNotification.candidate_id == candidate_id,
                    CandidateDelivery.chat_id == chat_id,
                    CandidateDelivery.status == "sent",
                    Subscriber.active.is_(True),
                ).limit(1))
                if not delivered:
                    return CallbackReply(callback_id, "This report was not delivered to your private subscription.")
                if stage == "cancel":
                    if note_state is not None and note_state.value.get("candidateId") == candidate_id:
                        session.delete(note_state)
                    return CallbackReply(callback_id, "Rejection cancelled. No changes saved.")
                candidate = session.scalar(select(Candidate).where(
                    Candidate.id == candidate_id
                ).with_for_update())
                if (candidate is None or candidate.status != "pending" or candidate.baseline
                        or candidate.origin != "ai" or candidate.verification_status != "supported"
                        or candidate.relationship_type not in {"unclassified", "new_incident"}
                        or candidate.related_incident_id is not None):
                    return CallbackReply(callback_id, "This candidate cannot be rejected from Telegram. Review in the admin panel.")
                if stage == "start":
                    # Starting over discards any earlier unsaved explanation.
                    if note_state is not None:
                        session.delete(note_state)
                    return CallbackReply(callback_id, "Select a rejection reason.", chat_id,
                                         "Choose a rejection reason for: " + clean_text(candidate.title)[:240],
                                         rejection_reason_keyboard(candidate_id))
                reason, label = REJECTION_REASONS[reason_key]
                if stage == "reason":
                    # A newly selected reason invalidates an older note draft.
                    if note_state is not None:
                        session.delete(note_state)
                    description = ("This saves the structured reason. Optionally add an explanation first."
                                   if rejection_notes_enabled else
                                   "This will save the reason. To add detailed notes, use the web admin panel.")
                    return CallbackReply(callback_id, "Confirm or cancel your decision.", chat_id,
                                         f"Reject '{clean_text(candidate.title)[:160]}' as {label}? " + description,
                                         rejection_confirmation_keyboard(candidate_id, reason_key,
                                                                         allow_note=rejection_notes_enabled))
                if stage == "note":
                    if not rejection_notes_enabled:
                        return CallbackReply(callback_id, "Telegram rejection explanations are disabled.")
                    data = {"candidateId": candidate_id, "reasonKey": reason_key,
                            "stage": "awaiting_text", "expiresAt": time.time() + 15 * 60}
                    if note_state is None:
                        session.add(WorkerState(name=rejection_note_session_key(reviewer_id), value=data))
                    else:
                        note_state.value = data
                    return CallbackReply(callback_id, "Reply to the bot's prompt with your explanation.", chat_id,
                                         "MCAD rejection explanation — reply to THIS message with your reason "
                                         "(maximum 1000 characters). You will confirm before it is saved. "
                                         "Use /cancel to discard.",
                                         {"force_reply": True, "input_field_placeholder": "Explain the rejection"})
                note = ""
                if stage == "final":
                    if not rejection_notes_enabled or expired_note or note_state is None:
                        return CallbackReply(callback_id, "Explanation expired or unavailable. Start review again.")
                    data = note_state.value or {}
                    if data.get("candidateId") != candidate_id or data.get("reasonKey") != reason_key:
                        return CallbackReply(callback_id, "A different rejection is in progress. Start review again.")
                    if data.get("stage") != "ready" or not isinstance(data.get("note"), str):
                        return CallbackReply(callback_id, "Enter your explanation first, or use Cancel.")
                    note = data["note"]
                elif note_state is not None or expired_note:
                    return CallbackReply(callback_id, "An explanation is in progress or expired. Use its latest confirmation, or Cancel.")
                # The state change is conditional, as in the admin API, to avoid
                # double reviews or overwriting an approval from another interface.
                changed = session.execute(update(Candidate).where(
                    Candidate.id == candidate_id, Candidate.status == "pending"
                ).values(status="rejected", rejection_reason=reason, rejection_notes=note)
                    .execution_options(synchronize_session=False))
                if changed.rowcount != 1:
                    return CallbackReply(callback_id, "This report was already reviewed. No changes saved.")
                # Preserve a minimal audit trail for the client. No bot-token,
                # message text or personal username is recorded.
                evidence = dict(candidate.evidence or {})
                history = list(evidence.get("telegramReviewHistory", []))
                history.append({"decision": "rejected", "reason": reason,
                                "reviewerId": chat_id, "reviewedAt": utcnow().isoformat() + "Z",
                                "hasExplanation": bool(note)})
                evidence["telegramReviewHistory"] = history
                candidate.evidence = evidence
                if note_state is not None:
                    session.delete(note_state)
                return CallbackReply(callback_id, "Rejected: " + label + ". Saved in the MCAD review queue.")
            action = parse_preview_action(callback.get("data"))
            if action is None:
                return CallbackReply(callback_id, "Unknown review action.")
            _, candidate_id = action
            candidate = session.get(Candidate, candidate_id)
            if candidate is None or candidate.status != "pending":
                return CallbackReply(callback_id, "This report is no longer awaiting review.")
            return CallbackReply(callback_id, "Preview only: use the MCAD admin panel to review this report.")
        message = update_data.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
            return None
        text = message.get("text") or ""
        command = text.split()[0].lower().split("@")[0] if isinstance(text, str) and text.strip() else ""
        chat_id = str(chat["id"])
        if rejection_notes_enabled and rejection_actions_enabled and review_buttons_enabled:
            actor = message.get("from")
            if (isinstance(actor, dict) and type(actor.get("id")) is int and
                    actor["id"] == chat["id"] and actor["id"] in reviewer_ids and
                    actor.get("is_bot") is not True):
                state_key = rejection_note_session_key(actor["id"])
                note_state = session.get(WorkerState, state_key)
                if note_state is not None:
                    draft = note_state.value or {}
                    if command == "/cancel":
                        session.delete(note_state)
                        return chat_id, "Rejection explanation cancelled. No changes saved."
                    if draft.get("expiresAt", 0) < time.time():
                        session.delete(note_state)
                        if not command.startswith("/"):
                            return chat_id, "Your rejection explanation expired. Start review again."
                    if not command.startswith("/") and draft.get("stage") == "awaiting_text":
                        reply_to = message.get("reply_to_message")
                        # Only a reply to our explicit ForceReply prompt counts.
                        if (not isinstance(reply_to, dict) or
                                not isinstance(reply_to.get("from"), dict) or
                                reply_to["from"].get("is_bot") is not True or
                                not str(reply_to.get("text", "")).startswith("MCAD rejection explanation")):
                            return chat_id, "Reply directly to the MCAD rejection explanation prompt, or use /cancel."
                        note = normalize_rejection_note(text)
                        if note is None:
                            return chat_id, "Enter between 1 and 1000 characters of explanation, or /cancel."
                        cid = draft.get("candidateId")
                        key = draft.get("reasonKey")
                        delivered = session.scalar(select(CandidateDelivery.id).join(
                            CandidateNotification, CandidateDelivery.event_id == CandidateNotification.id
                        ).join(Subscriber, CandidateDelivery.chat_id == Subscriber.chat_id).where(
                            CandidateNotification.candidate_id == cid,
                            CandidateDelivery.chat_id == chat_id,
                            CandidateDelivery.status == "sent",
                            Subscriber.active.is_(True),
                        ).limit(1))
                        candidate = session.get(Candidate, cid)
                        if (not delivered or candidate is None or candidate.status != "pending" or
                                candidate.baseline or candidate.origin != "ai" or
                                candidate.verification_status != "supported" or
                                candidate.relationship_type not in {"unclassified", "new_incident"} or
                                candidate.related_incident_id is not None or key not in REJECTION_REASONS):
                            session.delete(note_state)
                            return chat_id, "This candidate cannot be reviewed. No changes saved."
                        note_state.value = {**draft, "stage": "ready", "note": note,
                                            "expiresAt": time.time() + 15 * 60}
                        return MessageReply(chat_id, "Explanation received. Confirm or cancel rejection of: " +
                                            clean_text(candidate.title)[:200],
                                            rejection_confirmation_keyboard(cid, key, with_note=True))
        if command == "/subscribe":
            set_subscription(session, chat_id, True)
            reply = "Subscribed. You will receive new source-checked AI reports and published MCAD incidents. AI reports require human review. Historical records are not sent."
        elif command == "/unsubscribe":
            set_subscription(session, chat_id, False)
            reply = "Unsubscribed. MCAD incident alerts are stopped."
        elif command == "/latest":
            incident = session.scalar(select(Incident).where(Incident.status == "published").order_by(Incident.created_at.desc(), Incident.id).limit(1))
            reply = notification_text({"id": incident.id, "title": incident.title, "year": incident.year, "incidentCountry": incident.incident_country, "sourceUrl": incident.source_url}, base_url).replace("New verified MCAD incident", "Latest verified database record", 1) if incident else "No verified incidents are available yet."
        elif command == "/map":
            reply = safe_url(base_url) if base_url else "The public map URL is not configured yet."
        elif command in {"/start", "/help"}:
            reply = HELP
        else:
            return None
        return chat_id, reply


def feed_poll_once(factory, articles=None):
    from filters import matching_keywords
    if articles is None:
        from news import fetch_articles
        articles = fetch_articles()
    with factory.begin() as session:
        state = session.get(WorkerState, "feed_baseline")
        baseline = state is None
        added = 0
        for article in articles:
            keywords = matching_keywords(article)
            if not keywords:
                continue
            try:
                source_url = safe_url(article.get("link"))
            except ValueError:
                continue
            if not source_url or session.scalar(select(Candidate.id).where(Candidate.source_url == source_url)):
                continue
            session.add(Candidate(source_url=source_url, title=clean_text(article.get("title"))[:4000] or "Untitled source report", summary=clean_text(article.get("summary"))[:100000], matched_keywords=keywords, baseline=baseline))
            session.flush()
            added += 1
        if state is None:
            session.add(WorkerState(name="feed_baseline", value={"completedAt": utcnow().isoformat()}))
        return added


class WorkerController:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings
        self.owner = str(uuid4())
        self.stopped = threading.Event()
        self.threads = []
        self.client = TelegramClient(settings.telegram_bot_token) if settings.telegram_bot_token else None

    def start(self):
        jobs = []
        if self.client:
            jobs.append(("telegram_delivery", self._delivery_loop))
            if self.settings.enable_telegram_polling:
                jobs.append(("telegram_polling", self._poll_loop))
        if self.settings.enable_feed_polling:
            jobs.append(("feed_polling", self._feed_loop))
        if self.settings.enable_ai_search:
            jobs.append(("ai_monitor", self._ai_loop))
        for name, target in jobs:
            thread = threading.Thread(name=f"mcad-{name}", target=self._guard, args=(name, target), daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stopped.set()
        for thread in self.threads:
            thread.join(timeout=1)

    def _guard(self, name, target):
        while not self.stopped.is_set():
            try:
                target()
            except Exception:
                # Deliberately omit exception repr/traceback containing remote URLs.
                logger.error("MCAD %s worker temporarily unavailable; restarting.", name)
                self.stopped.wait(10)
            finally:
                try:
                    release_lease(self.factory, name, self.owner)
                except Exception:
                    pass

    def _delivery_loop(self):
        while not self.stopped.is_set():
            try:
                if claim_lease(self.factory, "telegram_delivery", self.owner):
                    sent = deliver_one(self.factory, self.client, self.settings.public_base_url, self.owner,
                                       message_format=self.settings.telegram_message_format)
                    if not sent:
                        deliver_candidate_one(self.factory, self.client, self.settings.public_base_url, self.owner,
                                              message_format=self.settings.telegram_message_format,
                                              reviewer_ids=self.settings.telegram_reviewer_ids,
                                              review_buttons_enabled=self.settings.enable_telegram_review_buttons,
                                              rejection_actions_enabled=self.settings.enable_telegram_rejection_actions,
                                              approval_actions_enabled=self.settings.enable_telegram_approval_actions,
                                              classification_actions_enabled=self.settings.enable_telegram_classification_actions)
            except Exception:
                logger.warning("MCAD delivery temporarily unavailable; retrying.")
            self.stopped.wait(1.05)

    def _poll_loop(self):
        while not self.stopped.is_set():
            if not claim_lease(self.factory, "telegram_polling", self.owner):
                self.stopped.wait(5)
                continue
            with self.factory() as session:
                state = session.get(WorkerState, "telegram_offset")
                offset = state.value.get("offset", 0) if state else 0
            data = self.client.get_updates(offset)
            if not data.get("ok"):
                logger.warning("MCAD Telegram polling unavailable; check token and ensure old listeners are stopped.")
                self.stopped.wait(30)
                continue
            for item in data.get("result", []):
                if self.stopped.is_set() or not claim_lease(self.factory, "telegram_polling", self.owner):
                    break
                reply = process_update(
                    self.factory, item, self.settings.public_base_url,
                    reviewer_ids=self.settings.telegram_reviewer_ids,
                    review_buttons_enabled=self.settings.enable_telegram_review_buttons,
                    rejection_actions_enabled=self.settings.enable_telegram_rejection_actions,
                    approval_actions_enabled=self.settings.enable_telegram_approval_actions,
                    approval_secret=self.settings.telegram_bot_token,
                    rejection_notes_enabled=self.settings.enable_telegram_rejection_notes,
                    classification_actions_enabled=self.settings.enable_telegram_classification_actions,
                )
                if isinstance(reply, CallbackReply):
                    self.client.answer_callback(reply.callback_id, reply.text)
                    if reply.chat_id and reply.message:
                        self.client.send(reply.chat_id, reply.message, reply_markup=reply.reply_markup)
                elif isinstance(reply, MessageReply):
                    self.client.send(reply.chat_id, reply.text, reply_markup=reply.reply_markup)
                    self.stopped.wait(1.05)
                elif reply:
                    self.client.send(*reply)
                    self.stopped.wait(1.05)

    def _feed_loop(self):
        while not self.stopped.is_set():
            try:
                if claim_lease(self.factory, "feed_polling", self.owner, seconds=120):
                    with self.factory() as session:
                        state = session.get(WorkerState, "feed_last_poll")
                        due = not state or utcnow().timestamp() - state.value.get("timestamp", 0) >= 900
                    if due:
                        feed_poll_once(self.factory)
                        with self.factory.begin() as session:
                            state = session.get(WorkerState, "feed_last_poll")
                            if state:
                                state.value = {"timestamp": utcnow().timestamp()}
                            else:
                                session.add(WorkerState(name="feed_last_poll", value={"timestamp": utcnow().timestamp()}))
            except Exception:
                logger.warning("MCAD feed review queue temporarily unavailable; retrying.")
            self.stopped.wait(60)

    def _ai_loop(self):
        from .ai_monitor import AIUnavailable, monitor_due, run_monitor_once

        while not self.stopped.is_set():
            try:
                # Both the server gate and saved administrator setting are checked
                # again when claiming a run. A configured API key alone does nothing.
                if monitor_due(self.factory, self.settings):
                    run_monitor_once(self.factory, self.settings, trigger="scheduled")
            except AIUnavailable:
                pass
            except Exception:
                logger.warning("MCAD AI monitor temporarily unavailable; check administrator run history.")
            self.stopped.wait(60)
