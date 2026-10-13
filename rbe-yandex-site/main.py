"""Independent RBE dashboard for Yandex Market.

This module deliberately has no imports from the main CheckStock application.
It owns its database and only reads official Yandex data that has been imported
into that database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import base64
import hashlib
import hmac
import secrets
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr
from report_quality import SCHEMA as QUALITY_SCHEMA

ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("RBE_DATABASE_PATH", ROOT / "data" / "rbe_yandex.db"))
CONNECTIONS_PATH = Path(os.getenv("RBE_CONNECTIONS_PATH", ROOT / "secrets" / "yandex_connections.json"))
USERS_PATH = Path(os.getenv("RBE_USERS_PATH", ROOT / "secrets" / "users.json"))
SESSION_TTL_HOURS = int(os.getenv("RBE_SESSION_TTL_HOURS", "12"))
AUTO_SYNC_ENABLED = os.getenv("RBE_AUTO_SYNC", "true").lower() in {"1", "true", "yes"}
SYNC_INTERVAL_MINUTES = max(5, int(os.getenv("RBE_SYNC_INTERVAL_MINUTES", "30")))
SYNC_LOOKBACK_DAYS = max(1, int(os.getenv("RBE_SYNC_LOOKBACK_DAYS", "14")))
GOOGLE_SHEET_ID = os.getenv("RBE_COST_GOOGLE_SHEET_ID", "1q0WL6OB3Edh2O1ogqx7CK3MAij3O6xjD6gE0i3q3qEY")
COST_SYNC_HOUR_MSK = min(23, max(0, int(os.getenv("RBE_COST_SYNC_HOUR_MSK", "2"))))
MOSCOW = ZoneInfo("Europe/Moscow")
YANDEX_API = "https://api.partner.market.yandex.ru"
SYNC_STOP_EVENT = threading.Event()
SYNC_LOCK = threading.Lock()
COST_SYNC_LOCK = threading.Lock()
SHEET_STORE_SLUGS = {
    "rimili": "rimili",
    "tris": "tris",
    "rockkiddo": "rockkiddo",
    "trusthome": "trusthome",
    "sokoloff": "sokoloff",
    "gogol": "gogol",
    "toyka": "toyka",
}

# Every indicator is based on one of two official daily source sets.  Keeping
# this mapping here makes the completeness label follow the calculation, not a
# generic (and potentially misleading) page-level status.
METRIC_SOURCES = {
    "orders_turnover": "orders",
    "buyout_seller_turnover": "financial",
    "buyout_buyer_turnover": "financial",
    "buyout_count": "financial",
    "commission": "financial",
    "storage": "financial",
    "logistics": "financial",
    "other_direct_expenses": "financial",
    "advertising": "financial",
    "bank_receipt": "financial",
    "sold_cost_of_goods": "cost",
    "marginal_profit": "cost",
    "marginal_profit_percent": "cost",
}


def connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    with connection() as conn:
        conn.executescript(QUALITY_SCHEMA)
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
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS targeted_sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                metric TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS cost_products (
                article TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_costs (
                cost_date TEXT NOT NULL,
                article TEXT NOT NULL,
                cost REAL NOT NULL CHECK(cost >= 0),
                source TEXT NOT NULL CHECK(source IN ('google', 'excel', 'manual')),
                sheet_name TEXT,
                imported_at TEXT NOT NULL,
                updated_by TEXT,
                PRIMARY KEY (cost_date, article)
            );
            CREATE INDEX IF NOT EXISTS product_costs_article_date ON product_costs(article, cost_date);
            CREATE TABLE IF NOT EXISTS store_product_costs (
                cost_date TEXT NOT NULL,
                store_slug TEXT NOT NULL,
                article TEXT NOT NULL,
                cost REAL NOT NULL CHECK(cost >= 0),
                source TEXT NOT NULL,
                sheet_name TEXT,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (cost_date, store_slug, article)
            );
            CREATE INDEX IF NOT EXISTS store_product_costs_article_date ON store_product_costs(article, cost_date);
            CREATE TABLE IF NOT EXISTS cost_import_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                cost_date TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS cost_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                changed_at TEXT NOT NULL,
                cost_date TEXT NOT NULL,
                article TEXT NOT NULL,
                old_cost REAL,
                new_cost REAL NOT NULL,
                source TEXT NOT NULL,
                sheet_name TEXT,
                changed_by TEXT
            );
            """
        )


