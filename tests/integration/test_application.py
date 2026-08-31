import unittest

from app.main import create_app


class ApplicationIntegrationTests(unittest.TestCase):
    def test_openapi_contains_all_public_contracts(self) -> None:
        paths = create_app().openapi()["paths"]

        self.assertEqual(len(paths), 101)
        self.assertIn("/login", paths)
        self.assertIn("/stock/{slug}/transfer", paths)
        self.assertIn("/stock/{slug}/transfers/in-transit", paths)
        self.assertIn("/stock/{slug}/total-data", paths)
        self.assertIn("/stock/{slug}/transfers/{transfer_id}/receive", paths)
        self.assertIn("/stock/{slug}/transfers/{transfer_id}/cancel", paths)
        self.assertIn("/stock/supplies", paths)
        self.assertIn("/stock/randomizer", paths)
        self.assertIn("/stock/randomizer/generate", paths)
        self.assertIn("/api/sales", paths)
        self.assertIn("/api/sales/wb-funnel-orders", paths)
        self.assertIn("/sales/unit-economics-1c", paths)
        self.assertIn("/sales/unit-economics-1c/ozon", paths)
        self.assertIn("/sales/unit-economics-1c/yandex-market", paths)
        self.assertIn("/sales/unit-economics-1c/cabinet-settings", paths)
        self.assertIn("/api/unit-economics-1c/cabinet-settings", paths)
        self.assertIn("/api/unit-economics-1c/cabinet-settings/{store_slug}", paths)
        self.assertIn("/api/unit-economics-1c/source-data/sync", paths)
        self.assertIn("/api/unit-economics-1c/prices/preview", paths)
        self.assertIn("/api/unit-economics-1c/prices/sync", paths)
        self.assertIn("/api/unit-economics-1c/sync", paths)
        self.assertIn("/api/unit-economics-1c/product-settings/{store_slug}", paths)
        self.assertIn("/sales/unit-economics-1c/reports/unit-profit.xlsx", paths)
        self.assertIn("/api/unit-economics-1c/reports/unit-profit/filters", paths)
        self.assertNotIn("/sales/unit-economics", paths)
        self.assertNotIn("/sales/unit-economics/wb-fbs", paths)
        self.assertIn("/admin/users", paths)
        self.assertIn("/admin/users/{user_id}/access-policy", paths)
        self.assertIn("/admin/access-requests/{request_id}/decision", paths)
        self.assertIn("/admin/access-grants/{grant_id}/revoke", paths)

    def test_every_operation_has_a_unique_identifier(self) -> None:
        schema = create_app().openapi()
        operation_ids = [
            operation["operationId"] for path in schema["paths"].values() for operation in path.values()
        ]

        self.assertEqual(len(operation_ids), len(set(operation_ids)))


if __name__ == "__main__":
    unittest.main()
