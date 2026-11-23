"""Moderate the specific draft delivered to the configured Telegram review chat."""
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

from fastapi import HTTPException
from sqlalchemy import select

from .models import Candidate, CandidateDelivery, CandidateNotification, WorkerState, utcnow
from .service import safe_url
from .telegram_feedback import CallbackAnswer


REVIEW_CALLBACK_PREFIX = "mcad:review:"
REVIEW_CALLBACK = re.compile(
    r"mcad:review:([0-9a-f-]{36}):(accept|update|reject|classify|cv|cm|[vm][a-z]|save:[A-Za-z0-9_-]{8}|cancel)\Z"
)
MAX_SOURCE_LINK_UNITS = 800

# Deliberately small, client-reviewable choices. More specific classifications
# require the web editor; Telegram must never accept arbitrary callback text.
CLASSIFICATION_FIELDS = {"v": "victimType", "m": "method"}
CLASSIFICATION_CHOICES = {
    "v": {"v": "Vessel", "p": "Port", "o": "Offshore infrastructure",
          "u": "Undersea infrastructure", "i": "Other maritime infrastructure"},
    "m": {"a": "AIS spoofing", "g": "GPS spoofing", "j": "GPS jamming",
          "r": "Ransomware", "o": "Other / undetermined"},
}
CLASSIFICATION_REVIEW_MINUTES = 15


def _display(value, max_units):
    clean = "".join(character for character in str(value or "") if character in "\n\t" or ord(character) >= 32)
    encoded = clean.encode("utf-16-le", errors="ignore")
    if len(encoded) <= max_units * 2:
        return clean
    return encoded[:(max_units - 1) * 2].decode("utf-16-le", errors="ignore").rstrip() + "…"


def _source_lines(values):
    seen = set()
    for value in values:
        try:
            url = safe_url(value)
        except ValueError:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        if _fits_source_link(url):
            yield url
        else:
            yield f"{_display(urlsplit(url).hostname, 200)} · full link in MCAD"
        if len(seen) >= 2:
            break


def _fits_source_link(url):
    try:
        return len(url.encode("utf-16-le")) // 2 <= MAX_SOURCE_LINK_UNITS and not any(char.isspace() for char in url)
    except UnicodeEncodeError:
        return False


def _visible_corroboration(candidate):
    from .editorial import telegram_corroborating_urls

    return telegram_corroborating_urls(candidate, url_filter=_fits_source_link)


def _classification_available(candidate):
    evidence = candidate.evidence or {}
    return (candidate.status == "pending" and candidate.origin == "ai"
            and candidate.verification_status == "supported" and not candidate.baseline
            and not evidence.get("suggestedIncidentId") and not evidence.get("materialUpdate"))


def _keyboard(delivery_id, rows):
    return {"inline_keyboard": [[{"text": label,
                                  "callback_data": f"{REVIEW_CALLBACK_PREFIX}{delivery_id}:{action}"}]
                                 for label, action in rows]}


def _menu_reply(query_id, chat_id, message_id, text, markup):
    return CallbackAnswer(query_id, text, chat_id, message_id, markup)


def _classification_state_key(reviewer_id):
    return f"tg_classify:{reviewer_id}"


def review_notification(candidate, delivery_id, base_url="", incident_title=None,
                        *, classification_enabled=False):
    evidence = candidate.evidence or {}
    fields = candidate.proposed_fields or {}
    update = evidence.get("materialUpdate") or {}
    target = evidence.get("suggestedIncidentId")
    title = _display(candidate.title, 270)
    description = _display(update.get("summary") or fields.get("summary") or candidate.summary, 1050)
    lines = ["MCAD · Incident update for review" if target else "MCAD · Incident for review", "", title]
    if target:
        lines.append(f"Existing incident: {_display(incident_title or evidence.get('suggestedIncidentTitle') or target, 170)}")
    if description:
        lines.extend(["", description])
    sources = update.get("supportingUrls") if update else fields.get("referenceUrls")
    pair = _visible_corroboration(candidate)
    urls = pair or tuple(_source_lines(sources or [candidate.source_url]))
    if urls:
        lines.extend(["", "Sources", *urls])
    # The bounded title, summary and two complete links fit in one message.
    text = "\n".join(lines)
    if pair:
        text += "\n\nCheck both independent sources and the description before accepting."
    else:
        text += "\n\nFurther verification required. Confirm the independent sources and incident match in MCAD before accepting."

    actions = []
    if pair:
        action, caption = ("update", "Apply update") if target else ("accept", "Accept")
        actions.append({"text": caption, "callback_data": f"{REVIEW_CALLBACK_PREFIX}{delivery_id}:{action}"})
    actions.append({"text": "Reject", "callback_data": f"{REVIEW_CALLBACK_PREFIX}{delivery_id}:reject"})
    rows = [actions]
    if classification_enabled and _classification_available(candidate):
        rows.append([{"text": "Correct classification",
                      "callback_data": f"{REVIEW_CALLBACK_PREFIX}{delivery_id}:classify"}])
    url = safe_url(base_url)
    if url:
        candidate_url = f"{url.rstrip('/')}/admin?candidate={quote(str(candidate.id), safe='')}"
        rows.append([{"text": "Open in MCAD", "url": candidate_url}])
    return text, {"inline_keyboard": rows}


