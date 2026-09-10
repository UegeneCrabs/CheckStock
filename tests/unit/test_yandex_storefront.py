import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from app import db, unit_economics_yandex
from app.repositories import core, yandex_assortment
from app.repositories import yandex_storefront as repository
from app.yandex import storefront_prices as prices
from app.yandex.captcha_service import CaptchaError, Client
from app.yandex.storefront_schedule import TIMEZONE, next_run_at
from scripts import parse_yandex_storefront_prices as worker

TARGET = {"store_slug": "tris", "article": "153985484", "business_id": 132956982,
          "market_sku": "4653983171", "card_id": "103049914635",
          "url": "https://market.yandex.ru/card/slug/103049914635"}


def offer(value=2883, **changes):
    return {"businessId": "132956982", "marketSku": "4653983171", "oskuId": "103049914635",
            "prices": {"price": {"value": value, "currency": "RUR"},
                       "greenPrice": {"price": {"value": 2825}}, "oldPrice": 10000}, **changes}


def page(offers):
    return '<noframes data-apiary="patch">' + json.dumps({"collections": {"offerAnalytics": offers}}) + '</noframes>'


class ScheduleTests(unittest.TestCase):
    def test_day_night_and_day_rollover(self):
        cases = [
            ("2026-09-10T00:30:00+05:00", "2026-09-10T01:00:00+05:00"),
            ("2026-09-10T01:00:00+05:00", "2026-09-10T08:00:00+05:00"),
            ("2026-09-10T07:59:59+05:00", "2026-09-10T08:00:00+05:00"),
            ("2026-09-10T08:04:00+05:00", "2026-09-10T09:00:00+05:00"),
            ("2026-09-10T18:05:00+05:00", "2026-09-10T19:00:00+05:00"),
            ("2026-09-10T19:04:00+05:00", "2026-09-11T01:00:00+05:00"),
            ("2026-12-31T23:59:00+05:00", "2027-01-01T01:00:00+05:00"),
        ]
        for current, expected in cases:
            with self.subTest(current=current):
                self.assertEqual(next_run_at(datetime.fromisoformat(current)), datetime.fromisoformat(expected))

    def test_thirteen_checks_daily_including_weekends_independent_of_server_timezone(self):
        current = datetime(2026, 9, 12, tzinfo=TIMEZONE).astimezone(UTC)
        scheduled = []
        for _ in range(13):
            current = next_run_at(current)
            scheduled.append(current.astimezone(TIMEZONE))
        self.assertEqual([value.hour for value in scheduled], [1, *range(8, 20)])
        self.assertTrue(all(value.day == 12 and value.minute == 0 for value in scheduled))
        self.assertEqual(next_run_at(current).astimezone(TIMEZONE).hour, 1)

    @patch.object(worker, "set_next_run")
    def test_sleeping_past_daytime_slot_waits_for_next_allowed_start(self, set_next_run):
        clock = datetime(2026, 9, 10, 18, 59, tzinfo=TIMEZONE)
        waits = 0

        def wait(milliseconds):
            nonlocal clock, waits
            waits += 1
            if waits == 1:
                clock = clock.replace(hour=23, minute=59)  # computer wakes after 19:00
            else:
                clock += timedelta(milliseconds=milliseconds)

        page_mock = Mock()
        page_mock.wait_for_timeout.side_effect = wait
        with patch.object(worker, "datetime") as dates:
            dates.now.side_effect = lambda zone: clock.astimezone(zone)
            worker.wait_for_next_run(page_mock)
        self.assertEqual((clock.day, clock.hour, clock.minute), (11, 1, 0))
        self.assertEqual(set_next_run.call_count, 2)


