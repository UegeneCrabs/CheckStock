import json
import logging
import unittest
from unittest import mock

from app import bitrix_alerts
from scripts import health_watchdog


class BitrixAlertTests(unittest.TestCase):
    def test_error_record_is_redacted_and_contains_request_context(self) -> None:
        try:
            raise RuntimeError("token=very-secret")
        except RuntimeError:
            record = logging.LogRecord(
                "app.test",
                logging.ERROR,
                __file__,
                1,
                "request failed password=%s",
                ("hidden",),
                exc_info=__import__("sys").exc_info(),
            )
        record.request_id = "request-123"

        message = bitrix_alerts.format_error_record(record)

        self.assertIn("request-123", message)
        self.assertIn("RuntimeError", message)
        self.assertNotIn("very-secret", message)
        self.assertNotIn("hidden", message)
        self.assertIn("<redacted>", message)

    def test_send_bitrix_message_posts_to_configured_dialog(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = b'{"result": 42}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        webhook_url = "https://raketabitrix.ru/rest/47/very-secret"
        with mock.patch.object(bitrix_alerts.request, "urlopen", return_value=response) as urlopen:
            bitrix_alerts.send_bitrix_message(webhook_url, "chat3820", "test message")

        http_request = urlopen.call_args.args[0]
        payload = json.loads(http_request.data.decode("utf-8"))
        self.assertEqual(payload, {"DIALOG_ID": "chat3820", "MESSAGE": "test message"})
        self.assertEqual(http_request.full_url, f"{webhook_url}/im.message.add.json")
        self.assertNotIn("very-secret", http_request.data.decode("utf-8"))

    def test_bitrix_handler_is_disabled_without_complete_configuration(self) -> None:
        self.assertIsNone(bitrix_alerts.bitrix_handler_from_env({}))
        self.assertIsNone(
            bitrix_alerts.bitrix_handler_from_env(
                {"CHECKSTOCK_BITRIX_WEBHOOK_URL": "https://raketabitrix.ru/rest/47/secret"}
            )
        )
        self.assertIsNotNone(
            bitrix_alerts.bitrix_handler_from_env(
                {
                    "CHECKSTOCK_BITRIX_WEBHOOK_URL": "https://raketabitrix.ru/rest/47/secret",
                    "CHECKSTOCK_BITRIX_DIALOG_ID": "chat3820",
                }
            )
        )

    def test_handler_suppresses_duplicate_errors_during_cooldown(self) -> None:
        sent: list[str] = []
        handler = bitrix_alerts.BitrixErrorHandler(
            "https://raketabitrix.ru/rest/47/secret",
            "chat3820",
            cooldown_seconds=300,
            sender=lambda _token, _chat_id, text: sent.append(text),
            clock=lambda: 100.0,
        )
        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "same failure", (), exc_info=None
        )

        handler.emit(record)
        handler.emit(record)
        handler._messages.join()

        self.assertEqual(len(sent), 1)

    def test_handler_deduplicates_same_exception_logged_by_multiple_loggers(self) -> None:
        sent: list[str] = []
        handler = bitrix_alerts.BitrixErrorHandler(
            "https://raketabitrix.ru/rest/47/secret",
            "chat3820",
            cooldown_seconds=300,
            sender=lambda _token, _chat_id, text: sent.append(text),
            clock=lambda: 100.0,
        )
        try:
            raise RuntimeError("database unavailable")
        except RuntimeError:
            exception_info = __import__("sys").exc_info()
        first = logging.LogRecord(
            "app.web", logging.ERROR, __file__, 1, "request failed", (), exception_info
        )
        second = logging.LogRecord(
            "uvicorn.error", logging.ERROR, __file__, 1, "ASGI error", (), exception_info
        )

        handler.emit(first)
        handler.emit(second)
        handler._messages.join()

        self.assertEqual(len(sent), 1)

    def test_watchdog_accepts_only_ready_application_and_database(self) -> None:
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"status":"ok","database":"ok"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with mock.patch.object(health_watchdog.request, "urlopen", return_value=response):
            ready, detail = health_watchdog.check_ready("http://app:8000/readyz", 3)

        self.assertTrue(ready)
        self.assertIn('"database": "ok"', detail)

        with mock.patch.object(
            health_watchdog.request,
            "urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            ready, detail = health_watchdog.check_ready("http://app:8000/readyz", 3)

        self.assertFalse(ready)
        self.assertIn("timed out", detail)

    def test_watchdog_uses_bitrix_when_configured(self) -> None:
        bitrix_sender = health_watchdog.notifier_from_env(
            {
                "CHECKSTOCK_BITRIX_WEBHOOK_URL": "https://raketabitrix.ru/rest/47/secret",
                "CHECKSTOCK_BITRIX_DIALOG_ID": "chat3820",
            }
        )
        self.assertIsNotNone(bitrix_sender)
        self.assertIsNone(health_watchdog.notifier_from_env({}))

        with mock.patch.object(health_watchdog, "send_bitrix_message") as send_bitrix:
            bitrix_sender("test")
        send_bitrix.assert_called_once()


if __name__ == "__main__":
    unittest.main()
