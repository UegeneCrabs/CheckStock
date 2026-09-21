from app.application.inbound_supplies import InboundSourceError
from app.dto.inbound_supplies import TERMINAL_STAGES, InboundItem, InboundSupply
from app.ozon import api
from app.stock.inbound_common import MAX_PAGES, count, identifier, object_row, recent, rows

STATES = {
    "DATA_FILLING": ("planned", "Заполнение данных"),
    "READY_TO_SUPPLY": ("planned", "Готова к отгрузке"),
    "ACCEPTED_AT_SUPPLY_WAREHOUSE": ("transit", "Принята в пункте отгрузки"),
    "IN_TRANSIT": ("transit", "В пути"),
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE": ("acceptance", "Приёмка на складе"),
    "REPORTS_CONFIRMATION_AWAITING": ("acceptance", "Согласование актов"),
    "REPORT_REJECTED": ("discrepancy", "Спор по актам"),
    "COMPLETED": ("completed", "Завершена"),
    "REJECTED_AT_SUPPLY_WAREHOUSE": ("cancelled", "Отказано в приёмке"),
    "CANCELLED": ("cancelled", "Отменена"),
    "OVERDUE": ("cancelled", "Просрочена"),
}
ACT_STATES = {
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE",
    "REPORTS_CONFIRMATION_AWAITING",
    "REPORT_REJECTED",
    "COMPLETED",
}


def order_ids(client_id: str, key: str) -> list[str]:
    result = []
    seen = set()
    cursor = ""
    for _ in range(MAX_PAGES):
        data = object_row(
            api.request(
                "/v3/supply-order/list",
                client_id,
                key,
                {
                    "filter": {"states": list(STATES)},
                    "last_id": cursor,
                    "limit": 100,
                    "sort_by": "ORDER_CREATION",
                    "sort_dir": "DESC",
                },
            ),
            "список заявок Ozon",
        )
        ids = data.get("order_ids")
        if not isinstance(ids, list):
            raise InboundSourceError("Ozon не вернул список идентификаторов поставок.")
        ids = [identifier(value, "заявка Ozon") for value in ids]
        if any(value in seen for value in ids) or len(set(ids)) != len(ids):
            raise InboundSourceError("Ozon повторил страницу заявок. Предыдущие данные сохранены.")
        seen.update(ids)
        result.extend(ids)
        if len(ids) < 100:
            return result
        next_cursor = str(data.get("last_id") or "")
        if not next_cursor or next_cursor == cursor:
            raise InboundSourceError("Ozon не вернул следующую страницу заявок.")
        cursor = next_cursor
    raise InboundSourceError("Список заявок Ozon получен не полностью.")


def bundle_items(client_id: str, key: str, bundle_id: str) -> list[dict]:
    result = []
    cursor = ""
    seen = set()
    for _ in range(MAX_PAGES):
        data = object_row(
            api.request(
                "/v1/supply-order/bundle",
                client_id,
                key,
                {
                    "bundle_ids": [bundle_id],
                    "limit": 100,
                    "last_id": cursor,
                },
            ),
            "состав поставки Ozon",
        )
        batch = rows(data.get("items"), "товары поставки Ozon")
        for row in batch:
            sku = identifier(row.get("sku"), "SKU поставки Ozon")
            if sku in seen:
                raise InboundSourceError("Ozon повторил товарные строки поставки.")
            seen.add(sku)
        result.extend(batch)
        if data.get("has_next") is False:
            return result
        if data.get("has_next") is not True:
            raise InboundSourceError("Ozon не указал, полностью ли получен состав поставки.")
        next_cursor = str(data.get("last_id") or "")
        if not batch or not next_cursor or next_cursor == cursor:
            raise InboundSourceError("Ozon не вернул следующую страницу товаров поставки.")
        cursor = next_cursor
    raise InboundSourceError("Состав поставки Ozon получен не полностью.")


def act_quantities(data: dict) -> tuple[dict[str, int | None], bool, str]:
    quantities: dict[str, int | None] = {}
    ambiguous = False
    for act in rows(data.get("supply_acts"), "акты поставки Ozon"):
        for item in rows(act.get("items"), "товары акта Ozon"):
            sku = identifier(object_row(item.get("sku_info"), "товар акта Ozon").get("sku"), "SKU акта Ozon")
            quantity = count(item.get("fact_quantity"), "факт в акте Ozon", optional=True)
            if sku in quantities and quantities[sku] != quantity:
                quantities[sku] = None
                ambiguous = True
            else:
                quantities[sku] = quantity
    defects = bool(rows(data.get("skus_defects", []), "брак в актах Ozon"))
    warning = (
        "В актах Ozon различается фактическое количество. Требуется сверка документов." if ambiguous else ""
    )
    return quantities, defects, warning


