"""Local UI fixture using production templates/scripts and synthetic F05 data.

Run: .venv/Scripts/python.exe tests/preview_economics_completeness.py
No application startup, source syncs, real database or secrets are used.
"""

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_economics_completeness import DAY, WB, YM, CompletenessTests  # noqa: E402

CompletenessTests.setUpClass()

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.dto.identity import User  # noqa: E402
from app.web import templating  # noqa: E402


def main():
    case = CompletenessTests()
    case.setUp()
    user = User(id=1, login="f05", full_name="F05", role="superadmin", created_at="2026-09-01T00:00:00Z")
    application = FastAPI()

    def report(market):
        rows = []
        for i, label in enumerate(("Не загружена реклама", "Полные данные · реклама 0 ₽", "Не задана цена продажи")):
            fixture = dict(WB if market == "WB" else YM)
            if i == 2:
                fixture["retail_price" if market == "WB" else "seller_price"] = None
            if market == "WB":
                period = case.period({DAY: case.wb_snapshot(fixture)}, {DAY: {"orders_count": 10}}, {DAY: 0}, {DAY}, {DAY} if i != 0 else set())
                daily = case.routes._report_daily_calculations(
                    date_from=date.fromisoformat(DAY), date_to=date.fromisoformat(DAY),
                    daily_orders={DAY: {"orders_count": 10}}, margin_snapshots={DAY: case.wb_snapshot(fixture)},
                    live_day=date(2026, 9, 25), live_snapshot=None, daily_advertising={DAY: 0},
                    orders_days={DAY}, advertising_days={DAY} if i != 0 else set())
            else:
                saved = case.ym_days.resolve_day(DAY, {"inputs": fixture}, {"orders_count": 10}, {"spend": 0}, True, i != 0)
                period = case.ym_calc.aggregate([saved], [DAY])
                daily = [case.ym_reports.daily_row(DAY, saved, {"orders_count": 10}, {"spend": 0}, True, i != 0, None)]
            rows.append({**period, "margin_complete": period["complete"], "margin_missing_days": period["missing_days"],
                         "name": label, "article": "F05-" + str(i), "store_slug": "rimili", "store_name": "RIMILI",
                         "subject": "Синтетические данные", "orders_count": 10, "orders_amount": 9000,
                         "advertising_spend": None if i == 0 else 0, "daily_calculations": daily})
        return {"ok": True, "rows": rows, "totals": case.reporting._unit_profit_report_totals(rows),
                "filters": {"subjects": [], "managers": [], "articles": []}, "pagination": {"enabled": False},
                "period_from": DAY, "period_to": DAY, "daily_details": True}

    reports = {market: report(market) for market in ("WB", "YM")}

    @application.middleware("http")
    async def actor(request, call_next):
        request.state.user = user
        return await call_next(request)

    @application.get("/report/{market}", response_class=HTMLResponse)
    def page(market: str):
        config = {"marketplace": market, "dataEndpoint": "/data/" + market,
                  "filtersEndpoint": "/filters", "stores": [{"slug": "rimili", "name": "RIMILI"}],
                  "defaultDateFrom": DAY, "defaultDateTo": DAY}
        content = templating.fill_template("economics/wb/unit-profit.html", report_marketplace=market,
                                          unit_1c_report_config=json.dumps(config), unit_1c_manager_filter="")
        with patch.object(templating, "render_system_alerts", return_value=""):
            return templating.render_page("F05 · Синтетические данные", "unit_1c_reports", content, user)

    @application.get("/data/{market}")
    def data(market: str):
        return reports[market]

    @application.get("/filters")
    def filters():
        return {"ok": True, "filters": {"subjects": [], "managers": [], "articles": []}}

    @application.get("/dashboard", response_class=HTMLResponse)
    def dashboard():
        config = {"productsEndpoint": "/products", "stores": [{"slug": "rimili", "name": "RIMILI"}],
                  "periodFrom": DAY, "periodTo": DAY, "lastCompleteDay": DAY, "periodDays": 1, "products": []}
        content = templating.fill_template("economics/shared/dashboard.html", unit_1c_config=json.dumps(config),
                  marketplace_name="Wildberries · Синтетические данные", marketplace_label="WB",
                  loading_description="", unit_1c_notice="")
        with patch.object(templating, "render_system_alerts", return_value=""):
            return templating.render_page("F05 · Синтетические данные", "unit_1c", content, user)

    @application.get("/products")
    def products():
        products = []
        for row in reports["WB"]["rows"]:
            p = case.routes._unit_economics_1c_mock_product("rimili", {"article": row["article"], "name": row["name"], "fbs_stock": 10}, history_days=0)
            p["economics_7d"].update(margin=row["margin"], roi=row["roi"], purchase_value=row["purchase_value"],
                 roi_purchase_value=row["roi_purchase_value"], margin_coverage=row["coverage"], roi_coverage=row["coverage"],
                 complete=row["complete"], messages=row["messages"])
            p["current_economics"].update(margin=row["daily_calculations"][0]["net_profit"], roi=row["roi"],
                 complete=row["complete"], orders=10, purchase_value=200, messages=row["messages"],
                 buyout_percent=80, expected_buyouts=8, day_profit=row["margin"],
                 day_purchase_value=1600, daily_complete=row["complete"], daily_messages=row["messages"],
                 advertising_spend=row["advertising_spend"],
                 issues={"margin": row["messages"], "roi": row["messages"]})
            p["data_errors"] = row["messages"]
            products.append(p)
        return {"ok": True, "products": products, "warnings": [], "period_from": DAY, "period_to": DAY, "period_days": 1}

    application.mount("/static", StaticFiles(directory=Path(__file__).resolve().parents[1] / "static"))
    try:
        uvicorn.run(application, host="127.0.0.1", port=8770, log_level="warning")
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
