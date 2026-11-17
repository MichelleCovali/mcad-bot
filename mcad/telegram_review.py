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
