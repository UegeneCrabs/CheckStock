from app.application.inbound_supplies import InboundSourceError
from app.dto.inbound_supplies import TERMINAL_STAGES, InboundItem, InboundSupply
from app.stock.inbound_common import MAX_PAGES, count, identifier, object_row, recent, rows
from app.yandex import api

STATES = {
    "CREATED": ("planned", "Создана"),
    "VALIDATED": ("planned", "В обработке"),
    "PUBLISHED": ("planned", "На утверждении"),
    "ACCEPTED_BY_WAREHOUSE_SYSTEM": ("planned", "Заявка утверждена"),
    "NEED_PREPARATION": ("planned", "Ожидает данных продавца"),
    "REGISTERED_IN_ELECTRONIC_QUEUE": ("acceptance", "В электронной очереди"),
    "ARRIVED_TO_SERVICE": ("acceptance", "Поставка отгружена"),
    "ARRIVED_TO_XDOC_SERVICE": ("transit", "На транзитном складе"),
    "SHIPPED_TO_SERVICE": ("transit", "Едет на склад хранения"),
    "WAREHOUSE_HANDLING": ("acceptance", "Потоварная приёмка"),
    "WAREHOUSE_SIGNED_ACT": ("acceptance", "Акт подписан складом"),
    "FINISHED": ("completed", "Завершена"),
    "CANCELLED": ("cancelled", "Отменена"),
    "INVALID": ("discrepancy", "Ошибка обработки"),
    "CANCELLATION_REQUESTED": ("unknown", "Запрошена отмена"),
    "CANCELLATION_REJECTED": ("unknown", "Отмена отклонена"),
}
INBOUND_SUBTYPES = {
    "DEFAULT",
    "XDOC",
    "VIRTUAL_DISTRIBUTION_CENTER",
    "VIRTUAL_DISTRIBUTION_CENTER_CHILD",
    "ADDITIONAL_SUPPLY",
}


def paged(key: str, path: str, field: str, payload: dict) -> list[dict]:
    result = []
    cursor = ""
    seen = set()
    for _ in range(MAX_PAGES):
        data = object_row(
            api.request(path, key, payload=payload, params={"limit": 100, "pageToken": cursor}),
            "поставки Яндекс Маркета",
        )
        batch = rows(data.get(field), field)
        result.extend(batch)
        paging = object_row(data.get("paging") or {}, "страницы Яндекс Маркета")
        next_cursor = str(paging.get("nextPageToken") or "")
        if not next_cursor:
            return result
        if next_cursor in seen or not batch:
            raise InboundSourceError("Яндекс Маркет повторил страницу поставок. Предыдущие данные сохранены.")
        seen.add(next_cursor)
        cursor = next_cursor
    raise InboundSourceError("Поставки Яндекс Маркета получены не полностью.")


def request_id(row: dict) -> str:
    return identifier(
        object_row(row.get("id"), "номер заявки Яндекс Маркета").get("id"), "заявка Яндекс Маркета"
    )


def normalize(campaign: dict, row: dict, goods: list[dict], parent: dict | None = None) -> InboundSupply:
    internal_id = request_id(row)
    ids = row["id"]
    status = str(row.get("status") or "UNKNOWN")
    stage, label = STATES.get(status, ("unknown", status))
    items = []
    seen = set()
    for good in goods:
        article = identifier(good.get("offerId"), "артикул Яндекс Маркета")
        if article in seen:
            raise InboundSourceError("Яндекс Маркет повторил артикул в составе поставки.")
        seen.add(article)
        counters = object_row(good.get("counters"), "количества в поставке Яндекс Маркета")
        items.append(
            InboundItem(
                article=article,
                name=str(good.get("name") or ""),
                quantity=count(counters.get("planCount"), "Яндекс planCount"),
                accepted_quantity=count(counters.get("factCount"), "Яндекс factCount", optional=True),
                shortage_quantity=count(counters.get("shortageCount"), "Яндекс shortageCount", optional=True),
                surplus_quantity=count(counters.get("surplusCount"), "Яндекс surplusCount", optional=True),
                defect_quantity=count(counters.get("defectCount"), "Яндекс defectCount", optional=True),
            )
        )
    if stage == "completed" and any(
        item.shortage_quantity or item.defect_quantity or item.surplus_quantity for item in items
    ):
        stage = "discrepancy"
    target = object_row(row.get("targetLocation") or {}, "склад назначения Яндекс Маркета")
    transit = object_row(
        row.get("transitLocation") or (parent or {}).get("transitLocation") or {}, "транзит Яндекс Маркета"
    )
    parent_link = object_row(row.get("parentLink") or {}, "связь с родительской заявкой")
    linked_parent = object_row(parent_link.get("id") or {}, "номер родительской заявки").get("id")
    return InboundSupply(
        key=f"{campaign['id']}:{internal_id}",
        supply_id=internal_id,
        parent_key=f"{campaign['id']}:{linked_parent}"
        if linked_parent and parent_link.get("type") == "VIRTUAL_DISTRIBUTION"
        else "",
        number=str(ids.get("marketplaceRequestId") or ids.get("warehouseRequestId") or internal_id),
        campaign_id=str(campaign["id"]),
        campaign_name=str(campaign.get("name") or ""),
        stage=stage,
        status=status,
        status_label=label,
        warehouse=str(target.get("name") or target.get("serviceId") or ""),
        transit_warehouse=str(transit.get("name") or transit.get("serviceId") or ""),
        planned_at=str(target.get("requestedDate") or transit.get("requestedDate") or ""),
        updated_at=str(row.get("updatedAt") or ""),
        items=tuple(items),
    )