def _load_users() -> dict[str, dict]:
    if not USERS_PATH.is_file():
        return {}
    try:
        payload = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _password_matches(password: str, stored: str) -> bool:
    """Verify PBKDF2-SHA256 hashes encoded as iterations$salt$hash."""

    try:
        iterations, salt_text, expected_text = stored.split("$", 2)
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        calculated = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), decode(salt_text), int(iterations))
        return hmac.compare_digest(calculated, decode(expected_text))
    except (ValueError, TypeError):
        return False


def _session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _current_user(request: Request) -> dict | None:
    token = request.cookies.get("rbe_session")
    if not token:
        return None
    with connection() as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM sessions WHERE token_hash=?", (_session_token(token),)
        ).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]).astimezone(UTC) <= datetime.now(UTC):
        return None
    user = _load_users().get(str(row["username"]))
    if not user:
        return None
    return {"username": str(row["username"]), "role": str(user.get("role") or "viewer")}


def _require_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Требуется вход")
    return user


def _require_admin(request: Request) -> dict:
    user = _require_user(request)
    if user["role"] != "admin":
        raise HTTPException(403, "Недостаточно прав")
    return user


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


def _date_range(date_from: date, date_to: date) -> list[str]:
    return [(date_from + timedelta(days=offset)).isoformat() for offset in range((date_to - date_from).days + 1)]


def _report_stores(store_slug: str | None) -> list[str]:
    """Use configured cabinets, including ones that have no rows yet."""

    if store_slug:
        return [store_slug]
    configured = [str(item.get("store_slug")) for item in load_connections() if item.get("store_slug")]
    if configured:
        return sorted(set(configured))
    with connection() as conn:
        return [str(row["store_slug"]) for row in conn.execute("SELECT DISTINCT store_slug FROM daily_financial")]


def _format_missing_days(days: list[str]) -> str:
    if not days:
        return ""
    parsed = [date.fromisoformat(day) for day in days]
    chunks: list[str] = []
    start = previous = parsed[0]
    for current in parsed[1:]:
        if current == previous + timedelta(days=1):
            previous = current
            continue
        chunks.append(start.strftime("%d.%m.%Y") if start == previous else f"{start:%d.%m}–{previous:%d.%m.%Y}")
        start = previous = current
    chunks.append(start.strftime("%d.%m.%Y") if start == previous else f"{start:%d.%m}–{previous:%d.%m.%Y}")
    return ", ".join(chunks)


