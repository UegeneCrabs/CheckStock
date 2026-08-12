import unittest

from app.dto.stores import Store, StoreCollection, StoreItem
from app.dto.unit_economics import PersistedFulfillmentRates
from app.wb.unit_economics_config import build_configuration


class UnitEconomicsConfigurationTests(unittest.TestCase):
    def test_store_and_fulfillment_defaults_are_built_in_one_place(self) -> None:
        configuration = build_configuration(
            StoreCollection(
                (
                    StoreItem(
                        slug="tris",
                        store=Store(name="TRIS", color="#000000", initials="TR", text="#fff"),
                    ),
                    StoreItem(
                        slug="unknown",
                        store=Store(name="New", color="#000000", initials="NE", text="#fff"),
                    ),
                )
            ),
            PersistedFulfillmentRates.model_validate(
                [
                    {
                        "name": "FF",
                        "storage_per_m3_day": 1,
                        "acceptance_per_unit": 2,
                        "fulfillment_per_unit": 3,
                    }
                ]
            ),
        )

        self.assertEqual(configuration.stores[0].tax, 8)
        self.assertEqual(configuration.stores[1].tax, 6)
        self.assertEqual(configuration.fulfillments[0].fulfillment, 3)
        self.assertEqual(configuration.defaults.stock_days, 56)


if __name__ == "__main__":
    unittest.main()
