"""Frozen initial MCAD schema, version 1. Future changes require a new version."""
from pathlib import Path


def upgrade(connection):
    dialect = connection.dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("MCAD migrations support PostgreSQL and SQLite.")
    source = Path(__file__).with_name(f"v0001_{dialect}.sql").read_text()
    for statement in source.split(";"):
        if statement.strip():
            connection.exec_driver_sql(statement)
