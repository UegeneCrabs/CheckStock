from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

from app.application.inbound_supplies import InboundSourceError
from app.core.constants import FBO_STOCK, OZON_INBOUND, HTTPStatus
from app.dto.inbound_supplies import TERMINAL_STAGES, InboundItem, InboundStage, InboundSupply
from app.dto.orders import OzonCredentials
from app.dto.ozon_inbound import (
    OzonSupply,
    SupplyActRequest,
    SupplyActs,
    SupplyBundleRequest,
    SupplyClusterRequest,
    SupplyClusters,
    SupplyDetailsPage,
    SupplyDetailsRequest,
    SupplyGood,
    SupplyGoodsPage,
    SupplyListPage,
    SupplyListRequest,
    SupplyOrder,
    SupplyStateFilter,
)
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError
from app.ozon.async_api import AsyncSellerApi
from app.stock.inbound_common import MAX_PAGES

STATES: Final[Mapping[str, tuple[InboundStage, str]]] = {
    FBO_STOCK.ozon_data_filling: ("planned", "Заполнение данных"),
    "READY_TO_SUPPLY": ("planned", "Готова к отгрузке"),
    "ACCEPTED_AT_SUPPLY_WAREHOUSE": ("transit", "Принята в пункте отгрузки"),
    "IN_TRANSIT": ("transit", "В пути"),
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE": ("acceptance", "Приёмка на складе"),
    "REPORTS_CONFIRMATION_AWAITING": ("acceptance", "Согласование актов"),
    "REPORT_REJECTED": ("discrepancy", "Спор по актам"),
    "COMPLETED": ("completed", "Завершена"),
    "REJECTED_AT_SUPPLY_WAREHOUSE": ("cancelled", "Отказано в приёмке"),
    "CANCELLED": ("cancelled", "Отменена"),
    "OVERDUE": ("transit", "Просрочена"),
}


def act_quantities(acts: SupplyActs) -> tuple[dict[str, int | None], bool, str]:
    quantities: dict[str, int | None] = {}
    ambiguous = False
    for act in acts.supply_acts:
        if act.type != OZON_INBOUND.acceptance_act:
            continue
        for item in act.items:
            sku = item.sku_info.sku
            if sku in quantities and quantities[sku] != item.fact_quantity:
                quantities[sku] = None
                ambiguous = True
            else:
                quantities[sku] = item.fact_quantity
    warning = "В актах Ozon различается фактическое количество." if ambiguous else ""
    return quantities, bool(acts.skus_defects), warning


def normalize(
    order: SupplyOrder,
    supply: OzonSupply,
    goods: tuple[SupplyGood, ...],
    acts: SupplyActs | None,
    *,
    clusters: Mapping[str, str] | None = None,
    act_warning: str = "",
) -> InboundSupply:
    status = supply.state or order.state
    stage, label = STATES.get(status, ("unknown", status))
    quantities, defects, warning = act_quantities(acts) if acts is not None else ({}, False, "")
    items = []
    for row in goods:
        if not row.offer_id or not row.sku:
            raise InboundSourceError("Ozon не вернул артикул или SKU товара поставки.")
        accepted = quantities.get(row.sku)
        items.append(
            InboundItem(
                article=row.offer_id,
                sku=row.sku,
                barcode=row.barcode,
                name=row.name or "",
                quantity=row.quantity,
                accepted_quantity=accepted,
                shortage_quantity=max(row.quantity - accepted, 0)
                if accepted is not None and status == FBO_STOCK.ozon_accepted
                else None,
                surplus_quantity=max(accepted - row.quantity, 0) if accepted is not None else None,
            )
        )
    warehouse = supply.storage_warehouse.display if supply.storage_warehouse else ""
    dropoff = order.drop_off_warehouse.display if order.drop_off_warehouse else ""
    timeslot = supply.timeslot or order.timeslot
    planned = timeslot.timeslot.start if timeslot and timeslot.timeslot else None
    return InboundSupply(
        key=supply.supply_id,
        supply_id=supply.supply_id,
        order_id=order.order_id,
        number=order.order_number or order.order_id,
        stage=stage,
        status=status,
        status_label=label,
        warehouse=warehouse or (dropoff if not supply.is_crossdock else ""),
        transit_warehouse=dropoff if supply.is_crossdock else "",
        destination_cluster=(clusters or {}).get(supply.macrolocal_cluster_id, ""),
        destination_cluster_id=supply.macrolocal_cluster_id,
        planned_at=planned.isoformat() if planned else "",
        created_at=order.created_date.isoformat() if order.created_date else "",
        updated_at=order.state_updated_date.isoformat() if order.state_updated_date else "",
        note="В актах есть сведения о браке." if defects else "",
        warning=" ".join(value for value in (act_warning, warning) if value),
        composition_confirmed=bool(supply.bundle_id),
        items=tuple(items),
    )


