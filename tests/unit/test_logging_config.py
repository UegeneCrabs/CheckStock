import logging
import unittest

from app.logging_config import CompactFormatter, RequestContextFilter, request_id_context


class CompactLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.formatter = CompactFormatter(
            "%(levelname)s %(short_name)s%(request_context)s | %(message)s"
        )
        self.context_filter = RequestContextFilter()

    def _render(self) -> str:
        record = logging.LogRecord("app.wb.api", logging.WARNING, __file__, 1, "retry", (), None)
        self.context_filter.filter(record)
        return self.formatter.format(record)

    def test_background_log_omits_empty_request_id_and_app_prefix(self) -> None:
        self.assertEqual(self._render(), "WARNING wb.api | retry")

    def test_request_log_uses_short_request_id(self) -> None:
        token = request_id_context.set("1234567890abcdef")
        try:
            rendered = self._render()
        finally:
            request_id_context.reset(token)

        self.assertEqual(rendered, "WARNING wb.api request=12345678 | retry")


if __name__ == "__main__":
    unittest.main()
