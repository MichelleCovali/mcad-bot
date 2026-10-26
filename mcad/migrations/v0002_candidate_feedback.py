
"""Add rejection feedback fields to candidates."""


def upgrade(connection):
    connection.exec_driver_sql(
        "ALTER TABLE candidates "
        "ADD COLUMN rejection_reason VARCHAR(64)"
    )

    connection.exec_driver_sql(
        "ALTER TABLE candidates "
        "ADD COLUMN rejection_notes TEXT"
    )
