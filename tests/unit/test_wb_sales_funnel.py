import unittest
from datetime import date, timedelta
from unittest import mock

from app.wb import sales_funnel


def _response(rows: list[dict]) -> dict:
    return {"data": {"products": rows}}


def _product(nm_id: int, *, views: int = 100, carts: int = 20, orders: int = 10, buyouts: int = 7) -> dict:
    return {
        "product": {"nmId": nm_id, "title": f"Товар {nm_id}", "vendorCode": f"SKU-{nm_id}"},
        "statistic": {
            "selected": {
                "openCount": views,
                "cartCount": carts,
                "addToWishList": 5,
                "orderCount": orders,
                "buyoutCount": buyouts,
                "cancelCount": 1,
                "orderSum": 1_234.5,
            },
            "comparison": {"orderCountDynamic": 12.5},
        },
    }


class WbSalesFunnelTests(unittest.TestCase):
    def setUp(self) -> None:
        sales_funnel._cache.clear()

    def tearDown(self) -> None:
        sales_funnel._cache.clear()

    def test_dashboard_requests_selected_and_comparison_periods(self) -> None:
        start = date.today() - timedelta(days=29)
        end = date.today()
        with (
            mock.patch.object(sales_funnel.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(sales_funnel.wb_api, "request", return_value=_response([_product(10)])) as request,
        ):
            payload = sales_funnel.dashboard("RIMILI", start.isoformat(), end.isoformat())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["store"], "rimili")
        self.assertEqual(payload["totals"]["views"], 100)
        self.assertEqual(payload["totals"]["favorites"], 5)
        self.assertEqual(payload["totals"]["view_to_cart"], 20.0)
        self.assertEqual(payload["products"][0]["article"], "SKU-10")
        body = request.call_args.kwargs["json_body"]
        self.assertEqual(body["selectedPeriod"], {"start": start.isoformat(), "end": end.isoformat()})
        self.assertEqual(
            body["pastPeriod"],
            {
                "start": (start - timedelta(days=30)).isoformat(),
                "end": (start - timedelta(days=1)).isoformat(),
            },
        )
        self.assertEqual(body["offset"], 0)

    def test_dashboard_uses_offset_for_a_following_full_page(self) -> None:
        start = date.today() - timedelta(days=1)
        with (
            mock.patch.object(sales_funnel, "PAGE_SIZE", 2),
            mock.patch.object(sales_funnel.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                sales_funnel.wb_api,
                "request",
                side_effect=[_response([_product(1), _product(2)]), _response([_product(3)])],
            ) as request,
        ):
            payload = sales_funnel.dashboard("rimili", start.isoformat(), date.today().isoformat())

        self.assertEqual(len(payload["products"]), 3)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].kwargs["json_body"]["offset"], 2)

    def test_dashboard_rejects_invalid_period(self) -> None:
        with self.assertRaisesRegex(ValueError, "не больше 90"):
            sales_funnel.dashboard("rimili", "2026-01-01", "2026-05-01")
        with self.assertRaisesRegex(ValueError, "Выберите магазин"):
            sales_funnel.dashboard("", date.today().isoformat(), date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