def load(
    key: str, campaigns: list[dict], previous: tuple[InboundSupply, ...] = ()
) -> tuple[InboundSupply, ...]:
    result = []
    known_open = {supply.key for supply in previous if supply.stage not in TERMINAL_STAGES}
    for campaign in campaigns:
        if campaign.get("scheme") not in {"fby", "fbo"}:
            continue
        base = f"/v2/campaigns/{campaign['id']}/supply-requests"
        listing = paged(key, base, "requests", {"requestTypes": ["SUPPLY"]})
        by_id = {request_id(row): row for row in listing}
        if len(by_id) != len(listing):
            raise InboundSourceError("Яндекс Маркет повторил заявки при загрузке списка.")
        missing = [
            supply.supply_id
            for supply in previous
            if supply.campaign_id == str(campaign["id"])
            and supply.key in known_open
            and supply.supply_id not in by_id
        ]
        for start in range(0, len(missing), 100):
            fetched = paged(
                key, base, "requests", {"requestIds": [int(value) for value in missing[start : start + 100]]}
            )
            by_id.update({request_id(row): row for row in fetched})
        children_by_parent: dict[str, list[str]] = {}
        for row in by_id.values():
            parent = row.get("parentLink") or {}
            if parent.get("type") == "VIRTUAL_DISTRIBUTION" and parent.get("id"):
                parent_id = identifier(
                    object_row(parent["id"], "родительская заявка Яндекс Маркета").get("id"),
                    "родительская заявка",
                )
                children_by_parent.setdefault(parent_id, []).append(request_id(row))
        for internal_id, row in by_id.items():
            if row.get("type") != "SUPPLY" or row.get("subtype") not in INBOUND_SUBTYPES:
                continue
            child_ids = [
                str(link["id"]["id"])
                for link in (row.get("childrenLinks") or [])
                if isinstance(link, dict)
                and link.get("type") == "VIRTUAL_DISTRIBUTION"
                and isinstance(link.get("id"), dict)
            ]
            child_ids = list(dict.fromkeys([*child_ids, *children_by_parent.get(internal_id, [])]))
            if child_ids:
                if any(child_id not in by_id for child_id in child_ids):
                    raise InboundSourceError(
                        "Не получены все дочерние поставки Яндекс Маркета. Состав сохранён до полного обновления."
                    )
                continue
            supply_key = f"{campaign['id']}:{internal_id}"
            stage = STATES.get(str(row.get("status")), ("unknown", ""))[0]
            parent = row.get("parentLink") or {}
            parent_id = str((parent.get("id") or {}).get("id") or "")
            parent_was_open = f"{campaign['id']}:{parent_id}" in known_open
            if (
                stage in TERMINAL_STAGES
                and supply_key not in known_open
                and not parent_was_open
                and not recent(row.get("updatedAt"))
            ):
                continue
            goods = paged(key, f"{base}/items", "items", {"requestId": int(internal_id)})
            result.append(normalize(campaign, row, goods, by_id.get(parent_id)))
    return tuple(result)
