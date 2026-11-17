"""Database-backed feed review queue, subscriptions, polling and notification outbox.

No network work runs on module import. Each worker has its own DB sessions and
holds a renewable lease, including across overlapping hosted deployments.
"""
import logging
import re
import threading
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4
from urllib.parse import quote

import requests
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import (Candidate, CandidateDelivery, CandidateNotification, Delivery, Incident,
                     OutboxEvent, ProcessedUpdate, Subscriber, WorkerLease, WorkerState, utcnow)
from .service import safe_url
from .candidate_alerts import deliver_candidate_one
from .telegram_review import (CallbackReply, REJECTION_REASONS, is_authorized_review_callback,
                              parse_preview_action, parse_rejection_action,
                              rejection_confirmation_keyboard, rejection_reason_keyboard)


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
                   review_buttons_enabled=False, rejection_actions_enabled=False):
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
            # Stage 3 handles confirmed, structured REJECTION only. Approval and
            # corrections remain read-only previews until separately implemented.
            rejection = parse_rejection_action(callback.get("data"))
            if rejection is not None:
                if not rejection_actions_enabled:
                    return CallbackReply(callback_id, "Telegram rejection is disabled. Use the MCAD admin panel.")
                stage, reason_key, candidate_id = rejection
                chat_id = str(callback["from"]["id"])
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
                    return CallbackReply(callback_id, "Select a rejection reason.", chat_id,
                                         "Choose a rejection reason for: " + clean_text(candidate.title)[:240],
                                         rejection_reason_keyboard(candidate_id))
                reason, label = REJECTION_REASONS[reason_key]
                if stage == "reason":
                    return CallbackReply(callback_id, "Confirm or cancel your decision.", chat_id,
                                         f"Reject '{clean_text(candidate.title)[:160]}' as {label}? "
                                         "This will save the reason. To add detailed notes, use the web admin panel.",
                                         rejection_confirmation_keyboard(candidate_id, reason_key))
                # The state change is conditional, as in the admin API, to avoid
                # double reviews or overwriting an approval from another interface.
                changed = session.execute(update(Candidate).where(
                    Candidate.id == candidate_id, Candidate.status == "pending"
                ).values(status="rejected", rejection_reason=reason, rejection_notes="")
                    .execution_options(synchronize_session=False))
                if changed.rowcount != 1:
                    return CallbackReply(callback_id, "This report was already reviewed. No changes saved.")
                # Preserve a minimal audit trail for the client. No bot-token,
                # message text or personal username is recorded.
                evidence = dict(candidate.evidence or {})
                history = list(evidence.get("telegramReviewHistory", []))
                history.append({"decision": "rejected", "reason": reason,
                                "reviewerId": chat_id, "reviewedAt": utcnow().isoformat() + "Z"})
                evidence["telegramReviewHistory"] = history
                candidate.evidence = evidence
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
                                              rejection_actions_enabled=self.settings.enable_telegram_rejection_actions)
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
                )
                if isinstance(reply, CallbackReply):
                    self.client.answer_callback(reply.callback_id, reply.text)
                    if reply.chat_id and reply.message:
                        self.client.send(reply.chat_id, reply.message, reply_markup=reply.reply_markup)
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