def _telegram_id(value, *, positive=False):
    if type(value) is not int or not 0 < abs(value) < 10**20 or positive and value < 0:
        return None
    return str(value)


def _review_chat(settings, message, actor):
    if not settings or not settings.telegram_review_enabled:
        return None
    chat = message.get("chat") or {}
    chat_id = _telegram_id(chat.get("id"))
    user_id = _telegram_id(actor.get("id"), positive=True)
    if (chat_id != settings.telegram_review_chat_id or chat.get("type") not in {"private", "group", "supergroup"}
            or not user_id or actor.get("is_bot") or user_id not in settings.telegram_reviewer_ids):
        return None
    if chat["type"] == "private" and chat_id != user_id:
        return None
    return chat_id


def _candidate_from_message(session, chat_id, message_id, delivery_id=None):
    query = select(CandidateNotification).join(CandidateDelivery).where(
        CandidateDelivery.chat_id == chat_id,
        CandidateDelivery.telegram_message_id == message_id,
        CandidateDelivery.status == "sent",
    )
    if delivery_id:
        query = query.where(CandidateDelivery.id == delivery_id)
    event = session.scalar(query)
    if not event or event.payload.get("reviewChatId") != chat_id:
        return None
    return session.get(Candidate, event.candidate_id)


def record_review_callback(session, callback, settings):
    query_id, data = callback.get("id"), callback.get("data")
    if not isinstance(query_id, str) or not 1 <= len(query_id) <= 128:
        return None
    match = REVIEW_CALLBACK.fullmatch(data) if isinstance(data, str) else None
    message = callback.get("message") or {}
    chat_id = _review_chat(settings, message, callback.get("from") or {})
    message_id = _telegram_id(message.get("message_id"), positive=True)
    unavailable = CallbackAnswer(query_id, "You cannot review this message.")
    if not (match and chat_id and message_id):
        return unavailable
    delivery_id, action = match.groups()
    candidate = _candidate_from_message(session, chat_id, message_id, delivery_id)
    if not candidate:
        return unavailable
    if candidate.status != "pending":
        return CallbackAnswer(query_id, "This draft has already been reviewed.")
    if action not in {"accept", "update", "reject"}:
        if not getattr(settings, "enable_telegram_classification_actions", False) or not _classification_available(candidate):
            return unavailable
        reviewer_id = _telegram_id((callback.get("from") or {}).get("id"), positive=True)
        state = session.get(WorkerState, _classification_state_key(reviewer_id))
        if action == "classify":
            # Going back to the first menu abandons a previous confirmation.
            if state and state.value.get("deliveryId") == delivery_id:
                session.delete(state)
            return _menu_reply(query_id, chat_id, message_id,
                "Choose a field. Nothing is published.",
                _keyboard(delivery_id, [("Maritime category", "cv"), ("Incident method", "cm"),
                                        ("Cancel", "cancel")]))
        if action in {"cv", "cm"}:
            field_key = action[1]
            options = [(value, field_key + code) for code, value in CLASSIFICATION_CHOICES[field_key].items()]
            return _menu_reply(query_id, chat_id, message_id,
                "Choose the corrected classification.",
                _keyboard(delivery_id, [*options, ("Back", "classify"), ("Cancel", "cancel")]))
        if action == "cancel":
            if state and state.value.get("deliveryId") == delivery_id:
                session.delete(state)
            markup = review_notification(candidate, delivery_id, settings.public_base_url,
                classification_enabled=True)[1]
            return _menu_reply(query_id, chat_id, message_id, "Correction cancelled.", markup)
        if len(action) == 2 and action[0] in CLASSIFICATION_CHOICES:
            field_key, choice_key = action
            choice = CLASSIFICATION_CHOICES[field_key].get(choice_key)
            if not choice:
                return unavailable
            field = CLASSIFICATION_FIELDS[field_key]
            previous = (candidate.proposed_fields or {}).get(field)
            if previous == choice:
                return CallbackAnswer(query_id, "This classification is already selected.")
            nonce = secrets.token_urlsafe(6)
            value = {"candidateId": candidate.id, "deliveryId": delivery_id,
                     "chatId": chat_id, "field": field, "oldValue": previous,
                     "newValue": choice, "nonce": nonce,
                     "expiresAt": (utcnow() + timedelta(minutes=CLASSIFICATION_REVIEW_MINUTES)).isoformat()}
            if state:
                state.value = value
            else:
                session.add(WorkerState(name=_classification_state_key(reviewer_id), value=value))
            return _menu_reply(query_id, chat_id, message_id,
                f"Confirm {field}: {_display(previous or '(unset)', 60)} → {_display(choice, 60)}. This will not publish.",
                _keyboard(delivery_id, [("Save correction (no publication)", f"save:{nonce}"),
                                        ("Cancel", "cancel")]))
        if action.startswith("save:"):
            draft = state.value if state else {}
            if (draft.get("candidateId") != candidate.id or draft.get("deliveryId") != delivery_id
                    or draft.get("chatId") != chat_id or draft.get("nonce") != action[5:]):
                return CallbackAnswer(query_id, "This correction confirmation is no longer valid.")
            try:
                expires = datetime.fromisoformat(draft["expiresAt"])
            except (KeyError, TypeError, ValueError):
                return CallbackAnswer(query_id, "This correction confirmation is invalid.")
            if expires <= utcnow():
                session.delete(state)
                return CallbackAnswer(query_id, "Correction expired. Start again.")
            # Lock the same candidate row as the normal publishing pathway.
            candidate = session.scalar(select(Candidate).where(Candidate.id == candidate.id)
                                       .with_for_update().execution_options(populate_existing=True))
            if not candidate or not _classification_available(candidate):
                return CallbackAnswer(query_id, "This report can no longer be corrected.")
            field, old_value, new_value = draft.get("field"), draft.get("oldValue"), draft.get("newValue")
            key = next((key for key, name in CLASSIFICATION_FIELDS.items() if name == field), None)
            if (not key or new_value not in CLASSIFICATION_CHOICES[key].values()
                    or (candidate.proposed_fields or {}).get(field) != old_value):
                session.delete(state)
                return CallbackAnswer(query_id, "The classification changed. Review the current report again.")
            candidate.proposed_fields = {**(candidate.proposed_fields or {}), field: new_value}
            evidence = dict(candidate.evidence or {})
            history = evidence.get("classificationReviews")
            history = list(history) if isinstance(history, list) else []
            history.append({"field": field, "oldValue": old_value, "newValue": new_value,
                            "reviewerTelegramId": reviewer_id, "reviewedAt": utcnow().isoformat() + "Z"})
            evidence["classificationReviews"] = history
            candidate.evidence = evidence
            session.delete(state)
            markup = review_notification(candidate, delivery_id, settings.public_base_url,
                classification_enabled=True)[1]
            return _menu_reply(query_id, chat_id, message_id,
                "Classification saved. Report still pending; nothing published.", markup)
        return unavailable
    if action == "reject":
        return CallbackAnswer(query_id, "Reply to this message with /reject followed by your explanation, for example /reject The report describes a drill, not an outage.")

    from .editorial import approve_candidate

    target = (candidate.evidence or {}).get("suggestedIncidentId")
    if (action == "update") != bool(target):
        return CallbackAnswer(query_id, "The suggested action changed. Open the draft in MCAD.")
    if not _visible_corroboration(candidate):
        return CallbackAnswer(query_id, "Confirm the sources and incident in the web review queue before publishing.")
    payload = {"updateIncidentId": target} if target else {}
    try:
        with session.begin_nested():
            approve_candidate(session, candidate.id, payload, via_telegram=True)
    except HTTPException as error:
        return CallbackAnswer(query_id, str(error.detail))
    except ValueError:
        return CallbackAnswer(query_id, "The draft needs editing before publication. Open it in MCAD.")
    return CallbackAnswer(query_id, "Existing incident updated." if target else "Incident published.")


def record_rejection(session, message, settings):
    chat_id = _review_chat(settings, message, message.get("from") or {})
    replied_to = message.get("reply_to_message") or {}
    message_id = _telegram_id(replied_to.get("message_id"), positive=True)
    if not (chat_id and message_id):
        return None
    candidate = _candidate_from_message(session, chat_id, message_id)
    if not candidate:
        return None
    if candidate.status != "pending":
        return chat_id, "This draft has already been reviewed."
    parts = message["text"].split(maxsplit=1)
    notes = "".join(character for character in parts[1] if character in "\n\t" or ord(character) >= 32).strip() if len(parts) == 2 else ""
    if not notes:
        return chat_id, "Please explain the rejection after /reject in your reply to the draft."

    from .editorial import reject_candidate

    try:
        with session.begin_nested():
            reject_candidate(session, candidate.id, "other", notes)
    except HTTPException as error:
        return chat_id, str(error.detail)
    return chat_id, "Draft rejected. Your explanation was saved for prompt improvements."