class AsyncOzonInbound(AsyncSellerApi):
    def __init__(self, http: AsyncHttp, credentials: OzonCredentials):
        super().__init__(http, credentials)
        self._acts = AsyncSellerApi(http, credentials, request_attempts=1)

    async def order_ids(self, states: tuple[str, ...], *, since: datetime | None = None) -> tuple[str, ...]:
        result: dict[str, None] = {}
        cursors = {""}
        cursor = ""
        for _ in range(MAX_PAGES):
            page = SupplyListPage.model_validate(
                (
                    await self._response(
                        OZON_INBOUND.list_path,
                        SupplyListRequest(filter=SupplyStateFilter(states=states), last_id=cursor),
                    )
                ).json()
            )
            if (
                not all(page.order_ids)
                or len(set(page.order_ids)) != len(page.order_ids)
                or result.keys() & set(page.order_ids)
            ):
                raise InboundSourceError("Ozon повторил страницу заявок или не передал номер.")
            result.update(dict.fromkeys(page.order_ids))
            if since is not None and page.order_ids:
                last = await self.details(page.order_ids[-1:])
                stamp = last[0].state_updated_date or last[0].created_date
                if stamp is not None and stamp < since:
                    return tuple(result)
            if page.last_id is None and len(page.order_ids) == OZON_INBOUND.page_size:
                raise InboundSourceError("Ozon не вернул следующую страницу заявок.")
            if not page.last_id:
                return tuple(result)
            if not page.order_ids or page.last_id in cursors:
                raise InboundSourceError("Ozon повторил указатель страницы заявок.")
            cursors.add(page.last_id)
            cursor = page.last_id
        raise InboundSourceError("Список заявок Ozon получен не полностью.")

    async def details(self, ids: tuple[str, ...], *, allow_missing: bool = False) -> tuple[SupplyOrder, ...]:
        try:
            response = await self._response(OZON_INBOUND.details_path, SupplyDetailsRequest(order_ids=ids))
        except OzonApiError as error:
            if allow_missing and error.status == HTTPStatus.NOT_FOUND:
                return ()
            raise
        orders = SupplyDetailsPage.model_validate(response.json()).orders
        received = {order.order_id for order in orders}
        if len(received) != len(orders) or received - set(ids) or (not allow_missing and set(ids) - received):
            raise InboundSourceError("Ozon вернул подробности не всех запрошенных заявок.")
        return orders

    async def goods(self, bundle_id: str) -> tuple[SupplyGood, ...]:
        result: dict[str, SupplyGood] = {}
        cursor = ""
        cursors = {cursor}
        for _ in range(MAX_PAGES):
            page = SupplyGoodsPage.model_validate(
                (
                    await self._response(
                        OZON_INBOUND.bundle_path, SupplyBundleRequest(bundle_ids=(bundle_id,), last_id=cursor)
                    )
                ).json()
            )
            for item in page.items:
                if item.sku in result:
                    raise InboundSourceError("Ozon повторил товарные строки поставки.")
                result[item.sku] = item
            if not page.has_next:
                return tuple(result.values())
            if not page.items or not page.last_id or page.last_id in cursors:
                raise InboundSourceError("Ozon не вернул следующую страницу товаров поставки.")
            cursor = page.last_id
            cursors.add(cursor)
        raise InboundSourceError("Состав поставки Ozon получен не полностью.")

    async def clusters(self) -> dict[str, str]:
        try:
            response = await self._response(OZON_INBOUND.clusters_path, SupplyClusterRequest())
            return {
                cluster.macrolocal_cluster_id: cluster.name
                for cluster in SupplyClusters.model_validate(response.json()).clusters
                if cluster.macrolocal_cluster_id and cluster.name
            }
        except (OzonApiError, ValueError):
            return {}

    async def load(self, previous: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]:
        known = {supply.key: supply for supply in previous}
        open_ids = {
            supply.order_id for supply in previous if supply.stage not in TERMINAL_STAGES and supply.order_id
        }
        closed_states = (FBO_STOCK.ozon_accepted, *sorted(FBO_STOCK.ozon_returned))
        active_ids = await self.order_ids(tuple(state for state in STATES if state not in closed_states))
        cutoff = datetime.now(UTC) - timedelta(days=FBO_STOCK.history_days)
        closed_ids = await self.order_ids(closed_states, since=cutoff)
        ids = tuple(dict.fromkeys((*active_ids, *closed_ids)))
        missing = sorted(open_ids - set(ids))
        clusters = await self.clusters()
        orders: list[SupplyOrder] = []
        for offset in range(0, len(ids), OZON_INBOUND.details_batch_size):
            orders.extend(await self.details(ids[offset : offset + OZON_INBOUND.details_batch_size]))
        for order_id in missing:
            orders.extend(await self.details((order_id,), allow_missing=True))
        result = []
        acts_limited = False
        supply_rows = [(order, supply) for order in orders for supply in order.supplies]
        supply_rows.sort(
            key=lambda pair: (
                (pair[1].state or pair[0].state) in closed_states,
                old.acceptance_checked_at if (old := known.get(pair[1].supply_id)) else "",
            )
        )
        for order, supply in supply_rows:
            status = supply.state or order.state
            old = known.get(supply.supply_id)
            terminal = status == FBO_STOCK.ozon_accepted or status in FBO_STOCK.ozon_returned
            changed_at = order.state_updated_date or order.created_date
            if (
                terminal
                and (not old or old.stage in TERMINAL_STAGES)
                and changed_at is not None
                and changed_at < cutoff
            ):
                continue
            if (
                terminal
                and old
                and old.stage in TERMINAL_STAGES
                and old.status == status
                and old.updated_at
                == (order.state_updated_date.isoformat() if order.state_updated_date else "")
                and (status != FBO_STOCK.ozon_accepted or not old.warning)
            ):
                route = normalize(order, supply, (), None, clusters=clusters)
                result.append(
                    old.model_copy(
                        update={
                            "warehouse": route.warehouse or old.warehouse,
                            "transit_warehouse": route.transit_warehouse or old.transit_warehouse,
                            "destination_cluster": route.destination_cluster or old.destination_cluster,
                            "destination_cluster_id": route.destination_cluster_id
                            or old.destination_cluster_id,
                        }
                    )
                )
                continue
            goods: tuple[SupplyGood, ...] = ()
            if supply.bundle_id:
                goods = await self.goods(supply.bundle_id)
            elif status in FBO_STOCK.ozon_active - {FBO_STOCK.ozon_data_filling, "OVERDUE"}:
                raise InboundSourceError("Ozon не вернул идентификатор состава поставки.")
            acts = None
            warning = ""
            if status in OZON_INBOUND.act_states:
                if acts_limited:
                    warning = "Приёмка будет обновлена после восстановления доступа к актам Ozon."
                else:
                    try:
                        response = await self._acts._response(
                            OZON_INBOUND.acts_path, SupplyActRequest(supply_id=int(supply.supply_id))
                        )
                        acts = SupplyActs.model_validate(response.json())
                    except OzonApiError as error:
                        code = f" (HTTP {error.status})" if error.status is not None else ""
                        warning = f"Фактическая приёмка Ozon пока не подтверждена{code}."
                        acts_limited = error.status in (
                            HTTPStatus.UNAUTHORIZED,
                            HTTPStatus.FORBIDDEN,
                            HTTPStatus.TOO_MANY_REQUESTS,
                        )
                    except ValueError:
                        warning = "Ozon вернул неполные данные акта приёмки."
            normalized = normalize(order, supply, goods, acts, clusters=clusters, act_warning=warning)
            if acts is not None:
                normalized = normalized.model_copy(
                    update={"acceptance_checked_at": datetime.now(UTC).isoformat()}
                )
            result.append(normalized)
        return tuple(result)
