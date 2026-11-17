from collections.abc import Callable
from datetime import date, datetime, timedelta

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.spp_report import SppTurnover
from app.economics.spp_report import SppDailyPoint, _DailyPrice, _HourlySnapshot, _utc_boundary


class SppHistoryReader:
    def __init__(
        self, *, yandex: bool, turnover: Callable[[tuple[str, ...], str, str], tuple[SppTurnover, ...]]
    ) -> None:
        self.daily_table = "yandex_daily_prices" if yandex else "wb_daily_prices"
        self.hourly_table = "yandex_hourly_prices" if yandex else "wb_hourly_prices"
        self.turnover_column = "article" if yandex else "nm_id"
        self.turnover = turnover

    def _daily_prices(
        self, stores: tuple[str, ...], date_from: date, date_to: date
    ) -> tuple[_DailyPrice, ...]:
        if not stores:
            return ()
        placeholders = ",".join("?" for _ in stores)
        with db.get_connection() as connection:
            rows = connection.execute(
                f"""SELECT store_slug, article, {self.turnover_column} AS nm_id, observed_day, spp_percent
                      FROM {self.daily_table}
                     WHERE store_slug IN ({placeholders})
                       AND observed_day>=? AND observed_day<=?""",
                (*stores, date_from.isoformat(), date_to.isoformat()),
            ).fetchall()
        return tuple(
            _DailyPrice(
                store_slug=str(row["store_slug"]),
                article=str(row["article"]),
                nm_id=str(row["nm_id"]),
                day=date.fromisoformat(str(row["observed_day"])),
                spp_percent=float(row["spp_percent"]) if row["spp_percent"] is not None else None,
            )
            for row in rows
        )

    def _hourly_snapshots(self, stores: tuple[str, ...], day: date) -> tuple[_HourlySnapshot, ...]:
        if not stores:
            return ()
        placeholders = ",".join("?" for _ in stores)
        with db.get_connection() as connection:
            rows = connection.execute(
                f"""SELECT store_slug, article, observed_at, spp_percent,
                           customer_price_with_spp, orders_turnover_rub
                      FROM {self.hourly_table}
                     WHERE store_slug IN ({placeholders})
                       AND observed_at>=? AND observed_at<?
                     ORDER BY observed_at""",
                (*stores, _utc_boundary(day), _utc_boundary(day + timedelta(days=1))),
            ).fetchall()
        return tuple(
            _HourlySnapshot(
                store_slug=str(row["store_slug"]),
                article=str(row["article"]),
                observed_at=str(row["observed_at"]),
                spp_percent=float(row["spp_percent"]) if row["spp_percent"] is not None else None,
                customer_price_with_spp=float(row["customer_price_with_spp"]),
                orders_turnover_rub=(
                    float(row["orders_turnover_rub"]) if row["orders_turnover_rub"] is not None else None
                ),
            )
            for row in rows
        )

    def _open_days(self, stores: tuple[str, ...], date_from: date, date_to: date) -> tuple[date, ...]:
        if not stores:
            return ()
        placeholders = ",".join("?" for _ in stores)
        with db.get_connection() as connection:
            rows = connection.execute(
                f"""SELECT observed_at FROM {self.hourly_table}
                     WHERE store_slug IN ({placeholders}) AND observed_at>=? AND observed_at<?""",
                (*stores, _utc_boundary(date_from), _utc_boundary(date_to + timedelta(days=1))),
            ).fetchall()
        return tuple(
            sorted(
                {
                    datetime.fromisoformat(str(row["observed_at"])).astimezone(MOSCOW_TIMEZONE).date()
                    for row in rows
                }
            )
        )

    def daily_history(
        self, store: str, article: str, date_from: date, date_to: date
    ) -> tuple[SppDailyPoint, ...]:
        open_days = set(self._open_days((store,), date_from, date_to))
        with db.get_connection() as connection:
            daily = connection.execute(
                f"""SELECT observed_day, {self.turnover_column} AS nm_id, spp_percent, customer_price_with_spp FROM {self.daily_table}
                     WHERE store_slug=? AND article=? AND observed_day>=? AND observed_day<=?""",
                (store, article, date_from.isoformat(), date_to.isoformat()),
            ).fetchall()
        by_day = {date.fromisoformat(str(row["observed_day"])): row for row in daily}
        daily_turnover = {
            (row.day, row.article): float(row.net_orders_amount)
            for row in self.turnover((store,), date_from.isoformat(), date_to.isoformat())
            if row.net_orders_amount is not None
        }
        return tuple(
            SppDailyPoint(
                date=day.isoformat(),
                average_spp=(
                    float(by_day[day]["spp_percent"])
                    if day not in open_days and day in by_day and by_day[day]["spp_percent"] is not None
                    else None
                ),
                observations=(
                    1
                    if day not in open_days and day in by_day and by_day[day]["spp_percent"] is not None
                    else 0
                ),
                average_price=(
                    float(by_day[day]["customer_price_with_spp"])
                    if day not in open_days and day in by_day
                    else None
                ),
                turnover_rub=(
                    daily_turnover.get((day, str(by_day[day]["nm_id"])))
                    if day not in open_days and day in by_day
                    else None
                ),
                open=day in open_days,
            )
            for day in (
                date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)
            )
        )
