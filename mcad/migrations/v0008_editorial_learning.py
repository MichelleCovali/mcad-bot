"""Frozen v8: mark editorial decisions included in a generated prompt draft."""


def upgrade(connection):
    connection.exec_driver_sql("ALTER TABLE telegram_feedback ADD COLUMN editorial_drafted_at TIMESTAMP NULL")
    connection.exec_driver_sql(
        "CREATE INDEX ix_telegram_feedback_editorial_drafts "
        "ON telegram_feedback (delivery_kind, editorial_drafted_at, created_at)"
    )
