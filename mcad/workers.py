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

from .models import Candidate, Delivery, Incident, OutboxEvent, ProcessedUpdate, Subscriber, WorkerLease, WorkerState, utcnow
from .service import safe_url


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

    def send(self, chat_id, text):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": chat_id, "text": clean_text(text)[:4096], "link_preview_options": {"is_disabled": True}},
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

    def get_updates(self, offset):
        try:
            response = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", params={"offset": offset, "timeout": 25, "allowed_updates": '["message"]'}, timeout=35)
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


def deliver_one(factory, client, base_url="", owner=None, now=None):
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
    result = client.send(chat_id, notification_text(payload, base_url)) if active else None
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


HELP = "MCAD verified incidents\n\n/subscribe — receive future verified incidents\n/unsubscribe — stop alerts\n/latest — latest verified database incident\n/map — open the incident map\n/help — show commands\n\nHistorical imports do not send alerts."


def process_update(factory, update_data, base_url=""):
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
        message = update_data.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or not isinstance(chat.get("id"), int):
            return None
        text = message.get("text") or ""
        command = text.split()[0].lower().split("@")[0] if isinstance(text, str) and text.strip() else ""
        chat_id = str(chat["id"])
        if command == "/subscribe":
            set_subscription(session, chat_id, True)
            reply = "Subscribed. You will receive future verified MCAD incidents. Historical records are not sent."
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
                    deliver_one(self.factory, self.client, self.settings.public_base_url, self.owner)
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
                reply = process_update(self.factory, item, self.settings.public_base_url)
                if reply:
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