def report_completeness(date_from: date, date_to: date, store_slug: str | None) -> dict[str, dict]:
    """Return missing official-day facts separately for order and finance metrics."""

    stores = _report_stores(store_slug)
    expected = set(_date_range(date_from, date_to))
    if not stores:
        empty = {"complete": False, "detail": "Нет подключённых магазинов"}
        return {"orders": empty, "financial": empty, "cost": empty}

    placeholders = ",".join("?" for _ in stores)
    params: list[object] = [date_from.isoformat(), date_to.isoformat(), *stores]
    with connection() as conn:
        financial_rows = conn.execute(
            f"SELECT store_slug, business_id, report_date, values_json, calculated_at FROM daily_financial WHERE report_date>=? AND report_date<=? AND store_slug IN ({placeholders})",
            params,
        ).fetchall()
        order_rows = conn.execute(
            f"SELECT store_slug, report_date FROM daily_orders WHERE report_date>=? AND report_date<=? AND store_slug IN ({placeholders})",
            params,
        ).fetchall()
        quality_rows = conn.execute(
            f"SELECT * FROM daily_source_quality WHERE report_date>=? AND report_date<=? AND store_slug IN ({placeholders})",
            params,
        ).fetchall()

    def status(rows: list[sqlite3.Row]) -> dict:
        actual: defaultdict[str, set[str]] = defaultdict(set)
        for row in rows:
            actual[str(row["store_slug"])].add(str(row["report_date"]))
        missing = {store: sorted(expected - actual[store]) for store in stores if expected - actual[store]}
        if not missing:
            return {"complete": True, "detail": "Данные полные"}
        detail = "; ".join(f"{store.upper()}: {_format_missing_days(days)}" for store, days in missing.items())
        return {"complete": False, "detail": f"Неполные данные: {detail}", "missing": missing}

    quality = {(r['store_slug'], r['business_id'], r['report_date']): r for r in quality_rows}
    accounts: defaultdict[str, set[int]] = defaultdict(set)
    for item in load_connections():
        if item.get('store_slug') in stores and item.get('business_id'):
            accounts[item['store_slug']].add(int(item['business_id']))
    for row in financial_rows:
        accounts[row['store_slug']].add(row['business_id'])
    facts = {(r['store_slug'], r['business_id'], r['report_date']): r for r in financial_rows}
    financial_missing: defaultdict[str, list[str]] = defaultdict(list)
    cost_missing: defaultdict[str, list[str]] = defaultdict(list)
    reasons = []
    order_confirmed = []
    for store in stores:
        for day in sorted(expected):
            day_ready = bool(accounts[store])
            orders_ready = bool(accounts[store])
            costs_ready = bool(accounts[store])
            if not accounts[store]:
                reasons.append(f"{store.upper()}: нет подключённого кабинета или загруженных источников за {day}")
            for business in sorted(accounts[store]):
                key = (store, business, day)
                fact, proof = facts.get(key), quality.get(key)
                valid = fact is not None and proof is not None and proof['calculated_at'] == fact['calculated_at']
                ready = bool(valid and proof['financial_complete'])
                orders_ready = orders_ready and bool(valid and proof['orders_complete'])
                if not ready:
                    detail = proof['detail'] if valid else f"Источники данных за {day} не проверены или не загружены"
                    reasons.append(f"{store.upper()} ({business}): {detail}")
                day_ready = day_ready and ready
                costs_ready = costs_ready and fact is not None and json.loads(fact['values_json']).get('sold_cost_of_goods') is not None
            if not day_ready:
                financial_missing[store].append(day)
            if not costs_ready:
                cost_missing[store].append(day)
            if orders_ready:
                order_confirmed.append({'store_slug': store, 'report_date': day})
    financial_status = {'complete': not financial_missing, 'detail': 'Исходные отчёты проверены'}
    if financial_missing:
        financial_status.update(detail='Неполные данные. ' + '; '.join(dict.fromkeys(reasons)), missing=dict(financial_missing))
    missing_costs = dict(cost_missing)
    if missing_costs:
        cost_detail = "; ".join(
            f"{store.upper()}: {_format_missing_days(days)}" for store, days in missing_costs.items()
        )
        cost_status = {
            "complete": False,
            "detail": f"Нет закупочной цены для проданных товаров: {cost_detail}",
            "missing": missing_costs,
        }
    else:
        cost_status = {"complete": True, "detail": "Закупочные цены заполнены"}
    if not financial_status['complete']:
        missing = {store: sorted(set(financial_missing.get(store, []) + missing_costs.get(store, [])))
                   for store in set(financial_missing) | set(missing_costs)}
        cost_status = {'complete': False, 'missing': missing,
                       'detail': 'ЗЦ и прибыль предварительные: ' + financial_status['detail'] +
                                 ('; ' + cost_status['detail'] if missing_costs else '')}
    # Empty order days need independent report evidence, not a placeholder P&L row.
    order_coverage = [*order_rows, *order_confirmed]
    return {"orders": status(order_coverage), "financial": financial_status, "cost": cost_status}


def repair_source(metric: str, completeness: dict) -> str | None:
    source = METRIC_SOURCES.get(metric)
    if source == 'cost' and not completeness['financial']['complete']:
        return 'financial'
    return source


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


def _sync_period() -> tuple[date, date]:
    """Reload a rolling window so temporary 420 responses heal on a later run."""

    last_day = date.today() - timedelta(days=1)
    return last_day - timedelta(days=SYNC_LOOKBACK_DAYS - 1), last_day


def _run_official_sync(
    date_from: date | None = None,
    date_to: date | None = None,
    stores: tuple[str, ...] | None = None,
    lock_already_held: bool = False,
) -> dict[str, str]:
    """Run the official importer once and persist a non-sensitive audit record.

    The worker imports cabinets concurrently; an API 420 becomes a failed run
    and is retried by the loop, rather than being treated as a broken key.
    """

    if not lock_already_held and not SYNC_LOCK.acquire(blocking=False):
        return {"status": "busy", "detail": "Загрузка уже выполняется"}
    date_from, date_to = (date_from, date_to) if date_from and date_to else _sync_period()
    started_at = datetime.now(UTC).isoformat()
    try:
        with connection() as conn:
            cursor = conn.execute(
                "INSERT INTO sync_runs(started_at,status,detail) VALUES(?,?,?)",
                (started_at, "running", f"Период {date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}"),
            )
            run_id = int(cursor.lastrowid)
        command = [
            sys.executable,
            str(ROOT / "scripts" / "refresh_official_period.py"),
            "--date-from", date_from.isoformat(), "--date-to", date_to.isoformat(), "--workers", "6",
        ]
        if stores:
            command.extend(["--stores", *stores])
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=3600, check=False)
            detail = (result.stdout or result.stderr or "Импорт завершён без сообщения").strip()[-4000:]
            status = "completed" if result.returncode == 0 else "retrying"
        except (OSError, subprocess.TimeoutExpired) as error:
            status = "retrying"
            detail = f"Не удалось завершить импорт: {error}"[-4000:]
        with connection() as conn:
            conn.execute(
                "UPDATE sync_runs SET finished_at=?,status=?,detail=? WHERE id=?",
                (datetime.now(UTC).isoformat(), status, detail, run_id),
            )
        return {"status": status, "detail": detail}
    finally:
        if not lock_already_held:
            SYNC_LOCK.release()


