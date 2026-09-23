"""Вручную обновить анонимный сеанс WB в отдельном профиле Яндекс Браузера.

Обычные загрузки цен обновляют сеанс автоматически при необходимости.
Эта команда принудительно подготавливает его для диагностики. БД не изменяется.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.wb import storefront_browser, storefront_session
from scripts.diagnostics.check_wb_storefront import article_number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--article", type=article_number, default="153985484")
    parser.add_argument("--browser-path", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=90, choices=range(10, 181), metavar="10..180")
    args = parser.parse_args(argv)
    try:
        path = storefront_browser.prepare(
            args.article, executable_path=args.browser_path, wait_seconds=args.wait_seconds
        )
    except (RuntimeError, storefront_session.StorefrontSessionError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Browser/HTTP error dumps can contain private headers. Do not print them.
        print(f"Не удалось подготовить сеанс WB: {type(error).__name__}", file=sys.stderr)
        return 1
    print(f"Сеанс проверен и сохранён: {path}. Токен в консоль не выводится.")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
