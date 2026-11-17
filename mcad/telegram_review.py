"""Authorization for future interactive Telegram reviews.

The Telegram user ID (callback_query.from.id), not the chat ID or sender
name, determines whether someone may make a review decision.
"""


def is_authorized_review_callback(callback_query: dict | None, reviewer_ids: frozenset[int]) -> bool:
    """Fail closed; only allow approved users in a private bot chat."""
    if not reviewer_ids or not isinstance(callback_query, dict):
        return False
    actor = callback_query.get("from")
    message = callback_query.get("message")
    if not isinstance(actor, dict) or not isinstance(message, dict):
        return False
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return False
    user_id = actor.get("id")
    return (
        type(user_id) is int
        and user_id in reviewer_ids
        and actor.get("is_bot") is not True
        and chat.get("type") == "private"
        and chat.get("id") == user_id
    )


# Telegram callback_data is limited to 64 bytes. We use a fixed action and UUID
# rather than inserting user-provided text or full incident details.
from dataclasses import dataclass
from uuid import UUID
import base64
import hashlib
import hmac
import time
from urllib.parse import urlsplit

from .service import normalize_payload, safe_url


@dataclass(frozen=True)
class CallbackReply:
    callback_id: str
    text: str
    chat_id: str | None = None
    message: str | None = None
    reply_markup: dict | None = None


def preview_keyboard(candidate_id: str) -> dict | None:
    """Safe, explicitly *preview* actions; never imply review is already enabled."""
    try:
        if str(UUID(candidate_id)) != candidate_id:
            return None
    except (ValueError, AttributeError, TypeError):
        return None
    return {"inline_keyboard": [
        [{"text": "Approve (preview)", "callback_data": f"mcad:p:approve:{candidate_id}"},
         {"text": "Reject (preview)", "callback_data": f"mcad:p:reject:{candidate_id}"}],
        [{"text": "Correct (preview)", "callback_data": f"mcad:p:correct:{candidate_id}"}],
    ]}


def parse_preview_action(data: object) -> tuple[str, str] | None:
    if not isinstance(data, str):
        return None
    chunks = data.split(":")
    if len(chunks) != 4 or chunks[:2] != ["mcad", "p"] or chunks[2] not in {"approve", "reject", "correct"}:
        return None
    if preview_keyboard(chunks[3]) is None:
        return None
    return chunks[2], chunks[3]


# A separate opt-in flow for rejecting a *delivered* AI candidate. No web
# approval, publishing, or classification updates can be triggered here.
REJECTION_REASONS = {
    "m": ("not_maritime", "Not maritime"),
    "c": ("not_cyber", "Not a cyber incident"),
    "d": ("duplicate", "Duplicate"),
    "e": ("insufficient_evidence", "Insufficient evidence"),
    "i": ("incorrect_information", "Incorrect information"),
}


def rejection_keyboard(candidate_id: str) -> dict | None:
    """Show functional Reject and read-only Approve/Correct previews."""
    markup = preview_keyboard(candidate_id)
    if markup is None:
        return None
    markup["inline_keyboard"][0][1] = {
        "text": "Reject (choose reason)",
        "callback_data": f"mcad:r:start:{candidate_id}",
    }
    return markup


def rejection_reason_keyboard(candidate_id: str) -> dict:
    rows = [[{"text": label, "callback_data": f"mcad:r:reason:{key}:{candidate_id}"}]
            for key, (_, label) in REJECTION_REASONS.items()]
    rows.append([{"text": "Cancel", "callback_data": f"mcad:r:cancel:{candidate_id}"}])
    return {"inline_keyboard": rows}


def rejection_confirmation_keyboard(candidate_id: str, key: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "Confirm rejection", "callback_data": f"mcad:r:confirm:{key}:{candidate_id}"}],
        [{"text": "Cancel", "callback_data": f"mcad:r:cancel:{candidate_id}"}],
    ]}


def parse_rejection_action(data: object) -> tuple[str, str | None, str] | None:
    """Validate callback content and keep it below Telegram's 64-byte limit."""
    if not isinstance(data, str) or len(data.encode("utf-8")) > 64:
        return None
    parts = data.split(":")
    if len(parts) == 4 and parts[:2] == ["mcad", "r"] and parts[2] in {"start", "cancel"}:
        stage, key, candidate_id = parts[2], None, parts[3]
    elif len(parts) == 5 and parts[:2] == ["mcad", "r"] and parts[2] in {"reason", "confirm"} and parts[3] in REJECTION_REASONS:
        stage, key, candidate_id = parts[2], parts[3], parts[4]
    else:
        return None
    if preview_keyboard(candidate_id) is None:
        return None
    return stage, key, candidate_id


