import unittest

from app.main import create_app


class ApplicationIntegrationTests(unittest.TestCase):
    def test_openapi_contains_all_public_contracts(self) -> None:
        paths = create_app().openapi()["paths"]

        self.assertEqual(len(paths), 71)
        self.assertIn("/login", paths)
        self.assertIn("/stock/{slug}/transfer", paths)
        self.assertIn("/stock/supplies", paths)
        self.assertIn("/stock/randomizer", paths)
        self.assertIn("/stock/randomizer/generate", paths)
        self.assertIn("/api/sales", paths)
        self.assertIn("/api/sales/wb-funnel-orders", paths)
        self.assertIn("/admin/users", paths)

    def test_every_operation_has_a_unique_identifier(self) -> None:
        schema = create_app().openapi()
        operation_ids = [
            operation["operationId"] for path in schema["paths"].values() for operation in path.values()
        ]

        self.assertEqual(len(operation_ids), len(set(operation_ids)))


if __name__ == "__main__":
    unittest.main()
