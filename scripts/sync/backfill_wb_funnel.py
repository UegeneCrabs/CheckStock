"""Resumable daily WB funnel refresh. Run separately from the web process.

API contract: https://dev.wildberries.ru/openapi/analytics
Uses /products with a one-day period (365-day history), offset pagination,
and a conservative 25-second interval per seller. No other marketplace APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.jobs import locks
from app.wb import funnel_orders, tokens

LOG = logging.getLogger("wb_funnel_backfill")
JOBS = (
    "wb_funnel_orders_sync",
    "wb_funnel_previous_day_close_00_msk",
    "wb_funnel_weekly_metrics_sync",
)
STOP = threading.Event()
INTERVAL = 25.0
PAGE_SIZE = 1000
REQUIRED_METRICS = (
    "orderCount",
    "orderSum",
    "cancelCount",
    "cancelSum",
    "buyoutCount",
    "buyoutSum",
)


class PermanentError(RuntimeError):
    """Needs operator attention; automatic retries cannot fix the input/access."""


def pause(seconds):
    if STOP.wait(max(0, seconds)):
        raise InterruptedError("Stopping; committed days remain in the checkpoint")


def days_between(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def retry_delay(headers, attempt, now=None):
    now = time.time() if now is None else now
    delay = min(900, 30 * 2 ** min(attempt, 5))
    for name in ("X-Ratelimit-Retry", "X-Ratelimit-Reset", "Retry-After"):
        raw = headers.get(name)
        if not raw:
            continue
        try:
            value = float(raw)
            if name == "X-Ratelimit-Reset" and value > 10**9:
                value = value / 1000 if value > 10**12 else value
                value -= now
        except ValueError:
            try:
                value = parsedate_to_datetime(raw).timestamp() - now
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(value):
            delay = max(delay, value + 2)
    return delay


class Pace:
    """Different tokens belonging to the same seller share one gate."""

    def __init__(self):
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            pause(self.next_at - time.monotonic())
            self.next_at = time.monotonic() + INTERVAL

    def defer(self, seconds):
        with self.lock:
            self.next_at = max(self.next_at, time.monotonic() + seconds)


class Client:
    def __init__(self, token, pace, store):
        self.token, self.pace, self.store = token, pace, store

    def request(self, payload):
        for attempt in range(1, 13):
            self.pace.wait()
            request = urllib.request.Request(
                funnel_orders.ENDPOINT,
                data=json.dumps(payload).encode(),
                headers={"Authorization": self.token, "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                status, headers = error.code, error.headers
                error.close()
                if status != 429 and status < 500:
                    raise PermanentError(f"WB HTTP {status}; check access or request parameters") from None
                delay = retry_delay(headers, attempt)
                reason = f"HTTP {status}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
                delay, reason = retry_delay({}, attempt), "network/invalid JSON"
            # Never log response bodies, request headers, tokens or DB parameters.
            LOG.warning("retry store=%s attempt=%s reason=%s wait=%.0fs", self.store, attempt, reason, delay)
            self.pace.defer(delay)
        raise RuntimeError("Temporary WB failure after 12 attempts; rerun to resume")


def fetch_day(client, day):
    """Do not expose partial pages to the database writer."""
    products = {}
    for page in range(100):
        payload = funnel_orders._period_payload(day, day)
        payload.update(skipDeletedNm=False, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
        response = client.request(payload)
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("products"), list):
            raise PermanentError("WB returned an invalid products envelope; old day preserved")
        rows = data["products"]
        if len(rows) > PAGE_SIZE:
            raise PermanentError("WB returned more rows than the requested page size")
        for raw in rows:
            if not isinstance(raw, dict):
                raise PermanentError("WB returned an invalid product")
            statistic = raw.get("statistic")
            selected = statistic.get("selected") if isinstance(statistic, dict) else None
            if not isinstance(selected, dict):
                raise PermanentError("WB omitted selected-period metrics; old day preserved")
            for field in REQUIRED_METRICS:
                value = selected.get(field)
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or value < 0
                    or (field.endswith("Count") and int(value) != value)
                ):
                    raise PermanentError(f"WB returned invalid {field}; old day preserved")
            values = funnel_orders._product_values(raw)
            if values is None or not values[0].isdigit():
                raise PermanentError("WB omitted a numeric nmId; old day preserved")
            if values[0] in products:
                raise PermanentError("WB repeated a product across pages; old day preserved")
            products[values[0]] = values
        if len(rows) < PAGE_SIZE:
            return list(products.values())
    raise PermanentError("WB exceeded 100 pages; old day preserved")


class Checkpoint:
    def __init__(self, path, start, end, stores):
        self.path, self.lock = path, threading.Lock()
        identity = {"version": 1, "from": str(start), "to": str(end), "stores": sorted(stores)}
        self.data = {"identity": identity, "status": "pending", "completed": {}, "errors": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            if self.data.get("identity") != identity:
                raise PermanentError("Checkpoint belongs to another period or set of stores")

    def saved(self, store, day):
        with self.lock:
            return str(day) in self.data["completed"].get(store, {})

    def update(self, *, store=None, day=None, records=None, error=None, status=None):
        with self.lock:
            if day is not None:
                self.data["completed"].setdefault(store, {})[str(day)] = records
                self.data["errors"].pop(store, None)
            if error is not None:
                self.data["errors"][store] = error
            if status:
                self.data["status"] = status
            self.data["updated_at"] = datetime.now(UTC).isoformat()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(self.data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)


@contextmanager
def exclusive_funnel():
    """Existing scheduled/manual funnel jobs use these cross-process DB locks."""
    with locks.hold("wb_funnel_daily_backfill"):
        while True:
            stack = ExitStack()
            try:
                for name in JOBS:
                    stack.enter_context(locks.hold(name))
            except locks.SyncJobBusyError:
                stack.close()
                LOG.info("Waiting for the current scheduled/manual funnel job")
                pause(30)
                continue
            break
        with stack:
            # Also allow the last scheduled request's rolling minute to expire.
            pause(65)
            yield


def load_store(store, client, days, checkpoint):
    for day in days:
        if checkpoint.saved(store, day):
            continue
        products = fetch_day(client, day)
        if STOP.is_set():
            raise InterruptedError("Stopping before database write")
        # Existing atomic replacement also stores coverage and refreshes dated WB metrics.
        funnel_orders._replace_day(store, day, products)
        checkpoint.update(store=store, day=day, records=len(products))
        LOG.info("saved store=%s day=%s rows=%d", store, day, len(products))


def verify(checkpoint):
    identity = checkpoint.data["identity"]
    expected_days = {
        str(day)
        for day in days_between(date.fromisoformat(identity["from"]), date.fromisoformat(identity["to"]))
    }
    with db.get_connection() as conn:
        for store in identity["stores"]:
            completed = checkpoint.data["completed"].get(store, {})
            if set(completed) != expected_days:
                raise RuntimeError(f"Incomplete checkpoint for {store}")
            rows = conn.execute(
                "SELECT day,COUNT(*) AS rows,MIN(source_version) AS version FROM wb_funnel_daily_orders "
                "WHERE store_slug=? AND day>=? AND day<=? GROUP BY day",
                (store, identity["from"], identity["to"]),
            ).fetchall()
            actual = {row["day"]: row for row in rows}
            coverage = {
                row["day"]
                for row in conn.execute(
                    "SELECT day FROM economics_source_days WHERE marketplace='WB' AND source='orders' "
                    "AND store_slug=? AND day>=? AND day<=?",
                    (store, identity["from"], identity["to"]),
                )
            }
            for day, count in completed.items():
                row = actual.get(day)
                if (
                    day not in coverage
                    or (row["rows"] if row else 0) != count
                    or (row and row["version"] < 4)
                ):
                    raise RuntimeError(f"Database verification failed for {store} {day}")
    LOG.info("Verified all %d store-days in the database", len(expected_days) * len(identity["stores"]))


def run(args):
    today = datetime.now(MOSCOW_TIMEZONE).date()
    if not today - timedelta(days=364) <= args.date_from <= args.date_to < today:
        raise PermanentError("Use completed Moscow days within the last 365 days")
    stores = sorted(set(args.store or STORES))
    missing = [store for store in stores if not tokens.has_token(store)]
    if missing:
        raise PermanentError("Missing WB tokens: " + ", ".join(missing))
    days = days_between(args.date_from, args.date_to)
    with exclusive_funnel():
        checkpoint = Checkpoint(args.state_file, args.date_from, args.date_to, stores)
        checkpoint.update(status="running")
        clients, paces = {}, {}
        for store in stores:
            token = tokens.get_token(store)
            claims = tokens.decode_token_claims(token)
            seller = str(claims.get("sid") or hashlib.sha256(token.encode()).hexdigest())
            clients[store] = Client(token, paces.setdefault(seller, Pace()), store)
        LOG.info(
            "Started WB-only backfill: %s..%s, %d stores, %d days",
            args.date_from,
            args.date_to,
            len(stores),
            len(days),
        )
        exit_code = 0
        with ThreadPoolExecutor(max_workers=len(stores)) as executor:
            futures = {
                executor.submit(load_store, store, clients[store], days, checkpoint): store
                for store in stores
            }
            for future in as_completed(futures):
                store = futures[future]
                try:
                    future.result()
                except Exception as error:
                    message = (
                        str(error)
                        if isinstance(error, (PermanentError, InterruptedError))
                        else type(error).__name__
                    )
                    checkpoint.update(store=store, error=message)
                    LOG.error("failed store=%s reason=%s", store, message)
                    exit_code = max(exit_code, 2 if isinstance(error, PermanentError) else 1)
        if not exit_code:
            verify(checkpoint)
        checkpoint.update(status="complete" if not exit_code else "incomplete")
        return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--date-to", type=date.fromisoformat, required=True, help="Inclusive, completed Moscow day"
    )
    parser.add_argument("--store", action="append", choices=sorted(STORES))
    parser.add_argument(
        "--state-file", type=Path, required=True, help="Persistent JSON checkpoint; reuse on restart"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        return run(args)
    except PermanentError as error:
        LOG.error("Stopped: %s", error)
        return 2
    except Exception as error:
        LOG.error("Stopped: %s; rerun with the same checkpoint to resume", type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
