"""Independent RBE dashboard for Yandex Market.

This module deliberately has no imports from the main CheckStock application.
It owns its database and only reads official Yandex data that has been imported
into that database.
"""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("RBE_DATABASE_PATH", ROOT / "data" / "rbe_yandex.db"))
CONNECTIONS_PATH = Path(os.getenv("RBE_CONNECTIONS_PATH", ROOT / "secrets" / "yandex_connections.json"))
YANDEX_API = "https://api.partner.market.yandex.ru"


def connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS daily_financial (
                store_slug TEXT NOT NULL,
                business_id INTEGER NOT NULL,
                report_date TEXT NOT NULL,
                values_json TEXT NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (store_slug, business_id, report_date)
            );
            CREATE TABLE IF NOT EXISTS daily_orders (
                store_slug TEXT NOT NULL,
                report_date TEXT NOT NULL,
                orders_amount REAL NOT NULL DEFAULT 0,
                cancellations_amount REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (store_slug, report_date)
            );
            """
        )


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def sum_daily(date_from: str, date_to: str, store_slug: str | None) -> dict:
    where = "report_date>=? AND report_date<=?"
    params: list[object] = [date_from, date_to]
    if store_slug:
        where += " AND store_slug=?"
        params.append(store_slug)
    with connection() as conn:
        financial = conn.execute(f"SELECT values_json FROM daily_financial WHERE {where}", params).fetchall()
        orders = conn.execute(
            f"SELECT SUM(orders_amount + cancellations_amount) AS total FROM daily_orders WHERE {where}", params
        ).fetchone()
    totals: defaultdict[str, float] = defaultdict(float)
    null_costs: set[str] = set()
    for row in financial:
        for key, value in json.loads(row["values_json"]).items():
            if value is None and key in {"cost_of_goods", "sold_cost_of_goods", "returned_cost_of_goods"}:
                null_costs.add(key)
            elif value is not None:
                totals[key] += number(value)
    result = dict(totals)
    for key in null_costs:
        result[key] = None
    result["orders_turnover"] = round(number(orders["total"] if orders else 0), 2) if orders else None
    if "bank_receipt" in result and result.get("sold_cost_of_goods") is not None:
        result["marginal_profit"] = round(result["bank_receipt"] - number(result["sold_cost_of_goods"]), 2)
        buyer = result.get("buyout_buyer_turnover")
        if buyer:
            result["marginal_profit_percent"] = round(result["marginal_profit"] / number(buyer) * 100, 2)
    return result


def load_connections() -> list[dict]:
    if not CONNECTIONS_PATH.exists():
        return []
    try:
        data = json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_connections(items: list[dict]) -> None:
    CONNECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONNECTIONS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONNECTIONS_PATH)


def check_connection(item: dict) -> dict:
    """Verify a key without creating a financial report."""

    try:
        response = httpx.get(
            f"{YANDEX_API}/v2/campaigns",
            params={"limit": 100},
            headers={"Api-Key": str(item.get("api_key") or ""), "Accept": "application/json"},
            timeout=30,
        )
        if response.status_code in (420, 429):
            return {"status": "limited", "detail": "Лимит API: повторите проверку позже"}
        response.raise_for_status()
        payload = response.json()
        campaigns = (payload.get("result") or payload).get("campaigns") or []
        has_business = any(
            int((row.get("business") or {}).get("id") or row.get("businessId") or 0)
            == int(item.get("business_id") or 0)
            for row in campaigns
        )
        return {
            "status": "connected" if has_business else "error",
            "detail": "Подключён" if has_business else "Нет доступа к указанному Business ID",
        }
    except httpx.HTTPStatusError as error:
        return {"status": "error", "detail": f"Яндекс Маркет вернул {error.response.status_code}"}
    except (httpx.HTTPError, ValueError):
        return {"status": "error", "detail": "Не удалось связаться с API Яндекс Маркета"}


class ConnectionRequest(BaseModel):
    store_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    business_id: int = Field(gt=0)
    api_key: SecretStr = Field(min_length=1, max_length=16_384)


app = FastAPI(title="RBE Яндекс Маркет")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_database()


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/settings")
def settings() -> FileResponse:
    return FileResponse(ROOT / "templates" / "settings.html")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/stores")
def stores() -> dict:
    with connection() as conn:
        rows = conn.execute("SELECT DISTINCT store_slug FROM daily_financial ORDER BY store_slug").fetchall()
    return {"ok": True, "stores": [row["store_slug"] for row in rows]}


@app.get("/api/report")
def report(date_from: date, date_to: date, store_slug: str | None = None) -> dict:
    if date_to < date_from or date_to - date_from > timedelta(days=366):
        raise HTTPException(422, "Выберите период от 1 до 367 дней")
    return {
        "ok": True,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "values": sum_daily(date_from.isoformat(), date_to.isoformat(), store_slug),
    }


@app.get("/api/connections")
def connections() -> dict:
    return {
        "ok": True,
        "connections": [
            {
                key: item[key]
                for key in ("store_slug", "business_id", "status", "detail")
                if key in item
            }
            for item in load_connections()
        ],
    }


@app.post("/api/connections")
def add_connection(payload: ConnectionRequest) -> dict:
    api_key = payload.api_key.get_secret_value().strip()
    try:
        response = httpx.get(
            f"{YANDEX_API}/v2/campaigns",
            params={"limit": 100},
            headers={"Api-Key": api_key, "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        campaigns = (response.json().get("result") or response.json()).get("campaigns") or []
    except httpx.HTTPStatusError as error:
        raise HTTPException(400, f"Яндекс Маркет вернул {error.response.status_code}; ключ не сохранён") from error
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(400, "Не удалось проверить ключ в API Яндекс Маркета") from error
    campaign_ids = sorted(
        int(row["id"])
        for row in campaigns
        if int((row.get("business") or {}).get("id") or row.get("businessId") or 0) == payload.business_id
    )
    if not campaign_ids:
        raise HTTPException(400, "У ключа нет доступа к указанному Business ID")
    entries = [
        item for item in load_connections()
        if not (item.get("store_slug") == payload.store_slug and item.get("business_id") == payload.business_id)
    ]
    entries.append({
        "store_slug": payload.store_slug,
        "business_id": payload.business_id,
        "campaign_ids": campaign_ids,
        "api_key": api_key,
        "status": "connected",
        "detail": "Подключён",
    })
    save_connections(entries)
    return {"ok": True, "store_slug": payload.store_slug, "business_id": payload.business_id, "campaign_ids": campaign_ids}


@app.post("/api/connections/check")
def check_all_connections() -> dict:
    """Check all keys concurrently and save only an operational status."""

    entries = load_connections()
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(entries)))) as executor:
        checks = list(executor.map(check_connection, entries))
    for item, check in zip(entries, checks, strict=True):
        item.update(check)
    save_connections(entries)
    return {
        "ok": True,
        "connections": [
            {key: item[key] for key in ("store_slug", "business_id", "status", "detail") if key in item}
            for item in entries
        ],
    }


@app.delete("/api/connections/{store_slug}/{business_id}")
def remove_connection(store_slug: str, business_id: int) -> dict:
    before = load_connections()
    after = [item for item in before if not (item.get("store_slug") == store_slug and item.get("business_id") == business_id)]
    if len(after) == len(before):
        raise HTTPException(404, "Подключение не найдено")
    save_connections(after)
    return {"ok": True}