class ParserTests(unittest.TestCase):
    def test_ordinary_price_is_selected_for_exact_seller_and_card(self):
        result = prices.parse_prices(page({"own": offer(), "other": offer(12, businessId="999"),
                                           "recommendation": offer(15, oskuId="222")}), TARGET["url"], TARGET)
        self.assertEqual(result["buyer_price"], 2883)
        self.assertEqual(result["offer_key"], "own")

    def test_pay_old_and_non_ruble_prices_are_never_fallbacks(self):
        for value in (None, True, "NaN", "inf", 0, -10):
            with self.subTest(value=value):
                self.assertIsNone(prices.parse_prices(page({"own": offer(value)}), TARGET["url"], TARGET)["buyer_price"])
        for price_data in ({"greenPrice": {"price": {"value": 100}}, "oldPrice": 10000},
                           {"price": {"value": 100, "currency": "USD"}}):
            self.assertIsNone(prices.parse_prices(page({"own": offer(prices=price_data)}), TARGET["url"], TARGET)["buyer_price"])

    def test_wrong_card_redirect_and_ambiguous_offers(self):
        self.assertEqual(prices.parse_prices(page({"own": offer()}), "https://market.yandex.ru/card/slug/999", TARGET)["status"], "wrong_page")
        self.assertEqual(prices.parse_prices(page({"a": offer(), "b": offer(4000)}), TARGET["url"], TARGET)["status"], "ambiguous")
        self.assertEqual(prices.parse_prices("", "https://market.yandex.ru/showcaptcha", TARGET)["status"], "captcha")
        self.assertIsNone(prices.parse_prices(page({"own": offer(marketSku=None, oskuId=None)}), TARGET["url"], {**TARGET, "market_sku": ""})["buyer_price"])
        self.assertIsNone(prices.parse_prices(page({"own": offer(prices={"price": 12})}), TARGET["url"], TARGET)["buyer_price"])

    def test_sold_out_page_and_script_text_are_distinguished(self):
        self.assertEqual(prices.parse_prices("<h1>Товар распродан</h1>", TARGET["url"], TARGET)["status"], "out_of_stock")
        self.assertEqual(prices.parse_prices("<h1>Разобрали в магазине</h1>", TARGET["url"], TARGET)["status"], "out_of_stock")
        self.assertEqual(prices.parse_prices('<script>var unused="Товар распродан";</script>', TARGET["url"], TARGET)["status"], "price_missing")

    def test_authoritative_top_level_b2c_link(self):
        mapping = {"showcaseUrls": [{"showcaseType": "B2B", "showcaseUrl": "https://business.market.yandex.ru/card/slug/9"},
                                    {"showcaseType": "B2C", "showcaseUrl": TARGET["url"]}]}
        self.assertEqual(prices.showcase_url(mapping, TARGET["business_id"]), TARGET["url"] + "?businessId=132956982")
        self.assertIsNone(prices.card_id("https://market.yandex.ru.example.com/card/slug/123"))
        self.assertIsNone(prices.showcase_url({"offer": mapping}, TARGET["business_id"]))

    @patch.object(prices.api, "request")
    def test_paginated_mapping_ignores_unrequested_products(self, request):
        request.side_effect = [
            {"offerMappings": [{"offer": {"offerId": "a"}}], "paging": {"nextPageToken": "next"}},
            {"offerMappings": [{"offer": {"offerId": "b"}}, {"offer": {"offerId": "other"}}]},
        ]
        self.assertEqual(set(prices.load_mappings("test", 1, ["a", "b"])), {"a", "b"})
        self.assertEqual(request.call_args.kwargs["params"]["page_token"], "next")


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state_dir = Path(temp.name)
        for mocked in (patch.object(core, "DB_PATH", self.state_dir / "test.db"),
                       patch.object(yandex_assortment, "load_active_products", return_value={("tris", TARGET["article"])})):
            mocked.start()
            self.addCleanup(mocked.stop)
        db.init_db()
        db.replace_catalog("tris", "YANDEX MARKET", [{"article": TARGET["article"], "mp_sku": TARGET["market_sku"]}], repository.now())
        repository.save_target(TARGET)

    def test_price_reaches_calculator_and_failure_preserves_last_known_only(self):
        repository.seller_price(TARGET, 3500)
        repository.record(TARGET, {"status": "ok", "buyer_price": 2883, "currency": "RUR"})
        product = unit_economics_yandex.load_products(("tris",))[0]
        self.assertEqual(product["details"]["customer_price"], 2883)
        self.assertEqual(product["details"]["retail_price"], 3500)
        self.assertIsNone(product["price"]["with_wallet"])
        repository.record(TARGET, {"status": "captcha_blocked", "message": "Капча"})
        product = unit_economics_yandex.load_products(("tris",))[0]
        self.assertIsNone(product["details"]["customer_price"])
        self.assertEqual(product["price_check"]["last_known_buyer_price"], 2883)
        self.assertTrue(product["price_check"]["is_stale"])
        self.assertEqual(repository.get_prices("rimili"), {})

    def test_changed_card_clears_old_price(self):
        repository.record(TARGET, {"status": "ok", "buyer_price": 2883})
        repository.save_target({**TARGET, "card_id": "999"})
        self.assertIsNone(repository.get_prices("tris")[TARGET["article"]]["buyer_price"])

    def test_import_missing_selected_product_preserves_catalog_and_stocks(self):
        db.upsert_mp_stock("tris", TARGET["article"], "YANDEX MARKET", "fbs", 7, repository.now())
        product = {"article": "new", "barcode": "1234", "name": "Из API", "market_sku": "99"}
        repository.add_missing_catalog_item("tris", product)
        repository.add_missing_catalog_item("tris", {**product, "name": "Не затирать"})
        catalog = {item["article"]: item for item in db.get_catalog_items("tris", "YANDEX MARKET")}
        self.assertEqual(set(catalog), {TARGET["article"], "new"})
        self.assertEqual(catalog["new"]["name"], "Из API")
        rows = {row["article"]: row for row in db.get_stock_items("tris", "YANDEX MARKET", ("fbs",))}
        self.assertEqual(rows[TARGET["article"]]["fbs_stock"], 7)

    def test_prepare_imports_selected_offer_missing_from_local_catalog(self):
        row = {"offer": {"offerId": "new", "name": "Из API", "barcodes": ["1234"]},
               "mapping": {"marketSku": "99"},
               "showcaseUrls": [{"showcaseType": "B2C", "showcaseUrl": "https://market.yandex.ru/card/slug/123"}]}
        with patch.object(prices.tokens, "has_credentials", return_value=True), \
                patch.object(prices.tokens, "get_api_key", return_value="test"), \
                patch.object(prices, "resolve_business_id", return_value=1), \
                patch.object(prices, "load_mappings", return_value={"new": row}) as mapping, \
                patch.object(prices.api, "request", return_value={"offers": [{"offerId": "new", "price": {"value": 999}}]}):
            targets, skipped = prices.prepare({("tris", "new")})
        mapping.assert_called_once_with("test", 1, ["new"])
        self.assertEqual(skipped, [])
        self.assertEqual(targets[0]["article"], "new")
        self.assertEqual(repository.get_prices("tris")["new"]["seller_price"], 999)

    def test_stale_prices_and_lease_exclusion(self):
        current = datetime(2026, 9, 10, 14, tzinfo=TIMEZONE)
        self.assertTrue(repository.fresh(current.isoformat(), current))
        self.assertFalse(repository.fresh((current - timedelta(hours=3)).isoformat(), current))
        self.assertFalse(repository.fresh("bad"))
        self.assertTrue(repository.acquire("first"))
        self.assertFalse(repository.acquire("second"))
        repository.release("second")
        self.assertFalse(repository.acquire("second"))
        repository.release("first")
        self.assertTrue(repository.acquire("second"))

    def test_prices_remain_available_during_planned_night_pause(self):
        evening = datetime(2026, 9, 10, 19, 5, tzinfo=TIMEZONE)
        self.assertTrue(repository.fresh(evening.isoformat(), evening.replace(hour=23)))
        self.assertFalse(repository.fresh(evening.isoformat(), evening + timedelta(hours=8)))
        night = datetime(2026, 9, 11, 1, 5, tzinfo=TIMEZONE)
        self.assertTrue(repository.fresh(night.isoformat(), night.replace(hour=8)))
        self.assertFalse(repository.fresh(night.isoformat(), night.replace(hour=9, minute=1)))

    def test_captcha_stops_batch_and_releases_lease(self):
        args = worker.arguments(["--state-dir", str(self.state_dir), "--delay", "0"])
        browser = Mock(client=None)
        browser.fetch.side_effect = CaptchaError("Проверка")
        browser.diagnostics.return_value = {"title": "Вы не робот?"}
        with patch.object(worker, "prepare", return_value=([TARGET, {**TARGET, "article": "second"}], [])):
            result = worker.run_once(browser, args)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["remaining"], 1)
        self.assertEqual(browser.fetch.call_count, 1)
        self.assertTrue(repository.acquire("new"))

    def test_scheduled_selection_respects_store_settings(self):
        with patch.object(worker.sync_settings, "enabled_stores", return_value=()):
            self.assertEqual(worker.selection(worker.arguments(["--loop"])), set())
        self.assertEqual(worker.selection(worker.arguments([])), {("tris", TARGET["article"])})

    def test_expanded_assortment_becomes_visible_without_app_restart(self):
        repository.add_missing_catalog_item("tris", {"article": "added-later", "barcode": "123", "name": "Новый"})
        with patch.object(yandex_assortment, "load_active_products", return_value={("tris", "added-later")}):
            repository.refresh_assortment()
        self.assertEqual(yandex_assortment.active_articles("tris"), {"added-later"})


