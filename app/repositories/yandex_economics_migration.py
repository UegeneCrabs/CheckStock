"""Move the existing cabinet commission once; subsequent imports cannot change it."""

import json
from datetime import UTC, datetime

from app.repositories.core import WRITE_LOCK


def migrate_company_commission(database):
    marker = "migration:company-commission-v6"
    timestamp = datetime.now(UTC).isoformat()
    with WRITE_LOCK, database.connect() as connection:
        rows = connection.execute(
            "SELECT store_slug,team_commission_percent FROM unit_economics_1c_cabinet_settings "
            "WHERE marketplace='YANDEX MARKET'"
        ).fetchall()
        for row in rows:
            store, commission = row["store_slug"], row["team_commission_percent"]
            if (
                commission is None
                or connection.execute(
                    "SELECT 1 FROM yandex_economics_sources WHERE store_slug=? AND article='' AND source=?",
                    (store, marker),
                ).fetchone()
            ):
                continue
            for scheme in ("FBY", "FBS"):
                saved = connection.execute(
                    "SELECT payload_json FROM yandex_economics_settings WHERE store_slug=? AND article='' AND scheme=?",
                    (store, scheme),
                ).fetchone()
                values = json.loads(saved["payload_json"]) if saved else {}
                if "company_commission_percent" in values:
                    continue
                values["company_commission_percent"] = commission
                connection.execute(
                    "INSERT INTO yandex_economics_settings "
                    "(store_slug,article,scheme,revision,payload_json,updated_at,updated_by) VALUES (?,'',?,1,?,?,?) "
                    "ON CONFLICT(store_slug,article,scheme) DO UPDATE SET "
                    "revision=yandex_economics_settings.revision+1,payload_json=excluded.payload_json,"
                    "updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                    (
                        store,
                        scheme,
                        json.dumps(values, ensure_ascii=False),
                        timestamp,
                        "Перенос комиссии кабинета",
                    ),
                )
            connection.execute(
                "INSERT INTO yandex_economics_sources (store_slug,article,source,payload_json,updated_at) "
                "VALUES (?,'',?,?,?) ON CONFLICT(store_slug,article,source) DO NOTHING",
                (store, marker, json.dumps({"commission_percent": commission}), timestamp),
            )
        connection.commit()
