"""Versioned daily observations and manual overlays, committed as one transaction.

The first source observation is immutable. Every refresh preserves its received
payload in the event log; manual overlays survive refreshes. Reads never capture.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from app.economics.daily_calculation import calculate, validate_change
from app.repositories.core import WRITE_LOCK, get_connection


class Conflict(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def now():
    return datetime.now(UTC).isoformat()


def _decode(row):
    saved = json.loads(row["payload_json"])
    # Read only the recorded inputs with their formula version; current settings
    # and remote sources are never consulted by a read/recalculation.
    payload = resolved(row["marketplace"], saved["source"], saved.get("overrides"), saved["original"])
    return {**payload, "revision": row["revision"], "token": str(row["revision"])}


def _select(conn, key):
    return conn.execute(
        "SELECT * FROM economics_daily WHERE marketplace=? AND store_slug=? AND article=? AND day=?",
        key,
    ).fetchone()


def observation(values, *, origins=None, raw=None, captured_at=None, version=None, basis="observed"):
    return {
        "values": values,
        "origins": origins or {},
        "raw": raw or {},
        "captured_at": captured_at or now(),
        "version": version,
        "basis": basis,
    }


def resolved(marketplace, source, overrides=None, original=None):
    overrides = overrides or {}
    values, result = calculate(marketplace, {**source["values"], **overrides}, source.get("version"))
    return {
        "original": original or source,
        "source": source,
        "overrides": overrides,
        "values": values,
        "result": result,
        "version": source.get("version"),
    }


def from_wb(row):
    values = json.loads(row["inputs_json"])
    values.setdefault("orders_count", values.get("advertising_orders_count"))
    source = observation(
        values,
        raw=dict(row),
        captured_at=row["captured_at"],
        version=row["calculation_version"],
        basis="legacy",
    )
    payload = resolved("WB", source)
    return {**payload, "revision": 0, "token": "legacy:" + digest(dict(row))}


def from_yandex(row, *, day_input=False):
    data = json.loads(row["payload_json"])
    values = dict(data.get("values") if day_input else data.get("inputs") or {})
    if not day_input:
        values.update(orders_count=data.get("orders_count"), advertising_spend=data.get("advertising_spend"))
    source = observation(
        values,
        origins=data.get("origins"),
        raw=data,
        captured_at=row["updated_at"] if day_input else row["captured_at"],
        version=data.get("version") if day_input else data.get("calculation_version"),
        basis="legacy",
    )
    return {**resolved("YANDEX MARKET", source), "revision": 0, "token": "legacy:" + digest(dict(row))}


def _legacy(conn, key):
    market, store, article, day = key
    if market == "WB":
        row = conn.execute(
            "SELECT * FROM unit_economics_1c_daily_margin_snapshots WHERE store_slug=? AND article=? AND day=?",
            (store, article, day),
        ).fetchone()
        return from_wb(row) if row else None
    row = conn.execute(
        "SELECT * FROM yandex_economics_daily WHERE store_slug=? AND article=? AND day=? AND scheme='COMMON'",
        (store, article, day),
    ).fetchone()
    if row:
        return from_yandex(row)
    row = conn.execute(
        "SELECT * FROM yandex_economics_sources WHERE store_slug=? AND article=? AND source=?",
        (store, article, "day-input:" + day + ":COMMON"),
    ).fetchone()
    return from_yandex(row, day_input=True) if row else None


def get(key, *, events=False):
    with get_connection() as conn:
        row = _select(conn, key)
        data = _decode(row) if row else _legacy(conn, key)
        if data and events:
            rows = conn.execute(
                "SELECT * FROM economics_daily_events WHERE marketplace=? AND store_slug=? AND article=? AND day=? ORDER BY revision",
                key,
            ).fetchall()
            data["events"] = []
            for row in rows:
                event = json.loads(row["payload_json"])
                # Full source responses remain in the journal. Avoid transferring
                # dozens of copies when opening a single value's history.
                event.pop("original", None)
                if "source" in event:
                    event["source"] = {k: v for k, v in event["source"].items() if k != "raw"}
                data["events"].append(
                    {"at": row["created_at"], "actor": row["actor"], "revision": row["revision"], **event}
                )
        if data:
            data["source_times"] = source_times(key[0], data["source"])
    return data


def source_times(marketplace, source):
    """Expose actual recorded acquisition times, separately from snapshot time."""
    values, raw = source["values"], source.get("raw") or {}
    if marketplace == "WB":
        result = {
            k: values.get("source_synced_at")
            for k in ("purchase_price", "fulfillment_cost", "team_commission_percent", "turnover_days")
        }
        result.update({k: values.get("price_updated_at") for k in ("retail_price", "customer_price")})
        result.update(
            {
                k: values.get("product_settings_updated_at")
                for k in ("delivery_wb_rub", "return_cost_rub", "volume_l", "storage_wb_rub")
            }
        )
        result.update(
            {
                k: values.get("cabinet_settings_updated_at")
                for k in (
                    "acceptance_coefficient",
                    "wb_extra_tariff_percent",
                    "acquiring_percent",
                    "vat_percent",
                    "usn_percent",
                    "osno_percent",
                    "tax_system",
                )
            }
        )
        reference = raw.get("reference")
        if isinstance(reference, dict) and reference.get("team_commission_percent") is None:
            result["team_commission_percent"] = values.get("cabinet_settings_updated_at")
        if values.get("buyout_default_applied"):
            result["buyout_percent"] = values.get("cabinet_settings_updated_at")
    else:
        prices = raw.get("prices") or {}
        result = {
            "seller_price": prices.get("seller_checked_at"),
            "buyer_price": prices.get("checked_at") or prices.get("spp_checked_at"),
        }
        result.update(
            {k: (raw.get("source_1c") or {}).get("synced_at") for k in ("purchase_price", "fulfillment_cost")}
        )
    metrics = raw.get("daily_metrics") or {}
    for field, name in (("orders_count", "orders"), ("advertising_spend", "advertising")):
        rows = metrics.get(name) or raw.get(name) or []
        stamps = [r.get("updated_at") or r.get("synced_at") for r in rows]
        stamps = [s for s in stamps if s]
        result[field] = max(stamps) if stamps else metrics.get("received_at")
    return result


def records(marketplace, stores, start, end):
    if not stores:
        return []
    marks = ",".join("?" for _ in stores)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM economics_daily WHERE marketplace=? AND store_slug IN ({marks}) AND day>=? AND day<=?",
            (marketplace, *stores, start, end),
        ).fetchall()
    return [
        {"store_slug": row["store_slug"], "article": row["article"], "day": row["day"], **_decode(row)}
        for row in rows
    ]


def day_records(marketplace, store, day):
    """Bulk reads, including real legacy records. No synthetic backfill on GET."""
    with get_connection() as conn:
        if marketplace == "WB":
            rows = conn.execute(
                "SELECT * FROM unit_economics_1c_daily_margin_snapshots WHERE store_slug=? AND day=?",
                (store, day),
            ).fetchall()
            result = {r["article"]: from_wb(r) for r in rows}
        else:
            rows = conn.execute(
                "SELECT * FROM yandex_economics_sources WHERE store_slug=? AND source=?",
                (store, "day-input:" + day + ":COMMON"),
            ).fetchall()
            result = {r["article"]: from_yandex(r, day_input=True) for r in rows}
            rows = conn.execute(
                "SELECT * FROM yandex_economics_daily WHERE store_slug=? AND day=? AND scheme='COMMON'",
                (store, day),
            ).fetchall()
            result.update({r["article"]: from_yandex(r) for r in rows})
    result.update({r["article"]: r for r in records(marketplace, (store,), day, day)})
    return result


def first_day(marketplace, store, articles):
    # Restrict the date range to permitted products too (no manager-scope leak).
    if not articles:
        return None
    with get_connection() as conn:
        rows = list(
            conn.execute(
                "SELECT article,MIN(day) AS day FROM economics_daily WHERE marketplace=? AND store_slug=? GROUP BY article",
                (marketplace, store),
            )
        )
        if marketplace == "WB":
            rows += list(
                conn.execute(
                    "SELECT article,MIN(day) AS day FROM unit_economics_1c_daily_margin_snapshots WHERE store_slug=? GROUP BY article",
                    (store,),
                )
            )
        else:
            rows += list(
                conn.execute(
                    "SELECT article,MIN(day) AS day FROM yandex_economics_daily WHERE store_slug=? AND scheme='COMMON' GROUP BY article",
                    (store,),
                )
            )
            rows += list(
                conn.execute(
                    "SELECT article,MIN(SUBSTR(source,11,10)) AS day FROM yandex_economics_sources WHERE store_slug=? AND source LIKE 'day-input:%:COMMON' GROUP BY article",
                    (store,),
                )
            )
    dates = [r["day"] for r in rows if r["article"] in articles]
    return min(dates) if dates else None


def _write(conn, key, payload, revision, event, actor):
    timestamp = now()
    if revision:
        result = conn.execute(
            "UPDATE economics_daily SET revision=?,payload_json=?,updated_at=? WHERE marketplace=? AND store_slug=? AND article=? AND day=? AND revision=?",
            (revision + 1, encode(payload), timestamp, *key, revision),
        )
    else:
        result = conn.execute(
            "INSERT INTO economics_daily (marketplace,store_slug,article,day,revision,payload_json,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(marketplace,store_slug,article,day) DO NOTHING",
            (*key, 1, encode(payload), timestamp),
        )
    if result.rowcount != 1:
        raise Conflict("Данные уже изменены. Откройте ячейку заново и проверьте расчёт.")
    conn.execute(
        "INSERT INTO economics_daily_events (id,marketplace,store_slug,article,day,revision,payload_json,created_at,actor) VALUES (?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, *key, revision + 1, encode(event), timestamp, actor),
    )


def capture(key, source):
    """Only called by writers/jobs. CAS also protects concurrent job processes."""
    for attempt in range(3):
        try:
            with WRITE_LOCK, get_connection() as conn:
                row = _select(conn, key)
                old = _decode(row) if row else _legacy(conn, key)

                def comparable(item):
                    return {k: v for k, v in item.items() if k != "captured_at"}

                if old and comparable(old["source"]) == comparable(source):
                    return False
                payload = resolved(key[0], source, (old or {}).get("overrides"), (old or {}).get("original"))
                event = {
                    "kind": "source",
                    "source": source,
                    "before": (old or {}).get("values"),
                    "after": payload["values"],
                }
                _write(conn, key, payload, (old or {}).get("revision", 0), event, "Синхронизация")
                conn.commit()
            return True
        except Conflict:
            if attempt == 2:
                raise


def proposal(key, data, field, value, undo=False):
    if not data:
        raise ValueError(
            "Снимок за этот день отсутствует. Нельзя создавать историю из сегодняшних параметров."
        )
    validate_change(key[0], field, value if not undo else ("usn" if field == "tax_system" else 0))
    overlays = dict(data["overrides"])
    if undo:
        if field not in overlays:
            raise ValueError("Для этой ячейки нет действующей корректировки.")
        overlays.pop(field)
    else:
        overlays[field] = value
    updated = resolved(key[0], data["source"], overlays, data["original"])
    preview = {
        "before": data["values"].get(field),
        "after": updated["values"].get(field),
        "before_result": data["result"],
        "after_result": updated["result"],
        "token": data["token"],
    }
    preview["preview_token"] = digest([key, field, value, undo, preview])
    return preview, updated


def refresh_metrics(key, changes, raw):
    """Merge dated metrics into the latest source under CAS, never stale costs."""
    if changes.keys() - {"orders_count", "advertising_spend"}:
        raise ValueError("Обновление дневных метрик не может изменять цену или затраты.")
    for attempt in range(3):
        try:
            with WRITE_LOCK, get_connection() as conn:
                row = _select(conn, key)
                old = _decode(row) if row else _legacy(conn, key)
                if not old or not any(old["source"]["values"].get(k) != v for k, v in changes.items()):
                    return False
                source = {
                    **old["source"],
                    "values": {**old["source"]["values"], **changes},
                    "raw": {**old["source"]["raw"], "daily_metrics": raw},
                }
                payload = resolved(key[0], source, old["overrides"], old["original"])
                _write(
                    conn,
                    key,
                    payload,
                    old["revision"],
                    {"kind": "source", "source": source, "before": old["values"], "after": payload["values"]},
                    "Синхронизация дневных метрик",
                )
                conn.commit()
            return True
        except Conflict:
            if attempt == 2:
                raise


def correct(key, *, token, preview_token, field, value, reason, actor, undo=False):
    if len(reason.strip()) < 3 or len(reason) > 2000:
        raise ValueError("Укажите причину (от 3 до 2000 символов).")
    with WRITE_LOCK, get_connection() as conn:
        row = _select(conn, key)
        old = _decode(row) if row else _legacy(conn, key)
        if not old or old["token"] != token:
            raise Conflict("Данные уже изменены. Откройте ячейку заново.")
        preview, payload = proposal(key, old, field, value, undo)
        if preview_token != preview["preview_token"]:
            raise Conflict("Предварительный расчёт изменился. Выполните проверку ещё раз.")
        _write(
            conn,
            key,
            payload,
            old["revision"],
            {
                "kind": "undo" if undo else "correction",
                "field": field,
                "reason": reason.strip(),
                "before": preview["before"],
                "after": preview["after"],
                "before_result": preview["before_result"],
                "after_result": preview["after_result"],
                "original": old["original"] if not row else None,
            },
            actor,
        )
        conn.commit()
    return get(key)


def wb_report_row(row):
    v, r = row["values"], row["result"]
    return {
        "store_slug": row["store_slug"],
        "article": row["article"],
        "day": row["day"],
        "marketplace": "WB",
        "unit_margin": r.get("margin"),
        "purchase_price": v.get("purchase_price"),
        "inputs_json": encode(v),
        "result_json": encode(r),
        "calculation_version": row.get("version") or 3,
        "price_day": v.get("price_day"),
        "captured_at": row["source"]["captured_at"],
        "daily_ledger": True,
        "overrides": row["overrides"],
    }


def yandex_report_row(row):
    v, r = row["values"], row["result"]
    data = {
        "day": row["day"],
        "scheme": "COMMON",
        "inputs": v,
        "origins": row["source"]["origins"],
        "orders_count": v.get("orders_count"),
        "advertising_spend": v.get("advertising_spend"),
        "expected_buyouts": r.get("expected_buyouts"),
        "profit": r.get("day_profit"),
        "purchase_value": r.get("purchase_value"),
        "calculation_version": row.get("version") or 15,
        "missing": r.get("daily_missing", []),
        "unit_missing": r.get("missing", []),
        "complete": r.get("daily_complete", False),
        "messages": r.get("daily_messages", []),
        "unit_messages": r.get("messages", []),
        "overrides": row["overrides"],
    }
    return {
        "store_slug": row["store_slug"],
        "article": row["article"],
        "day": row["day"],
        "scheme": "COMMON",
        "data": data,
        "payload_json": encode(data),
        "captured_at": row["source"]["captured_at"],
    }
