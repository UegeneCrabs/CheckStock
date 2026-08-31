from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True, slots=True)
class SyncJobDefinition:
    name: str
    title: str
    description: str
    schedule: str
    enabled: bool
    scope: str = "global"
    marketplaces: tuple[str, ...] = ()
    manual_run: bool = False


def _interval(seconds: int) -> str:
    if seconds % 86_400 == 0:
        days = seconds // 86_400
        return "Каждые сутки" if days == 1 else f"Каждые {days} сут."
    if seconds % 3_600 == 0:
        hours = seconds // 3_600
        return "Каждый час" if hours == 1 else f"Каждые {hours} ч."
    if seconds % 60 == 0:
        minutes = seconds // 60
        return "Каждую минуту" if minutes == 1 else f"Каждые {minutes} мин."
    return f"Каждые {seconds} сек."


def job_definitions() -> tuple[SyncJobDefinition, ...]:
    base = settings.background_sync_enabled
    funnel = base or settings.funnel_orders_sync_enabled
    prices = settings.unit_economics_1c_price_sync_enabled
    return (
        SyncJobDefinition(
            "catalog_sync",
            "Каталоги товаров",
            "Обновляет карточки и привязки товаров WB, Ozon и Яндекс Маркета.",
            f"Ежедневно в {settings.catalog_sync_hour:02d}:00",
            base,
            "store_marketplaces",
            ("WB", "OZON", "YANDEX MARKET"),
        ),
        SyncJobDefinition(
            "stock_sync",
            "Остатки маркетплейсов",
            "Загружает актуальные остатки по всем магазинам и маркетплейсам.",
            _interval(settings.auto_sync_interval_seconds),
            base,
            "store_marketplaces",
            ("WB", "OZON", "YANDEX MARKET"),
        ),
        SyncJobDefinition(
            "wb_advertising_sync",
            "Реклама WB",
            "Обновляет кампании, расходы, показы и клики Wildberries.",
            _interval(settings.wb_advertising_sync_interval_seconds),
            base,
            "stores",
        ),
        SyncJobDefinition(
            "wb_funnel_orders_sync",
            "Воронка продаж WB",
            "Загружает заказы, сумму заказов и конверсии из воронки продаж.",
            _interval(settings.wb_funnel_orders_sync_interval_seconds),
            funnel,
            "stores",
        ),
        SyncJobDefinition(
            "wb_funnel_previous_day_close_00_msk",
            "Закрытие воронки WB",
            "Повторно загружает вчерашний день после его завершения.",
            "Ежедневно в 00:00 МСК",
            funnel,
            "stores",
        ),
        SyncJobDefinition(
            "wb_funnel_weekly_metrics_sync",
            "Процент выкупа WB",
            "Обновляет недельные метрики и процент выкупа по товарам.",
            "Ежедневно в 01:00 МСК",
            funnel,
            "stores",
        ),
        SyncJobDefinition(
            "unit_economics_1c_sync",
            "Цены для юнит-экономики 1С",
            "Обновляет цены WB, используемые в расчётах юнит-экономики.",
            _interval(settings.unit_economics_1c_price_sync_interval_seconds),
            prices,
            "stores",
        ),
        SyncJobDefinition(
            "unit_economics_1c_wallet_sync",
            "Цена WB Кошелька",
            "Обновляет публичную цену товара с WB Кошельком.",
            _interval(settings.unit_economics_1c_wallet_sync_interval_seconds),
            prices,
            "stores",
        ),
        SyncJobDefinition(
            "unit_economics_1c_daily_margin_snapshot_00_msk",
            "Дневная маржа WB",
            "Фиксирует параметры и маржу на штуку за завершившийся день.",
            "Ежедневно в 00:00 МСК",
            base,
            "stores",
        ),
        SyncJobDefinition(
            "unit_economics_1c_source_sync",
            "Данные 1С",
            "Загружает закупку, фулфилмент и прочие исходные данные из таблиц 1С.",
            f"Ежедневно в {settings.unit_economics_1c_source_sync_hour:02d}:00 МСК",
            base,
        ),
        SyncJobDefinition(
            "unit_economics_1c_reference_sync",
            "Справочники юнит-экономики",
            "Обновляет категории, комиссии и справочные данные WB.",
            "Каждые сутки",
            base,
        ),
        SyncJobDefinition(
            "marketplace_stock_sync_and_history_23_msk",
            "История остатков маркетплейсов",
            "Сохраняет дневной снимок остатков на маркетплейсах.",
            "Ежедневно в 23:00 МСК",
            base,
            "store_marketplaces",
            ("WB", "OZON", "YANDEX MARKET"),
        ),
        SyncJobDefinition(
            "fulfillment_stock_history_00_msk",
            "История остатков ФФ",
            "Сохраняет дневной снимок остатков на фулфилментах.",
            "Ежедневно в 00:00 МСК",
            base,
        ),
        SyncJobDefinition(
            "stock_sheet_export",
            "Google Таблицы",
            "Проверяет расписания магазинов и выгружает остатки и заказы в Google Таблицы.",
            "Проверка каждую минуту; время задаётся для магазина",
            base,
            "stores",
        ),
        SyncJobDefinition(
            "ftp_wb_export",
            "FTP — себестоимость WB",
            "Собирает себестоимость из шести WB-листов и отправляет файл data.json на FTP.",
            (
                f"Ежедневно с {settings.ftp_export_start_hour:02d}:"
                f"{settings.ftp_export_start_minute:02d} до "
                f"{settings.ftp_export_deadline_hour:02d}:00 МСК; повтор каждые "
                f"{settings.ftp_export_retry_interval_seconds // 60} мин. при ошибке"
            ),
            base and settings.ftp_export_enabled,
            manual_run=True,
        ),
        SyncJobDefinition(
            "ftp_ozon_export",
            "FTP — себестоимость Ozon",
            "Собирает себестоимость из пяти Ozon-листов и отправляет data_ozon.json на FTP.",
            (
                f"Ежедневно с {settings.ftp_export_start_hour:02d}:"
                f"{settings.ftp_export_start_minute:02d} до "
                f"{settings.ftp_export_deadline_hour:02d}:00 МСК; повтор каждые "
                f"{settings.ftp_export_retry_interval_seconds // 60} мин. при ошибке"
            ),
            base and settings.ftp_export_enabled,
            manual_run=True,
        ),
        SyncJobDefinition(
            "wb_token_check",
            "Срок действия ключей WB",
            "Проверяет дату окончания API-ключей Wildberries.",
            _interval(settings.token_check_interval_seconds),
            base,
            "stores",
        ),
    )