def normalize(
    order: dict, supply: dict, goods: list[dict], acts: dict | None, act_warning: str = ""
) -> InboundSupply:
    order_id = identifier(order.get("order_id"), "заявка Ozon")
    supply_id = identifier(supply.get("supply_id"), "поставка Ozon")
    status = str(supply.get("state") or order.get("state") or "UNSPECIFIED")
    stage, label = STATES.get(status, ("unknown", status))
    quantities, defects, warning = act_quantities(acts) if acts is not None else ({}, False, "")
    items = []
    for row in goods:
        sku = identifier(row.get("sku"), "SKU поставки Ozon")
        quantity = count(row.get("quantity"), "Ozon quantity")
        accepted = quantities.get(sku)
        shortage = max(quantity - accepted, 0) if accepted is not None and status == "COMPLETED" else None
        items.append(
            InboundItem(
                article=identifier(row.get("offer_id"), "артикул Ozon"),
                sku=sku,
                barcode=str(row.get("barcode") or ""),
                name=str(row.get("name") or ""),
                quantity=quantity,
                accepted_quantity=accepted,
                shortage_quantity=shortage,
                surplus_quantity=max(accepted - quantity, 0) if accepted is not None else None,
            )
        )
    if stage == "completed" and (
        defects or any(item.shortage_quantity or item.surplus_quantity for item in items)
    ):
        stage = "discrepancy"
    warehouse = object_row(supply.get("storage_warehouse") or {}, "склад хранения Ozon")
    dropoff = object_row(order.get("dropoff_warehouse") or {}, "пункт отгрузки Ozon")
    timeslot = object_row(supply.get("timeslot") or order.get("timeslot") or {}, "таймслот Ozon")
    interval = object_row(timeslot.get("timeslot") or {}, "интервал Ozon")
    return InboundSupply(
        key=supply_id,
        supply_id=supply_id,
        order_id=order_id,
        number=str(order.get("order_number") or order_id),
        stage=stage,
        status=status,
        status_label=label,
        warehouse=str(warehouse.get("name") or warehouse.get("warehouse_id") or ""),
        transit_warehouse=str(dropoff.get("name") or dropoff.get("warehouse_id") or "")
        if supply.get("is_crossdock")
        else "",
        planned_at=str(interval.get("from") or ""),
        created_at=str(order.get("created_date") or ""),
        updated_at=str(order.get("state_updated_date") or ""),
        note="В актах есть сведения о браке." if defects else "",
        warning=" ".join(value for value in (act_warning, warning) if value),
        items=tuple(items),
    )


def load(client_id: str, key: str, previous: tuple[InboundSupply, ...] = ()) -> tuple[InboundSupply, ...]:
    ids = order_ids(client_id, key)
    known_open = {supply.order_id for supply in previous if supply.stage not in TERMINAL_STAGES}
    missing = sorted(known_open - set(ids))
    batches = [ids[start : start + 50] for start in range(0, len(ids), 50)] + [[value] for value in missing]
    result = []
    acts_unavailable = ""
    for batch_ids in batches:
        try:
            response = object_row(
                api.request("/v3/supply-order/get", client_id, key, {"order_ids": batch_ids}), "заявки Ozon"
            )
        except api.OzonApiError as error:
            if error.status == 404 and set(batch_ids).issubset(missing):
                continue
            raise
        orders = rows(response.get("orders"), "заявки Ozon")
        received = {identifier(order.get("order_id"), "заявка Ozon") for order in orders}
        if (set(batch_ids) - received) - set(missing) or received - set(batch_ids):
            raise InboundSourceError("Ozon вернул подробности не всех запрошенных заявок.")
        for order in orders:
            order_id = str(order["order_id"])
            supplies = rows(order.get("supplies"), "поставки в заявке Ozon")
            for supply in supplies:
                status = str(supply.get("state") or order.get("state") or "UNSPECIFIED")
                if STATES.get(status, ("unknown", ""))[0] in TERMINAL_STAGES:
                    if order_id not in known_open and not recent(
                        order.get("state_updated_date") or order.get("created_date")
                    ):
                        continue
                if not supply.get("bundle_id") and status in {
                    "DATA_FILLING",
                    "CANCELLED",
                    "OVERDUE",
                    "REJECTED_AT_SUPPLY_WAREHOUSE",
                }:
                    goods = []
                else:
                    bundle_id = identifier(supply.get("bundle_id"), "состав поставки Ozon")
                    goods = bundle_items(client_id, key, bundle_id)
                acts = None
                warning = acts_unavailable if status in ACT_STATES else ""
                if status in ACT_STATES and not acts_unavailable:
                    try:
                        acts = object_row(
                            api.request(
                                "/v1/supply-order/act/product/get",
                                client_id,
                                key,
                                {
                                    "supply_id": int(identifier(supply.get("supply_id"), "поставка Ozon")),
                                },
                            ),
                            "акты поставки Ozon",
                        )
                    except api.OzonApiError as error:
                        if error.status in (401, 403):
                            reason = "Нет доступа к актам приёмки Ozon. Проверьте права ключа."
                        elif error.status == 429:
                            reason = "Ozon ограничил частоту запросов актов приёмки. Загрузка повторится при следующем обновлении."
                        elif error.status and error.status >= 500:
                            reason = "Сервис актов приёмки Ozon временно недоступен."
                        else:
                            reason = "Акты приёмки Ozon пока не получены."
                        code = f" Код {error.status}." if error.status else ""
                        warning = f"{reason}{code} Фактическое количество не подтверждено."
                        if error.status in {401, 403, 429} or (error.status and error.status >= 500):
                            acts_unavailable = warning
                result.append(normalize(order, supply, goods, acts, warning))
    return tuple(result)
