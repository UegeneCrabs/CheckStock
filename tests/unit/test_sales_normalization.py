import unittest
from datetime import date

from app import sales


class SalesNormalizationTests(unittest.TestCase):
    def test_scalar_helpers_and_windows(self) -> None:
        self.assertEqual(sales._number({"amount": "1 234,5"}), 1234.5)
        self.assertEqual(sales._number("bad", 7), 7)
        self.assertEqual(sales._integer("3.9"), 3)
        self.assertEqual(sales._integer(None, 4), 4)
        self.assertTrue(sales._timestamp("11-08-2026").startswith("2026-08-11T00:00:00"))
        self.assertEqual(
            list(sales._windows(date(2026, 1, 1), date(2026, 1, 6), 2)),
            [
                (date(2026, 1, 1), date(2026, 1, 3)),
                (date(2026, 1, 3), date(2026, 1, 5)),
                (date(2026, 1, 5), date(2026, 1, 6)),
            ],
        )

    def test_wb_orders_include_sales_returns_and_cancellations(self) -> None:
        orders = [
            {
                "srid": "sale-1",
                "gNumber": "order-1",
                "nmId": 101,
                "barcode": "4601",
                "supplierArticle": "A-1",
                "finishedPrice": 900,
                "warehouseType": "Склад продавца",
                "date": "2026-08-10T10:00:00Z",
            },
            {
                "srid": "cancel-1",
                "gNumber": "order-2",
                "barcode": "4602",
                "finishedPrice": 500,
                "isCancel": True,
                "cancelDate": "2026-08-11T10:00:00Z",
                "date": "2026-08-10T10:00:00Z",
            },
        ]
        sales_rows = [
            {"srid": "sale-1", "saleID": "S1", "finishedPrice": 900, "date": "2026-08-11"},
            {"srid": "sale-1", "saleID": "R1", "finishedPrice": 200, "date": "2026-08-12"},
        ]

        normalized = sales._normalize_wb("rimili", orders, sales_rows)

        self.assertEqual(normalized[0]["scheme"], "fbs")
        self.assertEqual(normalized[0]["article"], "101")
        self.assertEqual(normalized[0]["sale_amount"], 700)
        self.assertEqual(normalized[0]["return_amount"], 200)
        self.assertEqual(normalized[1]["status"], "cancelled")
        self.assertEqual(normalized[1]["cancelled_amount"], 500)

    def test_wb_orders_include_all_nm_ids(self) -> None:
        orders = [
            {"srid": "active", "nmId": 111, "finishedPrice": 900, "date": "2026-08-10"},
            {"srid": "old", "nmId": 222, "finishedPrice": 500, "date": "2026-08-10"},
        ]

        normalized = sales._normalize_wb("trusthome", orders, [])

        self.assertEqual([row["order_key"] for row in normalized], ["active", "old"])

    def test_wb_fbs_orders_preserve_all_supplier_and_wb_statuses(self) -> None:
        orders = [
            {
                "id": 10,
                "rid": "active",
                "orderUid": "order-10",
                "article": "A-10",
                "nmId": 110,
                "skus": ["46010"],
                "convertedFinalPrice": 123450,
                "createdAt": "2026-08-10T10:00:00Z",
            },
            {
                "id": 11,
                "rid": "cancelled",
                "skus": ["46011"],
                "convertedPrice": 50000,
                "createdAt": "2026-08-11T10:00:00Z",
            },
        ]
        statuses = {
            10: {"supplierStatus": "complete", "wbStatus": "sorted"},
            11: {"supplierStatus": "confirm", "wbStatus": "canceled_by_client"},
        }

        normalized = sales._normalize_wb_fbs("rimili", orders, statuses)

        self.assertEqual((normalized[0]["status"], normalized[0]["substatus"]), ("complete", "sorted"))
        self.assertEqual(normalized[0]["article"], "110")
        self.assertEqual(normalized[0]["order_amount"], 1234.5)
        self.assertEqual(normalized[0]["cancelled_quantity"], 0)
        self.assertEqual(normalized[1]["status"], "confirm")
        self.assertEqual(normalized[1]["substatus"], "canceled_by_client")
        self.assertEqual(normalized[1]["cancelled_quantity"], 1)
        self.assertEqual(normalized[1]["cancelled_amount"], 500)

    def test_wb_fbs_statuses_enrich_matching_statistics_rows_without_duplicates(self) -> None:
        statistics = [
            {
                "order_key": "rid-1",
                "line_key": "4601",
                "scheme": "fbs",
                "status": "ordered",
                "substatus": "",
                "sold_quantity": 0,
                "cancelled_quantity": 0,
                "sale_amount": 0,
                "cancelled_amount": 0,
                "raw_json": '{"nmId":1}',
            }
        ]
        fbs = [
            {
                "order_key": "rid-1",
                "line_key": "other-key",
                "scheme": "fbs",
                "status": "complete",
                "substatus": "sold",
                "sold_quantity": 1,
                "cancelled_quantity": 0,
                "sale_amount": 900,
                "cancelled_amount": 0,
            }
        ]

        merged = sales._merge_wb_lines(statistics, fbs)

        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["status"], merged[0]["substatus"]), ("complete", "sold"))
        self.assertEqual(merged[0]["sold_quantity"], 1)
        self.assertEqual(merged[0]["raw_json"], '{"nmId":1}')

    def test_ozon_delivered_and_cancelled_lines(self) -> None:
        postings = [
            {
                "posting_number": "posting-1",
                "status": "delivered",
                "created_at": "2026-08-10T10:00:00Z",
                "updated_at": "2026-08-11T10:00:00Z",
                "products": [{"sku": 10, "offer_id": "A-1", "quantity": 2, "price": "300"}],
            },
            {
                "posting_number": "posting-2",
                "status": "cancelled",
                "created_at": "2026-08-10T10:00:00Z",
                "products": [{"sku": 11, "offer_id": "A-2", "quantity": 1, "price": "400"}],
            },
        ]

        normalized = sales._normalize_ozon("rimili", postings, "fbo")

        self.assertEqual(normalized[0]["sold_quantity"], 2)
        self.assertEqual(normalized[0]["sale_amount"], 600)
        self.assertEqual(normalized[1]["cancelled_quantity"], 1)
        self.assertEqual(normalized[1]["cancelled_amount"], 400)

    def test_yandex_statuses_split_amounts(self) -> None:
        orders = [
            {
                "id": 42,
                "status": "DELIVERED",
                "programType": "FBY",
                "creationDate": "2026-08-10",
                "items": [
                    {
                        "id": 7,
                        "offerId": "A-7",
                        "count": 3,
                        "prices": {"payment": 2400, "cashback": 300},
                        "itemStatuses": [
                            {"status": "DELIVERED_TO_BUYER", "count": 2},
                            {"status": "CANCELLED", "count": 1},
                        ],
                    }
                ],
            }
        ]

        normalized = sales._normalize_yandex("rimili", orders)

        self.assertEqual(normalized[0]["scheme"], "fbo")
        self.assertEqual(normalized[0]["sold_quantity"], 2)
        self.assertEqual(normalized[0]["cancelled_quantity"], 1)
        self.assertEqual(normalized[0]["sale_amount"], 1800)
        self.assertEqual(normalized[0]["cancelled_amount"], 900)


if __name__ == "__main__":
    unittest.main()
