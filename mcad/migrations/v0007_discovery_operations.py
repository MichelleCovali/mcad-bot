"""Frozen v7: discovery usage, budget thresholds and temporary lead cache."""
from sqlalchemy import Column, DateTime, Index, MetaData, String, Table


cache = Table("ai_discovery_cache", MetaData(),
    Column("fingerprint", String(64), primary_key=True),
    Column("expires_at", DateTime, nullable=False),
)
Index("ix_ai_discovery_cache_expires_at", cache.c.expires_at)


def upgrade(connection):
    for table, columns in {
        "ai_config": ["cost_rates JSON NOT NULL DEFAULT '{}'", "daily_warning_micros INTEGER NOT NULL DEFAULT 0", "scope_focus VARCHAR(350) NOT NULL DEFAULT ''"],
        "ai_runs": ["stages JSON NOT NULL DEFAULT '[]'", "skipped_leads INTEGER NOT NULL DEFAULT 0", "prompt_version_id VARCHAR(36)"],
    }.items():
        for definition in columns:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {definition}")
    cache.create(connection, checkfirst=True)
