"""Opt-in transaction tests; never use the application's DATABASE_URL.

Set CHECKSTOCK_TEST_POSTGRES_URL to a disposable test database. Each test uses
a unique schema and drops only that schema, leaving public and other tests intact.
"""

import os
import unittest
from uuid import uuid4

import test_yandex_finance as finance_tests
from sqlalchemy import create_engine, text


@unittest.skipUnless(
    os.environ.get("CHECKSTOCK_TEST_POSTGRES_URL"), "Separate PostgreSQL test URL not supplied"
)
class FinancePostgresPersistence(finance_tests.FinancePersistence):
    def setUp(self):
        # Keep fixtures/clock from the ordinary suite; replace its isolated database.
        super().setUp()
        url = os.environ["CHECKSTOCK_TEST_POSTGRES_URL"]
        if not url.startswith(("postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")):
            raise ValueError("The test URL must name PostgreSQL")
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        engine = create_engine(url)
        self.addCleanup(engine.dispose)
        schema = "finance_test_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        def cleanup():
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

        self.addCleanup(cleanup)
        # SQLAlchemy applies the schema to every ORM statement, including FKs.
        self.engine = engine.execution_options(schema_translate_map={None: schema})
        from sqlalchemy.orm import sessionmaker

        from app.application.finance import FinanceService
        from app.infrastructure.finance_repository import FinanceRepository
        from app.infrastructure.orm import OrmBase

        tables = [t for name, t in OrmBase.metadata.tables.items() if name.startswith("finance_yandex_")]
        OrmBase.metadata.create_all(self.engine, tables=tables)
        self.repo = FinanceRepository(sessionmaker(self.engine, expire_on_commit=False))
        self.service = FinanceService(self.repo)
        self.conn = self.connect()

    def test_I08_common_sales_keys_are_untouched(self):
        # Raw SQL in the SQLite base test has no translated schema. Explicitly
        # route this sentinel to the isolated schema, never PostgreSQL public.
        schema = self.engine.get_execution_options()["schema_translate_map"][None]
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    f'CREATE TABLE "{schema}".sales_order_lines (order_key TEXT, line_key TEXT, quantity INTEGER)'
                )
            )
            connection.execute(text(f"INSERT INTO \"{schema}\".sales_order_lines VALUES ('9001','1',3)"))
        self.repo.publish(self.conn, self.parsed(), "test")
        with self.engine.connect() as connection:
            rows = connection.execute(text(f'SELECT * FROM "{schema}".sales_order_lines')).all()
        self.assertEqual(rows, [("9001", "1", 3)])

    def test_I02_transaction_rollback_including_heads(self):
        # The shared test's hook also needs to recognize schema-qualified INSERT.
        from sqlalchemy import event
        from test_yandex_finance import sale

        self.publish([sale()])
        before = self.report()["version"]

        def reject(conn, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO") and ".finance_yandex_operations " in statement:
                raise RuntimeError("insert failure")

        event.listen(self.engine, "before_cursor_execute", reject)
        try:
            with self.assertRaises(RuntimeError):
                self.publish([sale(seller="900")])
        finally:
            event.remove(self.engine, "before_cursor_execute", reject)
        self.assertEqual(self.report()["version"], before)
        self.assertEqual(self.report()["metrics"]["seller_turnover"]["value"], "100.00")
