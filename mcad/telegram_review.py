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
