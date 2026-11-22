"""Add relationship fields unless the legacy feature v5 already installed them."""
from sqlalchemy import inspect

from .v0005_candidate_relationships import upgrade as legacy_relationships


def upgrade(connection):
    columns = {column["name"] for column in inspect(connection).get_columns("candidates")}
    if "relationship_type" not in columns:
        legacy_relationships(connection)
