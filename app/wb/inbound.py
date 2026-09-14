from app.application.inbound_supplies import InboundSourceError
from app.dto.inbound_supplies import TERMINAL_STAGES, InboundItem, InboundSupply
from app.stock.inbound_common import MAX_PAGES, count, identifier, object_row, recent, rows
from app.wb import api

STATUS_LABELS = {
    1: "Не запланировано",
    2: "Запланировано",
    3: "Отгрузка разрешена",
    4: "Идёт приёмка",
    5: "Принято",
    6: "Отгружено на воротах",
}


def list_supplies(token: str) -> list[dict]:
    result = []
    seen = set()
    for page in range(MAX_PAGES):
        batch = rows(
            api._request(
                "POST",
                f"{api.SUPPLIES_BASE}/api/v1/supplies",
                token,
                params={"limit": 1000, "offset": page * 1000},
                json_body={"dates": [], "statusIDs": [1, 2, 3, 4, 5, 6]},
            ),
            "поставки WB",
        )
        for row in batch:
            key = supply_key(row)
            if key in seen:
                raise InboundSourceError(
                    "WB повторил поставки при постраничной загрузке. Повторим обновление позже."
                )
            seen.add(key)
        result.extend(batch)
        if len(batch) < 1000:
            return result
    raise InboundSourceError("Список поставок WB получен не полностью.")


def supply_key(row: dict) -> str:
    if row.get("preorderID"):
        return f"preorder:{identifier(row['preorderID'], 'заказ WB')}"
    return f"supply:{identifier(row.get('supplyID'), 'поставка WB')}"


def get_goods(token: str, supply_id: str, preorder: bool) -> list[dict]:
    result = []
    seen = set()
    for page in range(MAX_PAGES):
        batch = rows(
            api._request(
                "GET",
                f"{api.SUPPLIES_BASE}/api/v1/supplies/{supply_id}/goods",
                token,
                params={"limit": 1000, "offset": page * 1000, "isPreorderID": str(preorder).lower()},
            ),
            "товары поставки WB",
        )
        for row in batch:
            key = (row.get("nmID"), row.get("barcode"), row.get("techSize"))
            if key in seen:
                raise InboundSourceError("WB повторил товарные строки. Предыдущий состав сохранён.")
            seen.add(key)
        result.extend(batch)
        if len(batch) < 1000:
            return result
    raise InboundSourceError("Товарный состав WB получен не полностью.")


def normalize(summary: dict, detail: dict, goods: list[dict]) -> InboundSupply:
    data = {**summary, **detail}
    if not goods and data.get("quantity"):
        raise InboundSourceError("WB вернул пустой состав непустой поставки. Предыдущие данные сохранены.")
    status = int(data.get("statusID") or 0)
    items = tuple(
        InboundItem(
            article=str(row.get("nmID") or row.get("vendorCode") or ""),
            sku=str(row.get("nmID") or ""),
            vendor_code=str(row.get("vendorCode") or ""),
            barcode=identifier(row.get("barcode"), "баркод WB"),
            quantity=count(row.get("quantity"), "WB quantity"),
            accepted_quantity=count(row.get("acceptedQuantity"), "WB acceptedQuantity", optional=True),
            ready_quantity=count(row.get("readyForSaleQuantity"), "WB readyForSaleQuantity", optional=True),
            shortage_quantity=(
                max(int(row["quantity"]) - int(row["acceptedQuantity"]), 0)
                if status == 5 and row.get("acceptedQuantity") is not None
                else None
            ),
        )
        for row in goods
    )
    stage = {1: "planned", 2: "planned", 3: "planned", 4: "acceptance", 6: "acceptance"}.get(
        status, "unknown"
    )
    if status == 6 and data.get("transitWarehouseID"):
        stage = "transit"
    note = ""
    if status == 5:
        if any(item.shortage_quantity or 0 for item in items):
            stage = "discrepancy"
        elif items and all(
            item.ready_quantity is not None and item.ready_quantity >= item.quantity for item in items
        ):
            stage = "completed"
        else:
            stage = "placement"
        note = "После приёмки в СЦ товары могут ещё перемещаться на склад хранения. Готовность к продаже — по данным поставки WB."
    supply_id = identifier(data.get("supplyID") or data.get("preorderID"), "поставка WB")
    return InboundSupply(
        key=supply_key(data),
        supply_id=supply_id,
        order_id=str(data.get("preorderID") or ""),
        number=supply_id,
        status=str(status),
        status_label=STATUS_LABELS.get(status, f"Статус {status}"),
        stage=stage,
        warehouse=str(data.get("actualWarehouseName") or data.get("warehouseName") or ""),
        transit_warehouse=str(data.get("transitWarehouseName") or ""),
        planned_at=str(data.get("supplyDate") or ""),
        created_at=str(data.get("createDate") or ""),
        updated_at=str(data.get("updatedDate") or ""),
        items=items,
        note=note,
    )


def load(token: str, previous: tuple[InboundSupply, ...] = ()) -> tuple[InboundSupply, ...]:
    summaries = list_supplies(token)
    known = {supply.key: supply for supply in previous}
    listed = {supply_key(row) for row in summaries}
    for supply in previous:
        if supply.key not in listed and supply.stage not in TERMINAL_STAGES:
            summaries.append(
                {
                    "supplyID": supply.supply_id
                    if not supply.key.startswith("preorder:") or supply.supply_id != supply.order_id
                    else None,
                    "preorderID": supply.order_id or None,
                }
            )
    result = []
    for row in summaries:
        key = supply_key(row)
        old = known.get(key)
        if row.get("statusID") == 5 and not recent(
            row.get("updatedDate") or row.get("factDate") or row.get("createDate")
        ):
            if old is None or old.stage in TERMINAL_STAGES:
                continue
        preorder = not bool(row.get("supplyID"))
        supply_id = identifier(row.get("supplyID") or row.get("preorderID"), "поставка WB")
        try:
            detail = object_row(
                api._request(
                    "GET",
                    f"{api.SUPPLIES_BASE}/api/v1/supplies/{supply_id}",
                    token,
                    params={"isPreorderID": str(preorder).lower()},
                ),
                "детали поставки WB",
            )
            result.append(normalize(row, detail, get_goods(token, supply_id, preorder)))
        except api.WBApiError as error:
            if error.status == 404 and key not in listed:
                continue
            raise
    return tuple(result)
