from app.config import settings
from app.dto.ozon_tariffs import OzonTariffCatalog

CATALOG = OzonTariffCatalog.model_validate_json(settings.ozon_tariffs_path.read_bytes())
