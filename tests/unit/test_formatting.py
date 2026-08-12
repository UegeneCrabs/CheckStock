import unittest

from app.web.common import _fmt_num


class FormattingTests(unittest.TestCase):
    def test_integer_uses_non_breaking_group_separator(self) -> None:
        self.assertEqual(_fmt_num(1234567), "1\u00a0234\u00a0567")

    def test_missing_value_is_zero(self) -> None:
        self.assertEqual(_fmt_num(None), "0")


if __name__ == "__main__":
    unittest.main()