# Approval uses a separately gated, two-stage confirmation. The second callback
# carries a short, expiring MAC bound to the reviewer, candidate and bot secret.
# This fits Telegram's 64-byte callback_data limit (36-byte UUID included).
_APPROVAL_WINDOW_MINUTES = 15


def approval_keyboard(candidate_id: str, *, allow_rejection: bool = False) -> dict | None:
    markup = rejection_keyboard(candidate_id) if allow_rejection else preview_keyboard(candidate_id)
    if markup is not None:
        markup["inline_keyboard"][0][0] = {
            "text": "Approve (review details)",
            "callback_data": f"mcad:a:start:{candidate_id}",
        }
    return markup


def _base36(number: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if number <= 0:
        return "0"
    digits = []
    while number:
        number, r = divmod(number, 36)
        digits.append(alphabet[r])
    return "".join(reversed(digits))


def approval_confirmation_keyboard(candidate_id: str, reviewer_id: int, secret: str, *, now=None) -> dict:
    if preview_keyboard(candidate_id) is None or not secret or type(reviewer_id) is not int:
        raise ValueError("A valid candidate, reviewer and bot secret are required")
    tick = _base36(int((time.time() if now is None else now) // 60))
    message = f"{candidate_id}:{reviewer_id}:{tick}".encode("ascii")
    mac = base64.urlsafe_b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()[:8]).decode().rstrip("=")
    callback_data = f"mcad:a:c:{candidate_id}:{tick}:{mac}"
    if len(callback_data.encode("utf-8")) > 64:
        raise ValueError("Approval callback exceeds Telegram's 64-byte limit")
    return {"inline_keyboard": [
        [{"text": "Confirm PUBLIC publication", "callback_data": callback_data}],
        [{"text": "Cancel", "callback_data": f"mcad:a:cancel:{candidate_id}"}],
    ]}


def parse_approval_action(data: object) -> tuple[str, str, str | None, str | None] | None:
    if not isinstance(data, str) or len(data.encode("utf-8")) > 64:
        return None
    parts = data.split(":")
    if len(parts) == 4 and parts[:2] == ["mcad", "a"] and parts[2] in {"start", "cancel"}:
        stage, candidate_id, tick, signature = parts[2], parts[3], None, None
    elif len(parts) == 6 and parts[:3] == ["mcad", "a", "c"]:
        stage, candidate_id, tick, signature = "confirm", parts[3], parts[4], parts[5]
        if not 1 <= len(tick) <= 6 or any(c not in "0123456789abcdefghijklmnopqrstuvwxyz" for c in tick):
            return None
        if len(signature) != 11 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in signature):
            return None
    else:
        return None
    if preview_keyboard(candidate_id) is None:
        return None
    return stage, candidate_id, tick, signature


def valid_approval_confirmation(candidate_id: str, reviewer_id: int, secret: str,
                                tick: str | None, signature: str | None, *, now=None) -> bool:
    if not secret or type(reviewer_id) is not int or not tick or not signature:
        return False
    current_minute = int((time.time() if now is None else now) // 60)
    try:
        signed_minute = int(tick, 36)
    except ValueError:
        return False
    if not 0 <= current_minute - signed_minute <= _APPROVAL_WINDOW_MINUTES:
        return False
    data = f"{candidate_id}:{reviewer_id}:{tick}".encode("ascii")
    expected = base64.urlsafe_b64encode(hmac.new(secret.encode(), data, hashlib.sha256).digest()[:8]).decode().rstrip("=")
    return hmac.compare_digest(expected, signature)


def verified_evidence_sources(candidate) -> list[dict]:
    """Fail closed: do not trust a 'supported' label alone for Telegram publication.

    These values are *AI-reported* independent sources, not a substitute for
    the reviewer's judgment. Require two distinct opened hosts and source groups.
    """
    evidence = candidate.evidence if isinstance(candidate.evidence, dict) else {}
    if not evidence.get("firstReportedAt") or evidence.get("contradictions"):
        return []
    sources = evidence.get("sources")
    if not isinstance(sources, list):
        return []
    valid = []
    for source in sources:
        if not isinstance(source, dict) or source.get("openedByVerifier") is not True or not source.get("supports") or source.get("contradicts"):
            continue
        group = source.get("independenceGroup")
        if not isinstance(group, str) or not group.strip():
            continue
        try:
            url = safe_url(source.get("url"))
        except ValueError:
            continue
        host = (urlsplit(url).hostname or "").removeprefix("www.") if url else ""
        if host:
            valid.append({"url": url, "host": host, "group": group.strip().casefold(),
                          "title": str(source.get("title") or "")[:240],
                          "snippet": str(source.get("snippet") or "")[:2000]})
    if len({item["host"] for item in valid}) < 2 or len({item["group"] for item in valid}) < 2:
        return []
    return valid


def telegram_approval_payload(candidate, reviewer_id: int) -> dict | None:
    """Build an explicit, bounded set of fields, preserving AI provenance.

    AI data cannot override IDs, status, author identity, original references,
    review/audit data or map coordinates. Unsupported records stay in web review.
    """
    if (candidate.status != "pending" or candidate.origin != "ai"
        or candidate.verification_status != "supported" or candidate.baseline
        or candidate.relationship_type not in {"unclassified", "new_incident"}
        or candidate.related_incident_id is not None):
        return None
    sources = verified_evidence_sources(candidate)
    fields = candidate.proposed_fields if isinstance(candidate.proposed_fields, dict) else {}
    title = fields.get("title")
    if not sources or not isinstance(title, str) or not title.strip() or title.strip() != candidate.title.strip():
        return None
    # The source URL must come from the verifier's inspected evidence.
    try:
        primary_url = safe_url(candidate.source_url)
    except ValueError:
        return None
    if primary_url not in {item["url"] for item in sources}:
        return None
    permitted = {
        "title", "year", "yearEnd", "month", "dateText", "dateStart", "datePrecision",
        "victimCountry", "victimType", "victimIdentity", "victimTypeShort",
        "incidentCountry", "attackerCountry", "locationName", "impactArea",
        "affectedSystem", "method", "malwareType", "ransomwareType", "attackPattern",
        "motive", "motiveShort", "campaign", "impact", "impactShort", "summary",
        "countermeasures", "threatActor", "additionalInfo", "socialEngineeringTechnique",
    }
    payload = {k: v for k, v in fields.items() if k in permitted}
    payload["sourceUrl"] = primary_url
    payload["referenceUrls"] = list(dict.fromkeys(item["url"] for item in sources))
    payload["sources"] = [{"url": item["url"], "shortName": item["title"], "rawText": item["snippet"]}
                          for item in sources]
    payload["description"] = str(fields.get("summary") or candidate.summary or "")[:100000]
    payload["status"] = "published"
    payload["rawFields"] = {"_aiReview": {
        "candidateId": candidate.id,
        "runId": candidate.ai_run_id,
        "verificationStatus": candidate.verification_status,
        "evidence": candidate.evidence or {},
        "proposedFields": candidate.proposed_fields or {},
    }, "_telegramReview": {"reviewerId": str(reviewer_id), "decision": "approved"}}
    try:
        normalize_payload(payload)
    except (TypeError, ValueError):
        return None
    return payload


def approval_review_message(candidate, source_items: list[dict]) -> str:
    fields = candidate.proposed_fields or {}
    lines = ["MCAD — REVIEW BEFORE PUBLICATION", "",
             "Title: " + str(fields.get("title") or candidate.title)[:250],
             "Year: " + str(fields.get("year") or "Unknown"),
             "Category: " + str(fields.get("victimType") or "Not specified")[:120],
             "Method: " + str(fields.get("method") or "Not specified")[:120],
             "", "Summary (AI-generated):", str(fields.get("summary") or candidate.summary or "Not supplied")[:650],
             "", "Sources to verify independently:"]
    seen = set()
    for source in source_items:
        if (source["host"], source["group"]) in seen:
            continue
        lines.append(source["url"])
        seen.add((source["host"], source["group"]))
        if len(seen) >= 4:
            break
    lines.extend(["", "Confirm ONLY after opening the source links and checking the title, details and categories.",
                  "Confirm makes this record PUBLIC. Use Cancel or the web admin panel to edit details."])
    return "\n".join(lines)[:4096]


def approval_session_key(candidate_id: str, reviewer_id: int) -> str:
    """Compact DB key for outstanding confirmations; user IDs may be 20 digits."""
    digest = hashlib.sha256(f"{reviewer_id}:{candidate_id}".encode("ascii")).hexdigest()
    return "tg_approval:" + digest[:48]