def _missing_ranges(missing: dict[str, list[str]]) -> list[tuple[str, date, date]]:
    """Compact absent days into store-specific date ranges for a repair run."""

    result: list[tuple[str, date, date]] = []
    for store, values in missing.items():
        days = sorted(date.fromisoformat(value) for value in values)
        if not days:
            continue
        start = previous = days[0]
        for current in days[1:]:
            if current == previous + timedelta(days=1):
                previous = current
                continue
            result.append((store, start, previous))
            start = previous = current
        result.append((store, start, previous))
    return result


def _run_missing_sync(missing: dict[str, list[str]]) -> list[dict[str, str]]:
    """Repair only absent store/date facts; complete report periods stay untouched."""

    if not SYNC_LOCK.acquire(blocking=False):
        return [{"status": "busy", "detail": "Загрузка уже выполняется"}]
    try:
        return [
            _run_official_sync(date_from, date_to, stores=(store,), lock_already_held=True)
            for store, date_from, date_to in _missing_ranges(missing)
        ]
    finally:
        SYNC_LOCK.release()


def _run_targeted_sync(
    run_id: int, metric: str, source: str, date_from: date, date_to: date,
    store_slug: str | None, missing: dict[str, list[str]],
) -> None:
    """Repair an indicator and retain a user-facing result, retrying API 420."""

    for attempt in range(1, 4):
        with connection() as conn:
            conn.execute("UPDATE targeted_sync_runs SET status='running',attempts=?,detail=? WHERE id=?", (attempt, "Загружаем отсутствующие данные…", run_id))
        results = _run_missing_sync(missing)
        details = "\n".join(item["detail"] for item in results)
        statuses = {item["status"] for item in results}
        if statuses == {"completed"}:
            remaining = report_completeness(date_from, date_to, store_slug)[source].get("missing", {})
            if not remaining:
                with connection() as conn:
                    conn.execute("UPDATE targeted_sync_runs SET status='completed',finished_at=?,detail=? WHERE id=?", (datetime.now(UTC).isoformat(), "Данные получены от Яндекс Маркета", run_id))
                return
            with connection() as conn:
                conn.execute("UPDATE targeted_sync_runs SET status='no_data',finished_at=?,detail=? WHERE id=?", (datetime.now(UTC).isoformat(), "Яндекс Маркет не вернул данные за выбранный период", run_id))
            return
        if "420" in details or "429" in details:
            if attempt < 3:
                with connection() as conn:
                    conn.execute("UPDATE targeted_sync_runs SET status='retrying',detail=? WHERE id=?", (f"Лимит API Яндекс Маркета (420/429). Повтор {attempt + 1} из 3 через 30 минут.", run_id))
                if SYNC_STOP_EVENT.wait(30 * 60):
                    return
                continue
            message = "Лимит API Яндекс Маркета: три попытки не прошли. Повторите позже."
        else:
            if 'NO_DATA' in details:
                with connection() as conn:
                    conn.execute("UPDATE targeted_sync_runs SET status='no_data',finished_at=?,detail=? WHERE id=?", (datetime.now(UTC).isoformat(), "Яндекс пока не вернул финансовые данные за выбранный период. Расчёт остаётся неполным; повторите позже.", run_id))
                return
            message = "Ошибка загрузки: " + (details[-500:] or "Яндекс Маркет не вернул описание ошибки")
        with connection() as conn:
            conn.execute("UPDATE targeted_sync_runs SET status='failed',finished_at=?,detail=? WHERE id=?", (datetime.now(UTC).isoformat(), message, run_id))
        return


def _sync_loop() -> None:
    while not SYNC_STOP_EVENT.is_set():
        _run_official_sync()
        SYNC_STOP_EVENT.wait(SYNC_INTERVAL_MINUTES * 60)


