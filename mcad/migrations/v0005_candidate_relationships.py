"""Add proposed incident relationships without changing existing reviews."""


def upgrade(connection):
    connection.exec_driver_sql(
        "ALTER TABLE candidates "
        "ADD COLUMN relationship_type VARCHAR(32) "
        "NOT NULL DEFAULT 'unclassified'"
    )
    connection.exec_driver_sql(
        "ALTER TABLE candidates "
        "ADD COLUMN related_incident_id VARCHAR(36) "
        "REFERENCES incidents(id)"
    )
    connection.exec_driver_sql(
        "ALTER TABLE candidates "
        "ADD COLUMN relationship_reason TEXT NOT NULL DEFAULT ''"
    )
    connection.exec_driver_sql(
        "CREATE INDEX ix_candidates_related_incident_id "
        "ON candidates (related_incident_id)"
    )