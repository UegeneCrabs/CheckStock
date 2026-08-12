import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from app import decision_center as dc
from app.container import ApplicationContainer
from app.dto.decision import DecisionStatus, DecisionStatusRequest
from app.dto.identity import Role, User
from app.infrastructure.database import database_for_path, dispose_databases
from app.infrastructure.orm import DecisionActionRecord, OrmBase


def _product(**overrides):
    item = {
        "store": "trusthome",
        "storeName": "TRUSTHOME",
        "nmId": 101,
        "article": "101",
        "name": "Тестовый товар",
        "imageUrl": "",
        "price": 1000,
        "purchaseCost": 400,
        "costModelled": False,
        "profitPerUnit": 280,
        "profitShare": 0.28,
        "stock": 2,
        "stockDays": 2,
        "orders": 28,
        "buyouts": 20,
        "cancels": 2,
        "revenue": 28000,
        "weeklyOrders": 7,
        "buyoutRate": 0.71,
        "views": 800,
        "carts": 240,
        "estimatedReach": 18000,
        "cartRate": 0.30,
        "checkoutRate": 0.117,
        "rating": 4.7,
        "deliveryDays": 2,
        "growth": 30,
        "visibility": 45,
        "avgPosition": 24,
        "adImpressions": 5000,
        "adClicks": 200,
        "adSpend": 2000,
        "adOrders": 12,
        "ctr": 0.04,
        "drr": 0.071,
        "health": 72,
        "dataUpdatedAt": "2026-08-10T10:00:00+00:00",
    }
    item.update(overrides)
    return item


class DecisionCenterTests(unittest.TestCase):
    def test_stock_risk_becomes_a_prioritized_decision(self):
        rows = dc._opportunities([_product()], {})
        stock = next(item for item in rows if item["fingerprint"].endswith(":stockout"))

        self.assertEqual(stock["domain"], "Наличие")
        self.assertEqual(stock["severity"], "critical")
        self.assertGreater(stock["expectedProfit"], 0)

    def test_dashboard_is_wb_only(self):
        product = _product()
        with (
            mock.patch.object(dc, "_build_products", return_value=[product]),
            mock.patch.object(dc, "_action_states", return_value={}),
            mock.patch.object(dc, "_sync_summary", return_value=[]),
        ):
            result = dc.dashboard(["trusthome"])

        self.assertEqual(result["meta"]["marketplace"], "WB")
        self.assertEqual(result["meta"]["marketplaceName"], "Wildberries")
        self.assertNotIn("OZON", str(result))
        self.assertNotIn("YANDEX", str(result))

    def test_action_status_survives_a_new_connection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "decision.db"
            dispose_databases()
            database = database_for_path(path)
            OrmBase.metadata.create_all(database.engine)
            container = ApplicationContainer(database_path=lambda: path)
            user = User(
                id=7,
                full_name="Менеджер",
                login="manager",
                role=Role.ADMIN,
                created_at=datetime(2026, 8, 12, tzinfo=UTC),
            )
            saved = container.decision_commands.set_status(
                DecisionStatusRequest(
                    fingerprint="trusthome:101:stockout",
                    status=DecisionStatus.IN_PROGRESS,
                ),
                user,
            )
            dispose_databases()
            database = database_for_path(path)
            with database.session_factory() as session:
                record = session.get(DecisionActionRecord, "trusthome:101:stockout")
                self.assertIsNotNone(record)
                persisted_status = record.status
            dispose_databases()

        self.assertEqual(saved.status, DecisionStatus.IN_PROGRESS)
        self.assertEqual(persisted_status, DecisionStatus.IN_PROGRESS.value)


if __name__ == "__main__":
    unittest.main()