def _normalise_header(value: object) -> str:
    """Compare Russian column names regardless of spaces and punctuation."""

    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _normalise_article(value: object) -> str:
    return str(value or "").strip()


def stores_for_sheet(sheet_name: str) -> list[str]:
    name = sheet_name.lower()
    return [slug for marker, slug in SHEET_STORE_SLUGS.items() if marker in name]


def _parse_cost(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), 2) if float(value) >= 0 else None
    text = str(value or "").strip().replace("₽", "").replace("руб.", "").replace("руб", "")
    text = text.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return round(parsed, 2) if parsed >= 0 else None


def _upsert_product(conn: sqlite3.Connection, article: str, now: str) -> None:
    conn.execute(
        """INSERT INTO cost_products(article,first_seen_at,last_seen_at) VALUES(?,?,?)
           ON CONFLICT(article) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
        (article, now, now),
    )


def _write_costs(
    cost_date: date,
    rows: list[dict[str, Any]],
    source: str,
    changed_by: str | None,
) -> dict[str, int]:
    """Persist a dated snapshot and retain a compact audit trail for every change."""

    now = datetime.now(UTC).isoformat()
    inserted = updated = unchanged = 0
    with connection() as conn:
        for row in rows:
            article = _normalise_article(row.get("article"))
            cost = _parse_cost(row.get("cost"))
            if not article or cost is None:
                continue
            _upsert_product(conn, article, now)
            previous = conn.execute(
                "SELECT cost,source FROM product_costs WHERE cost_date=? AND article=?",
                (cost_date.isoformat(), article),
            ).fetchone()
            # Explicitly supplied values have priority over a later automatic
            # refresh: manual > Excel > Google for the same article and day.
            if previous and (
                (previous["source"] == "manual" and source != "manual")
                or (previous["source"] == "excel" and source == "google")
            ):
                unchanged += 1
                continue
            if previous and float(previous["cost"]) == cost and previous["source"] == source:
                unchanged += 1
                continue
            conn.execute(
                """INSERT INTO product_costs(cost_date,article,cost,source,sheet_name,imported_at,updated_by)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(cost_date,article) DO UPDATE SET
                     cost=excluded.cost,source=excluded.source,sheet_name=excluded.sheet_name,
                     imported_at=excluded.imported_at,updated_by=excluded.updated_by""",
                (cost_date.isoformat(), article, cost, source, row.get("sheet_name"), now, changed_by),
            )
            conn.execute(
                """INSERT INTO cost_changes(changed_at,cost_date,article,old_cost,new_cost,source,sheet_name,changed_by)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (now, cost_date.isoformat(), article, previous["cost"] if previous else None, cost, source,
                 row.get("sheet_name"), changed_by),
            )
            if previous:
                updated += 1
            else:
                inserted += 1
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged}


