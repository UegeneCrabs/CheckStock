import json
import logging
import unittest
from unittest import mock

from app import telegram_alerts
from scripts import health_watchdog


class TelegramAlertTests(unittest.TestCase):
    def test_send_message_uses_bot_api_without_leaking_token_to_payload(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = b'{"ok": true, "result": {}}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with mock.patch.object(telegram_alerts.request, "urlopen", return_value=response) as urlopen:
            telegram_alerts.send_telegram_message("123:secret", "654043449", "test message")

        http_request = urlopen.call_args.args[0]
        payload = json.loads(http_request.data.decode("utf-8"))
        self.assertEqual(payload, {"chat_id": "654043449", "text": "test message"})
        self.assertIn("/bot123:secret/sendMessage", http_request.full_url)
        self.assertNotIn("secret", http_request.data.decode("utf-8"))

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

        message = telegram_alerts.format_error_record(record)

        self.assertIn("request-123", message)
        self.assertIn("RuntimeError", message)
        self.assertNotIn("very-secret", message)
        self.assertNotIn("hidden", message)
        self.assertIn("<redacted>", message)

    def test_handler_suppresses_duplicate_errors_during_cooldown(self) -> None:
        sent: list[str] = []
        handler = telegram_alerts.TelegramErrorHandler(
            "123:secret",
            "654043449",
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
        handler = telegram_alerts.TelegramErrorHandler(
            "123:secret",
            "654043449",
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

    def test_handler_is_disabled_without_complete_configuration(self) -> None:
        self.assertIsNone(telegram_alerts.telegram_handler_from_env({}))
        self.assertIsNone(
            telegram_alerts.telegram_handler_from_env(
                {"CHECKSTOCK_TELEGRAM_BOT_TOKEN": "123:secret"}
            )
        )

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


if __name__ == "__main__":
    unittest.main()
