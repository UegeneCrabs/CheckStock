"""Read-only smoke check of configured supply APIs. Does not open the database."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.stores import STORES
from app.ozon import api as ozon
from app.ozon import inbound as ozon_inbound
from app.ozon import tokens as ozon_tokens
from app.stock.inbound_supplies import MARKETPLACES
from app.wb import api as wb
from app.wb import inbound as wb_inbound
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex
from app.yandex import inbound as yandex_inbound
from app.yandex import tokens as yandex_tokens
from app.yandex.sync import resolve_campaigns


def check(target):
    store, marketplace = target
    result = {"store": store, "marketplace": marketplace}
    try:
        if marketplace == "WB":
            if not wb_tokens.has_token(store):
                return {**result, "status": "not_configured"}
            token = wb_tokens.get_token(store)
            listing = wb._request(
                "POST",
                wb.SUPPLIES_BASE + "/api/v1/supplies",
                token,
                params={"limit": 1, "offset": 0},
                json_body={"dates": [], "statusIDs": [1, 2, 3, 4, 5, 6]},
            )
            if not isinstance(listing, list):
                return {**result, "status": "invalid_response"}
            result["has_supplies"] = bool(listing)
            if listing:
                row = listing[0]
                supply_id = row.get("supplyID") or row.get("preorderID")
                params = {"isPreorderID": str(not bool(row.get("supplyID"))).lower()}
                detail = wb._request(
                    "GET", f"{wb.SUPPLIES_BASE}/api/v1/supplies/{supply_id}", token, params=params
                )
                goods = wb._request(
                    "GET",
                    f"{wb.SUPPLIES_BASE}/api/v1/supplies/{supply_id}/goods",
                    token,
                    params={**params, "limit": 1, "offset": 0},
                )
                result["details_ok"] = isinstance(detail, dict) and "statusID" in detail
                result["items_ok"] = isinstance(goods, list)
                result["normalized_stage"] = wb_inbound.normalize(row, detail, goods).stage
        elif marketplace == "OZON":
            if not ozon_tokens.has_credentials(store):
                return {**result, "status": "not_configured"}
            client_id, key = ozon_tokens.get_credentials(store)
            listing = ozon.request(
                "/v3/supply-order/list",
                client_id,
                key,
                {
                    "filter": {"states": list(ozon_inbound.STATES)},
                    "last_id": "",
                    "limit": 1,
                    "sort_by": "ORDER_CREATION",
                    "sort_dir": "DESC",
                },
            )
            result["has_supplies"] = bool(listing.get("order_ids"))
            if listing.get("order_ids"):
                detail = ozon.request(
                    "/v3/supply-order/get", client_id, key, {"order_ids": [str(listing["order_ids"][0])]}
                )
                result["details_ok"] = isinstance(detail.get("orders"), list)
                supplies = [
                    supply for order in detail.get("orders", []) for supply in order.get("supplies", [])
                ]
                if supplies and supplies[0].get("bundle_id"):
                    data = ozon.request(
                        "/v1/supply-order/bundle",
                        client_id,
                        key,
                        {"bundle_ids": [supplies[0]["bundle_id"]], "limit": 1, "last_id": ""},
                    )
                    result["items_ok"] = isinstance(data.get("items"), list)
                    result["has_next_present"] = "has_next" in data
                    result["normalized_stage"] = ozon_inbound.normalize(
                        detail["orders"][0],
                        supplies[0],
                        data["items"],
                        None,
                    ).stage
                    finished = ozon.request(
                        "/v3/supply-order/list",
                        client_id,
                        key,
                        {
                            "filter": {"states": ["COMPLETED"]},
                            "last_id": "",
                            "limit": 1,
                            "sort_by": "ORDER_STATE_UPDATED_AT",
                            "sort_dir": "DESC",
                        },
                    )
                    if finished.get("order_ids"):
                        closed = ozon.request(
                            "/v3/supply-order/get",
                            client_id,
                            key,
                            {
                                "order_ids": [str(finished["order_ids"][0])],
                            },
                        )
                        if closed.get("orders") and closed["orders"][0].get("supplies"):
                            try:
                                acts = ozon.request(
                                    "/v1/supply-order/act/product/get",
                                    client_id,
                                    key,
                                    {
                                        "supply_id": int(closed["orders"][0]["supplies"][0]["supply_id"]),
                                    },
                                )
                                quantities, _, warning = ozon_inbound.act_quantities(acts)
                                result["acts"] = (
                                    "ambiguous" if warning else "ok" if quantities else "not_published"
                                )
                            except ozon.OzonApiError as error:
                                result["acts"] = f"http_{error.status}"
        else:
            if not yandex_tokens.has_credentials(store):
                return {**result, "status": "not_configured"}
            key = yandex_tokens.get_api_key(store)
            campaigns = [
                campaign
                for campaign in resolve_campaigns(store, key)
                if campaign.get("scheme") in ("fby", "fbo")
            ]
            result["fby_campaigns"] = len(campaigns)
            for campaign in campaigns:
                path = f"/v2/campaigns/{campaign['id']}/supply-requests"
                listing = yandex.request(path, key, payload={"requestTypes": ["SUPPLY"]}, params={"limit": 1})
                if not isinstance(listing.get("requests"), list):
                    return {**result, "status": "invalid_response"}
                result["has_supplies"] = result.get("has_supplies", False) or bool(listing["requests"])
                if listing["requests"]:
                    data = yandex.request(
                        path + "/items",
                        key,
                        payload={"requestId": listing["requests"][0]["id"]["id"]},
                        params={"limit": 1},
                    )
                    result["items_ok"] = isinstance(data.get("items"), list)
                    result["normalized_stage"] = yandex_inbound.normalize(
                        campaign, listing["requests"][0], data["items"]
                    ).stage
        return {**result, "status": "ok"}
    except Exception as error:
        return {
            **result,
            "status": "error",
            "http_status": getattr(error, "status", None),
            "error_type": type(error).__name__,
        }


if __name__ == "__main__":
    wb.REQUEST_ATTEMPTS = ozon.MAX_ATTEMPTS = yandex.MAX_ATTEMPTS = 1
    wb.REQUEST_TIMEOUT = ozon.REQUEST_TIMEOUT = yandex.REQUEST_TIMEOUT = 15
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(check, ((store, mp) for store in STORES for mp in MARKETPLACES)):
            print(json.dumps(result, ensure_ascii=False), flush=True)