def _extract_costs_from_workbook(workbook: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Read every WB tab and return valid rows plus explicit duplicate conflicts."""

    candidates: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_articles: set[str] = set()
    for sheet in workbook.worksheets:
        name = str(sheet.title or "")
        if "wb" not in name.lower():
            continue
        values = list(sheet.iter_rows(values_only=True))
        header_index = next(
            (index for index, row in enumerate(values)
             if "артикулвб" in {_normalise_header(cell) for cell in row}
             and "себесруб" in {_normalise_header(cell) for cell in row}),
            None,
        )
        if header_index is None:
            continue
        header = {_normalise_header(cell): index for index, cell in enumerate(values[header_index])}
        article_column, cost_column = header["артикулвб"], header["себесруб"]
        for row in values[header_index + 1:]:
            article = _normalise_article(row[article_column] if article_column < len(row) else "")
            if not article:
                continue
            seen_articles.add(article)
            cost = _parse_cost(row[cost_column] if cost_column < len(row) else "")
            if cost is not None:
                candidates[article].append({"article": article, "cost": cost, "sheet_name": name})
    valid: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for article, values in candidates.items():
        costs = {value["cost"] for value in values}
        if len(costs) == 1:
            item = values[0].copy()
            item["sheet_name"] = ", ".join(sorted({value["sheet_name"] for value in values}))
            valid.append(item)
        else:
            conflicts.append({"article": article, "values": values})
    return valid, conflicts, seen_articles


def _read_google_costs() -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Download a publicly shared Google Sheet as one workbook.

    Public XLSX export contains every tab, so no Google API key or service
    account is needed while the sheet has link access.
    """

    from io import BytesIO
    from openpyxl import load_workbook

    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/export?format=xlsx"
    response = httpx.get(url, follow_redirects=True, timeout=120)
    response.raise_for_status()
    return _extract_costs_from_workbook(load_workbook(BytesIO(response.content), read_only=True, data_only=True))


def _run_cost_google_sync(cost_date: date | None = None) -> bool:
    if not COST_SYNC_LOCK.acquire(blocking=False):
        return False
    cost_date = cost_date or datetime.now(MOSCOW).date()
    started_at = datetime.now(UTC).isoformat()
    try:
        with connection() as conn:
            run_id = int(conn.execute(
                "INSERT INTO cost_import_runs(started_at,cost_date,source,status) VALUES(?,?,?,?)",
                (started_at, cost_date.isoformat(), "google", "running"),
            ).lastrowid)
        try:
            rows, conflicts, seen_articles = _read_google_costs()
            now = datetime.now(UTC).isoformat()
            with connection() as conn:
                for article in seen_articles:
                    _upsert_product(conn, article, now)
            result = _write_costs(cost_date, rows, "google", None)
            status = "partial" if conflicts else "completed"
            detail = json.dumps({"scanned_articles": len(seen_articles), "conflicts": conflicts[:100], **result}, ensure_ascii=False)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as error:
            status, detail = "retrying", str(error)
        with connection() as conn:
            conn.execute(
                "UPDATE cost_import_runs SET finished_at=?,status=?,detail=? WHERE id=?",
                (datetime.now(UTC).isoformat(), status, detail[:8000], run_id),
            )
        return status in {"completed", "partial"}
    finally:
        COST_SYNC_LOCK.release()


def _cost_sync_loop() -> None:
    """Take one full Google snapshot each Moscow night, not on every restart."""

    while not SYNC_STOP_EVENT.is_set():
        now = datetime.now(MOSCOW)
        next_run = now.replace(hour=COST_SYNC_HOUR_MSK, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        if SYNC_STOP_EVENT.wait((next_run - now).total_seconds()):
            return
        # Network and Google API failures are transient often enough to merit
        # a few quiet retries during the same night.
        for attempt in range(4):
            if _run_cost_google_sync():
                break
            if attempt < 3 and SYNC_STOP_EVENT.wait(30 * 60):
                return


class ConnectionRequest(BaseModel):
    store_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    business_id: int = Field(gt=0)
    api_key: SecretStr = Field(min_length=1, max_length=16_384)


class SyncRequest(BaseModel):
    date_from: date
    date_to: date
    metric: str | None = None
    store_slug: str | None = None


class CostRequest(BaseModel):
    cost_date: date
    article: str = Field(min_length=1, max_length=255)
    cost: float = Field(ge=0, le=10_000_000)


app = FastAPI(title="RBE Яндекс Маркет")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def authenticate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/") or path in {"/login", "/health"}:
        return await call_next(request)
    user = _current_user(request)
    if user is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Требуется вход"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    request.state.user = user
    return await call_next(request)


@app.on_event("startup")
def on_startup() -> None:
    init_database()
    if AUTO_SYNC_ENABLED:
        threading.Thread(target=_sync_loop, name="rbe-official-sync", daemon=True).start()
    threading.Thread(target=_cost_sync_loop, name="rbe-cost-sync", daemon=True).start()


@app.get("/login")
def login_form() -> FileResponse:
    return FileResponse(ROOT / "templates" / "login.html")


@app.post("/login")
def login(username: str = Form(), password: str = Form()):
    user = _load_users().get(username.strip())
    if not user or not _password_matches(password, str(user.get("password_hash") or "")):
        return RedirectResponse("/login?error=1", status_code=303)
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    with connection() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (datetime.now(UTC).isoformat(),))
        conn.execute(
            "INSERT INTO sessions(token_hash,username,expires_at) VALUES(?,?,?)",
            (_session_token(token), username.strip(), expires_at),
        )
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "rbe_session", token, max_age=SESSION_TTL_HOURS * 3600, httponly=True, samesite="lax", secure=os.getenv("RBE_COOKIE_SECURE", "false").lower() == "true"
    )
    return response


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get("rbe_session")
    if token:
        with connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_session_token(token),))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("rbe_session")
    return response


@app.get("/")
def dashboard(request: Request) -> FileResponse:
    _require_user(request)
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/settings")
def settings(request: Request) -> FileResponse:
    _require_admin(request)
    return FileResponse(ROOT / "templates" / "settings.html")


