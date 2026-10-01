"""Manual UI fixture: python tests/preview_economics_calendar.py (localhost only).

Uses disposable data and no application lifespan, secrets, jobs or marketplace APIs.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.responses import HTMLResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from test_economics_calendar import CalendarTests, api, inputs  # noqa: E402

from app.web import templating  # noqa: E402


def main():
    case = CalendarTests()
    case.setUp()
    try:
        for i in range(65):
            article = f"DEMO-{i + 1:03}"
            names = [
                "Конструктор с магнитными деталями, 36 элементов",
                "Набор для творчества «Мастерская»",
                "Мягкая игрушка Мишка, 28 см",
            ]
            case.catalog.append(
                {
                    "article": article,
                    "name": names[i % 3],
                    "barcode": str(4600000000000 + i),
                    "image_url": "http://127.0.0.1:8769/demo-product.svg",
                }
            )
            for market in ("WB", "YANDEX MARKET"):
                for day in (case.day, case.today.isoformat()):
                    values = inputs(market)
                    if i % 3 == 1:
                        values["purchase_price"] = None
                    if i % 3 == 2:
                        values["fulfillment_cost"] = None
                    if i % 5 == 0:
                        values["advertising_spend"] = None
                    case.capture((market, "rimili", article, day), values)
        application = case.client.app
        application.mount(
            "/static", StaticFiles(directory=Path(__file__).resolve().parents[1] / "static"), name="static"
        )

        @application.get("/sales/unit-economics-1c", response_class=HTMLResponse)
        @application.get("/sales/unit-economics-1c/yandex-market", response_class=HTMLResponse)
        def page(request: Request):
            with patch.object(templating, "render_system_alerts", return_value=""):
                return api.page(request)

        @application.get("/demo-product.svg")
        def demo_photo():
            return Response(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 124"><rect width="96" height="124" fill="#f4ead9"/><rect x="20" y="20" width="56" height="88" rx="6" fill="#b7895b"/><rect x="25" y="25" width="46" height="50" fill="#f8f1e5"/><circle cx="48" cy="49" r="17" fill="#cc7463"/><path d="M35 51h26M48 34v31" stroke="#f8e2a6" stroke-width="5"/><text x="48" y="93" text-anchor="middle" fill="white" font-size="9">DEMO</text></svg>',
                media_type="image/svg+xml",
            )

        uvicorn.run(application, host="127.0.0.1", port=8769, log_level="warning")
    finally:
        case.tearDown()


if __name__ == "__main__":
    main()