class CaptchaTests(unittest.TestCase):
    @patch.object(worker, "Client")
    def test_graphical_instruction_uses_canvas_and_never_hidden_text_input(self, client):
        page_mock = Mock()
        checkbox, main, instruction, text_input = Mock(), Mock(), Mock(), Mock()
        checkbox.count.return_value = 0
        main.count.return_value = instruction.count.return_value = 1
        main.is_visible.return_value = instruction.is_visible.return_value = True
        main.screenshot.return_value = b"image"
        main.bounding_box.return_value = {"x": 100, "y": 200, "width": 300, "height": 180}
        instruction.locator.return_value.count.return_value = 1
        instruction.locator.return_value.evaluate.return_value = "native-canvas-base64"
        locators = {"#js-button": checkbox,
                    ".AdvancedCaptcha-ImageWrapper img, img.AdvancedCaptcha-Image": Mock(first=main),
                    ".AdvancedCaptcha-SilhouetteTask .TaskImage": Mock(first=instruction),
                    'input[name="rep"]:not([type="hidden"])': text_input}
        page_mock.locator.side_effect = locators.__getitem__
        client.return_value.solve.return_value = {"coordinates": [{"x": 10, "y": 20}]}
        browser = worker.Browser(page_mock, worker.arguments([]))
        browser.captcha = Mock(side_effect=[True, False])
        browser.solve_captcha()
        task = client.return_value.solve.call_args.args[0]
        self.assertEqual(task["type"], "SmartCaptchaTask")
        self.assertEqual(task["imgInstructions"], "native-canvas-base64")
        page_mock.mouse.click.assert_called_once_with(110, 220)
        text_input.fill.assert_not_called()

    def test_timeout_after_captcha_does_not_discard_rendered_card(self):
        page_mock = Mock()
        page_mock.goto.side_effect = [Mock(status=200), TimeoutError()]
        page_mock.url = TARGET["url"]
        page_mock.content.return_value = page({"own": offer()})
        browser = worker.Browser(page_mock, worker.arguments([]))
        browser.captcha = Mock(side_effect=[True, False])
        browser.solve_captcha = Mock()
        self.assertEqual(browser.fetch(TARGET)["buyer_price"], 2883)

    @patch("app.yandex.captcha_service.time.sleep")
    def test_one_task_polling_cost_and_budget(self, sleep):
        client = Client("test-key", max_tasks=1)
        client.request = Mock(side_effect=[{"taskId": 123}, {"status": "processing"},
                                          {"status": "ready", "cost": "0.0012", "solution": {"text": "answer"}}])
        self.assertEqual(client.solve({"type": "ImageToTextTask"}), {"text": "answer"})
        self.assertEqual(client.cost, Decimal("0.0012"))
        with self.assertRaises(CaptchaError):
            client.solve({})
        self.assertEqual([call.args[0] for call in client.request.call_args_list], ["createTask", "getTaskResult", "getTaskResult"])

    @patch("app.yandex.captcha_service.urllib.request.urlopen", side_effect=OSError("secret request"))
    def test_uncertain_create_task_is_not_retried_or_leaked(self, request):
        with self.assertRaisesRegex(CaptchaError, "ошибка соединения") as raised:
            Client("private-key").solve({})
        self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(request.call_count, 1)

    @patch.object(worker, "Client")
    def test_free_checkbox_does_not_submit_paid_task(self, client):
        page_mock = Mock()
        browser = worker.Browser(page_mock, worker.arguments([]))
        browser.captcha = Mock(side_effect=[True, False, False])
        browser.solve_captcha()
        client.assert_not_called()
        page_mock.locator.return_value.click.assert_called_once()

    @patch.object(worker, "Client")
    def test_unknown_challenge_does_not_submit_paid_task(self, client):
        page_mock = Mock()
        page_mock.locator.return_value.count.return_value = 0
        page_mock.locator.return_value.first.count.return_value = 0
        browser = worker.Browser(page_mock, worker.arguments([]))
        browser.captcha = Mock(return_value=True)
        with self.assertRaisesRegex(CaptchaError, "Неизвестный вариант"):
            browser.solve_captcha()
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