@app.get("/costs")
def costs_page(request: Request) -> FileResponse:
    _require_user(request)
    return FileResponse(ROOT / "templates" / "costs.html")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/me")
def current_user(request: Request) -> dict:
    return {"ok": True, "user": _require_user(request)}


@app.get("/api/stores")
def stores(request: Request) -> dict:
    _require_user(request)
    with connection() as conn:
        rows = conn.execute("SELECT DISTINCT store_slug FROM daily_financial ORDER BY store_slug").fetchall()
    return {"ok": True, "stores": [row["store_slug"] for row in rows]}


@app.get("/api/report")
def report(request: Request, date_from: date, date_to: date, store_slug: str | None = None) -> dict:
    user = _require_user(request)
    if date_to < date_from or date_to - date_from > timedelta(days=366):
        raise HTTPException(422, "Выберите период от 1 до 367 дней")
    completeness = report_completeness(date_from, date_to, store_slug)
    return {
        "ok": True,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "values": sum_daily(date_from.isoformat(), date_to.isoformat(), store_slug),
        "quality": {
            metric: {**completeness[source], "can_repair": repair_source(metric, completeness) in {"orders", "financial"}}
            for metric, source in METRIC_SOURCES.items()
        },
        "can_sync": user["role"] == "admin",
        "sync_running": SYNC_LOCK.locked(),
    }


