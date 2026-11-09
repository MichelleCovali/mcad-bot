"""Frozen v3 schema: a paused monitor and review evidence, never publication."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, MetaData, String, Table


metadata = MetaData()
config_table = Table("ai_config", metadata,
    Column("id", Integer, primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("runs_per_day", Integer, nullable=False),
    Column("lookback_hours", Integer, nullable=False),
    Column("max_candidates", Integer, nullable=False),
    Column("max_tool_calls", Integer, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)
run_table = Table("ai_runs", metadata,
    Column("id", String(36), primary_key=True),
    Column("trigger", String(32), nullable=False),
    Column("status", String(32), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=True),
    Column("candidates_created", Integer, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("web_search_calls", Integer, nullable=False),
    Column("response_ids", JSON, nullable=False),
    Column("error", String(255), nullable=True),
)
Index("ix_ai_runs_status", run_table.c.status)
Index("ix_ai_runs_started_at", run_table.c.started_at)


def upgrade(connection):
    config_table.create(connection, checkfirst=True)
    run_table.create(connection, checkfirst=True)
    for declaration in (
        "origin VARCHAR(32) NOT NULL DEFAULT 'rss'",
        "proposed_fields JSON NOT NULL DEFAULT '{}'",
        "evidence JSON NOT NULL DEFAULT '{}'",
        "verification_status VARCHAR(32) NOT NULL DEFAULT 'unreviewed'",
        "ai_run_id VARCHAR(36) REFERENCES ai_runs(id)",
    ):
        connection.exec_driver_sql("ALTER TABLE candidates ADD COLUMN " + declaration)
    connection.exec_driver_sql("CREATE INDEX ix_candidates_ai_run_id ON candidates (ai_run_id)")
    connection.execute(config_table.insert().values(
        id=1, enabled=False, runs_per_day=3, lookback_hours=48,
        max_candidates=5, max_tool_calls=15, updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
