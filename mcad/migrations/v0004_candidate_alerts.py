"""Frozen v4 schema for durable, opt-in AI candidate review alerts.

Published incident outboxes are intentionally untouched. New tables make this an
additive migration on PostgreSQL and SQLite, including existing production data.
"""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, MetaData, String, Table, UniqueConstraint


metadata = MetaData()
# Existing referenced tables are declared only for FK resolution, never created.
Table("candidates", metadata, Column("id", String(36), primary_key=True))
Table("subscribers", metadata, Column("chat_id", String(64), primary_key=True))

notifications = Table("candidate_notifications", metadata,
    Column("id", String(36), primary_key=True),
    Column("candidate_id", String(36), ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("candidate_id", name="uq_candidate_notifications_candidate"),
)
deliveries = Table("candidate_deliveries", metadata,
    Column("id", String(36), primary_key=True),
    Column("event_id", String(36), ForeignKey("candidate_notifications.id", ondelete="CASCADE"), nullable=False),
    Column("chat_id", String(64), ForeignKey("subscribers.chat_id"), nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", DateTime, nullable=False),
    Column("lease_owner", String(36), nullable=True),
    Column("lease_until", DateTime, nullable=True),
    Column("last_error", String(64), nullable=True),
    Column("telegram_message_id", String(64), nullable=True),
    Column("sent_at", DateTime, nullable=True),
    UniqueConstraint("event_id", "chat_id", name="uq_candidate_deliveries_event_chat"),
    CheckConstraint("status IN ('pending', 'retry', 'sending', 'sent', 'cancelled', 'failed')", name="candidate_delivery_status"),
)
for column in ("event_id", "chat_id", "status", "next_attempt_at"):
    Index("ix_candidate_deliveries_" + column, deliveries.c[column])


def upgrade(connection):
    notifications.create(connection, checkfirst=True)
    deliveries.create(connection, checkfirst=True)