@app.get("/api/sync-status")
def sync_status(request: Request) -> dict:
    _require_user(request)
    with connection() as conn:
        row = conn.execute(
            "SELECT started_at,finished_at,status,detail FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {"ok": True, "automatic": AUTO_SYNC_ENABLED, "interval_minutes": SYNC_INTERVAL_MINUTES, "last_run": dict(row) if row else None}


@app.get("/api/targeted-sync/{run_id}")
def targeted_sync_status(request: Request, run_id: int) -> dict:
    _require_user(request)
    with connection() as conn:
        row = conn.execute(
            "SELECT id,metric,status,attempts,detail,started_at,finished_at FROM targeted_sync_runs WHERE id=?", (run_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Загрузка не найдена")
    return {"ok": True, "run": dict(row)}


@app.post("/api/sync", status_code=202)
def start_sync(request: Request, payload: SyncRequest) -> dict:
    _require_admin(request)
    if payload.date_to < payload.date_from or payload.date_to - payload.date_from > timedelta(days=366):
        raise HTTPException(422, "Выберите период от 1 до 367 дней")
    if SYNC_LOCK.locked():
        raise HTTPException(409, "Загрузка уже выполняется")
    if payload.metric:
        source = METRIC_SOURCES.get(payload.metric)
        if source is None:
            raise HTTPException(422, "Неизвестный показатель отчёта")
        completeness = report_completeness(payload.date_from, payload.date_to, payload.store_slug)
        source = repair_source(payload.metric, completeness)
        missing = completeness[source].get("missing", {})
        if not missing:
            return {"ok": True, "detail": "Для этого показателя пропусков уже нет"}
        if source == "cost":
            raise HTTPException(422, "Для ЗЦ сначала загрузите или исправьте себестоимость на вкладке «Себестоимость»")
        with connection() as conn:
            cursor = conn.execute(
                "INSERT INTO targeted_sync_runs(metric,started_at,status,detail) VALUES(?,?,?,?)",
                (payload.metric, datetime.now(UTC).isoformat(), "queued", "Ожидает запуска"),
            )
            run_id = int(cursor.lastrowid)
        threading.Thread(
            target=_run_targeted_sync,
            kwargs={
                "run_id": run_id, "metric": payload.metric, "source": source,
                "date_from": payload.date_from, "date_to": payload.date_to,
                "store_slug": payload.store_slug, "missing": missing,
            },
            name="rbe-missing-data-sync",
            daemon=True,
        ).start()
        return {"ok": True, "detail": "Загрузка только отсутствующих данных запущена", "missing": missing, "run_id": run_id}
    threading.Thread(
        target=_run_official_sync,
        kwargs={"date_from": payload.date_from, "date_to": payload.date_to},
        name="rbe-manual-sync",
        daemon=True,
    ).start()
    return {"ok": True, "detail": "Загрузка запущена"}


@app.get("/api/costs")
def costs(request: Request, cost_date: date, query: str = "") -> dict:
    _require_user(request)
    where = "cost_date=?"
    params: list[object] = [cost_date.isoformat()]
    if query.strip():
        where += " AND article LIKE ?"
        params.append(f"%{query.strip()}%")
    with connection() as conn:
        rows = conn.execute(
            f"""SELECT cost_date,article,cost,source,sheet_name,imported_at,updated_by
                 FROM product_costs WHERE {where} ORDER BY article""",
            params,
        ).fetchall()
        known = conn.execute("SELECT COUNT(*) AS count FROM cost_products").fetchone()["count"]
    return {"ok": True, "cost_date": cost_date.isoformat(), "known_articles": known, "rows": [dict(row) for row in rows]}


@app.get("/api/costs/status")
def costs_status(request: Request) -> dict:
    _require_user(request)
    with connection() as conn:
        latest = conn.execute(
            "SELECT started_at,finished_at,cost_date,source,status,detail FROM cost_import_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        product_count = conn.execute("SELECT COUNT(*) AS count FROM cost_products").fetchone()["count"]
    return {
        "ok": True,
        "google_sheet_id": GOOGLE_SHEET_ID,
        "nightly_hour_msk": COST_SYNC_HOUR_MSK,
        "public_sheet_access": True,
        "known_articles": product_count,
        "sync_running": COST_SYNC_LOCK.locked(),
        "last_run": dict(latest) if latest else None,
    }


@app.post("/api/costs")
def save_cost(request: Request, payload: CostRequest) -> dict:
    user = _require_admin(request)
    article = _normalise_article(payload.article)
    if not article:
        raise HTTPException(422, "Укажите АртикулВБ")
    result = _write_costs(
        payload.cost_date,
        [{"article": article, "cost": payload.cost, "sheet_name": None}],
        "manual",
        user["username"],
    )
    return {"ok": True, **result}


@app.post("/api/costs/import-excel")
async def import_costs_excel(request: Request, cost_date: date, file: UploadFile = File(...)) -> dict:
    user = _require_admin(request)
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(422, "Загрузите файл Excel формата .xlsx или .xlsm")
    try:
        from io import BytesIO
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(await file.read()), read_only=True, data_only=True)
    except Exception as error:
        raise HTTPException(422, "Не удалось прочитать Excel-файл") from error
    imported: list[dict[str, Any]] = []
    invalid_rows: list[str] = []
    for sheet in workbook.worksheets:
        values = list(sheet.iter_rows(values_only=True))
        header_index = next(
            (index for index, row in enumerate(values)
             if "артикулвб" in {_normalise_header(cell) for cell in row}
             and "себесруб" in {_normalise_header(cell) for cell in row}),
            None,
        )
        if header_index is None:
            continue
        header = {_normalise_header(cell): index for index, cell in enumerate(values[header_index])}
        for line, row in enumerate(values[header_index + 1:], start=header_index + 2):
            article = _normalise_article(row[header["артикулвб"]] if header["артикулвб"] < len(row) else "")
            if not article:
                continue
            cost = _parse_cost(row[header["себесруб"]] if header["себесруб"] < len(row) else "")
            if cost is None:
                invalid_rows.append(f"{sheet.title}!{line}")
                continue
            imported.append({"article": article, "cost": cost, "sheet_name": sheet.title})
    if not imported:
        raise HTTPException(422, "В файле не найдены колонки «АртикулВБ» и «Себес, руб» с корректными значениями")
    result = _write_costs(cost_date, imported, "excel", user["username"])
    return {"ok": True, "accepted": len(imported), "invalid_rows": invalid_rows[:100], **result}


@app.post("/api/costs/sync", status_code=202)
def sync_costs_now(request: Request) -> dict:
    _require_admin(request)
    if COST_SYNC_LOCK.locked():
        raise HTTPException(409, "Загрузка себестоимости уже выполняется")
    threading.Thread(target=_run_cost_google_sync, name="rbe-manual-cost-sync", daemon=True).start()
    return {"ok": True, "detail": "Загрузка себестоимости на сегодня запущена"}


@app.get("/api/connections")
def connections(request: Request) -> dict:
    _require_admin(request)
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
def add_connection(request: Request, payload: ConnectionRequest) -> dict:
    _require_admin(request)
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
def check_all_connections(request: Request) -> dict:
    """Check all keys concurrently and save only an operational status."""

    _require_admin(request)
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
def remove_connection(request: Request, store_slug: str, business_id: int) -> dict:
    _require_admin(request)
    before = load_connections()
    after = [item for item in before if not (item.get("store_slug") == store_slug and item.get("business_id") == business_id)]
    if len(after) == len(before):
        raise HTTPException(404, "Подключение не найдено")
    save_connections(after)
    return {"ok": True}
