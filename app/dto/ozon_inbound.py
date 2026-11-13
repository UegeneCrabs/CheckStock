from pydantic import Field, StrictBool

from app.core.constants import OZON_INBOUND
from app.dto.common import DtoModel
from app.dto.ozon_orders import Identifier, OptionalDate, OzonPayload


class SupplyStateFilter(DtoModel):
    states: tuple[str, ...]


class SupplyListRequest(DtoModel):
    filter: SupplyStateFilter
    last_id: str = ""
    limit: int = OZON_INBOUND.page_size
    sort_by: str = "ORDER_STATE_UPDATED_AT"
    sort_dir: str = "DESC"


class SupplyListPage(OzonPayload):
    order_ids: tuple[Identifier, ...]
    last_id: str | None = None


class SupplyDetailsRequest(DtoModel):
    order_ids: tuple[str, ...]


class SupplyWarehouse(OzonPayload):
    warehouse_id: Identifier = ""
    name: str | None = None
    address: str | None = None

    @property
    def display(self) -> str:
        return (self.name or self.address or self.warehouse_id).strip()


class SupplyInterval(OzonPayload):
    start: OptionalDate = Field(default=None, alias="from")


class SupplyTimeslot(OzonPayload):
    timeslot: SupplyInterval | None = None


class OzonSupply(OzonPayload):
    supply_id: Identifier
    bundle_id: Identifier = ""
    state: str | None = None
    storage_warehouse: SupplyWarehouse | None = None
    macrolocal_cluster_id: Identifier = ""
    is_crossdock: bool = False
    timeslot: SupplyTimeslot | None = None


class SupplyOrder(OzonPayload):
    order_id: Identifier
    order_number: Identifier = ""
    state: str
    drop_off_warehouse: SupplyWarehouse | None = None
    timeslot: SupplyTimeslot | None = None
    created_date: OptionalDate = None
    state_updated_date: OptionalDate = None
    supplies: tuple[OzonSupply, ...]


class SupplyDetailsPage(OzonPayload):
    orders: tuple[SupplyOrder, ...]


class SupplyBundleRequest(DtoModel):
    bundle_ids: tuple[str, ...]
    last_id: str = ""
    limit: int = OZON_INBOUND.page_size


class SupplyGood(OzonPayload):
    sku: Identifier
    offer_id: Identifier
    quantity: int = Field(ge=0, strict=True)
    barcode: Identifier = ""
    name: str | None = None


class SupplyGoodsPage(OzonPayload):
    items: tuple[SupplyGood, ...]
    has_next: StrictBool
    last_id: Identifier = ""


class SupplyActRequest(DtoModel):
    supply_id: int


class SupplyActSku(OzonPayload):
    sku: Identifier


class SupplyActItem(OzonPayload):
    sku_info: SupplyActSku
    fact_quantity: int | None = Field(default=None, ge=0, strict=True)


class SupplyAct(OzonPayload):
    type: str
    items: tuple[SupplyActItem, ...]


class SupplyActs(OzonPayload):
    supply_acts: tuple[SupplyAct, ...]
    skus_defects: tuple[object, ...] = ()


class SupplyClusterRequest(DtoModel):
    cluster_type: str = OZON_INBOUND.cluster_type


class SupplyCluster(OzonPayload):
    macrolocal_cluster_id: Identifier = ""
    name: str


class SupplyClusters(OzonPayload):
    clusters: tuple[SupplyCluster, ...]
