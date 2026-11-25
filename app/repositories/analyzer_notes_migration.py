from sqlalchemy import inspect

from app.core.constants import DatabaseDialect
from app.infrastructure.database import Database
from app.repositories import core


def migrate_analyzer_daily_notes(database: Database) -> None:
    with core.WRITE_LOCK, database.engine.connect() as connection:
        if database.dialect_name == DatabaseDialect.SQLITE:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.begin()
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('analyzer_daily_notes'))")

        tables = set(inspect(connection).get_table_names())
        if "rnp_action_log" in tables:
            if "analyzer_daily_notes" in tables:
                raise RuntimeError("Both the legacy and current analyzer daily notes tables exist")
            connection.exec_driver_sql("ALTER TABLE rnp_action_log RENAME TO analyzer_daily_notes")
            connection.exec_driver_sql("ALTER TABLE analyzer_daily_notes ADD COLUMN updated_at VARCHAR")
            connection.exec_driver_sql("ALTER TABLE analyzer_daily_notes ADD COLUMN updated_by VARCHAR")
            connection.exec_driver_sql("DROP INDEX IF EXISTS idx_rnp_action_lookup")
            connection.exec_driver_sql(
                "CREATE INDEX idx_analyzer_daily_note_lookup "
                "ON analyzer_daily_notes (store_slug, marketplace, article, action_date)"
            )
            if "rnp_action_revisions" in tables:
                connection.exec_driver_sql(
                    """
                    UPDATE analyzer_daily_notes
                    SET updated_at = (
                        SELECT edited_at FROM rnp_action_revisions
                        WHERE action_id = analyzer_daily_notes.id ORDER BY edited_at DESC, id DESC LIMIT 1
                    ), updated_by = (
                        SELECT user_name FROM rnp_action_revisions
                        WHERE action_id = analyzer_daily_notes.id ORDER BY edited_at DESC, id DESC LIMIT 1
                    )
                    WHERE EXISTS (SELECT 1 FROM rnp_action_revisions WHERE action_id = analyzer_daily_notes.id)
                    """
                )
        if "rnp_action_revisions" in tables:
            connection.exec_driver_sql("DROP TABLE rnp_action_revisions")
        connection.commit()
