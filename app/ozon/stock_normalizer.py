from datetime import UTC

from app.dto.ozon_stock import OzonStockEvidence, OzonStockQuantities


def quantities(evidence: OzonStockEvidence) -> OzonStockQuantities:
    checked = evidence.source_checked_at
    catalog = evidence.catalog_observed_at
    confirmed = False
    if evidence.source_ok and checked is not None and catalog is not None:
        checked = checked if checked.tzinfo is not None else checked.replace(tzinfo=UTC)
        catalog = catalog if catalog.tzinfo is not None else catalog.replace(tzinfo=UTC)
        confirmed = checked >= catalog
    seller = (evidence.fbs, evidence.rfbs)
    return OzonStockQuantities(
        fbs=sum(max(value or 0, 0) for value in seller)
        if confirmed or any(value is not None for value in seller)
        else None,
        fbo=max(evidence.fbo or 0, 0) if confirmed or evidence.fbo is not None else None,
    )
