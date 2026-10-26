from pathlib import Path

from pydantic import ValidationError

from app.core.constants import OZON_LOGISTICS
from app.dto.ozon_logistics_volume import LogisticsTariffCatalog


def load_catalog() -> LogisticsTariffCatalog | None:
    path = Path(__file__).parents[1] / OZON_LOGISTICS.tariffs_relative_path
    try:
        return LogisticsTariffCatalog.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None


CATALOG = load_catalog()
