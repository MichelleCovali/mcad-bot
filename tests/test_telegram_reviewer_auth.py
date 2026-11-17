import pytest

from mcad.config import Settings, parse_reviewer_ids
from mcad.telegram_review import is_authorized_review_callback


def callback(user_id=123, chat_id=123, chat_type="private"):
    return {"from": {"id": user_id}, "message": {"chat": {"id": chat_id, "type": chat_type}}}


def test_ids_default_deny():
    assert parse_reviewer_ids("") == frozenset()
    assert not is_authorized_review_callback(callback(), frozenset())


def test_ids_are_parsed_and_deduplicated():
    assert parse_reviewer_ids("123, 456,123") == frozenset({123, 456})


@pytest.mark.parametrize("value", ["foo", "123,", "-123", "@username", "0", "123;456"])
def test_invalid_ids_fail_fast(value):
    with pytest.raises(ValueError, match="TELEGRAM_REVIEWER_IDS"):
        parse_reviewer_ids(value)


def test_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_REVIEWER_IDS", "123, 456")
    assert Settings.from_env().telegram_reviewer_ids == frozenset({123, 456})


def test_allowed_user_in_private_chat():
    assert is_authorized_review_callback(callback(), frozenset({123}))


@pytest.mark.parametrize("event", [
    callback(user_id=999, chat_id=999),
    callback(chat_id=999),
    callback(chat_type="group"),
    {"from": {"id": True}, "message": {"chat": {"id": 123, "type": "private"}}},
    {},
])
def test_unauthorized_events_are_blocked(event):
    assert not is_authorized_review_callback(event, frozenset({123}))
