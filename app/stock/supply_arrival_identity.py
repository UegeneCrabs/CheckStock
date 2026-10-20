import hashlib
import json

from app.dto.supply_arrivals import SupplyArrival, SupplyArrivalId


def arrival_id(source: str, row: SupplyArrival) -> SupplyArrivalId:
    project = row.store_slug or " ".join(row.project.casefold().split())
    identity = json.dumps((source, project, row.order), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()
