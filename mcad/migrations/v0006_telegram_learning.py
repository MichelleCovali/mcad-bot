"""Frozen, additive v6 schema for Telegram feedback and prompt releases."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, MetaData, String, Table, Text, UniqueConstraint


metadata = MetaData()
feedback = Table("telegram_feedback", metadata,
    Column("id", String(36), primary_key=True),
    Column("delivery_kind", String(1), nullable=False),
    Column("delivery_id", String(36), nullable=False),
    Column("actor_id", String(24), nullable=False),
    Column("title", Text, nullable=False),
    Column("source_url", Text),
    Column("evidence", Text, nullable=False),
    Column("reason", String(32), nullable=False),
    Column("comment", Text, nullable=False),
    Column("status", String(12), nullable=False),
    Column("review_notes", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("reviewed_at", DateTime),
    UniqueConstraint("delivery_kind", "delivery_id", "actor_id", name="uq_telegram_feedback_actor"),
)
Index("ix_telegram_feedback_status", feedback.c.status)

versions = Table("learning_prompt_versions", metadata,
    Column("id", String(36), primary_key=True),
    Column("summary", Text, nullable=False),
    Column("discovery_instructions", Text, nullable=False),
    Column("verification_instructions", Text, nullable=False),
    Column("created_by_ai", Boolean, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("last_activated_at", DateTime),
)

results = Table("learning_prompt_case_results", metadata,
    Column("version_id", String(36), ForeignKey("learning_prompt_versions.id", ondelete="CASCADE"), primary_key=True),
    Column("feedback_id", String(36), ForeignKey("telegram_feedback.id", ondelete="CASCADE"), primary_key=True),
    Column("verdict", String(32), nullable=False),
    Column("rationale", Text, nullable=False),
    Column("model", String(100), primary_key=True),
    Column("evaluated_at", DateTime, nullable=False),
)


def upgrade(connection):
    feedback.create(connection, checkfirst=True)
    versions.create(connection, checkfirst=True)
    results.create(connection, checkfirst=True)
