"""Record bounded research attempts separately from billable stage usage."""


def upgrade(connection):
    connection.exec_driver_sql("ALTER TABLE ai_runs ADD COLUMN coverage JSON NOT NULL DEFAULT '{}'")
