"""Standard costs from real daily WB reports, independent of the calculator."""

from collections import Counter
from datetime import date, timedelta

from app.dto.wb_standard_costs import (
    DeliveryTerm,
    LogisticsCalculation,
    ReportRow,
    Snapshot,
    SourceBundle,
    StorageDetail,
)
from app.economics.wb.standard_numbers import norm as norm
from app.economics.wb.standard_numbers import number as number
from app.economics.wb.standard_working import RowAccumulator, RowDraft
from app.wb.standard_costs_sources import (
    CommissionRate,
    FinanceEntry,
    ProductCard,
    StorageEntry,
    WarehouseTariff,
    normalize_sources,
)

# Explicit report assumption requested by the user, not an observed WB index.
DEFAULT_LOCALIZATION_INDEX = 1.0


def cost(tariff: WarehouseTariff, scheme: str, volume: float | None) -> float | None:
    base, extra = (
        (tariff.fbs_base, tariff.fbs_extra) if scheme == "FBS" else (tariff.fbw_base, tariff.fbw_extra)
    )
    if volume is None or volume <= 0 or base is None or base < 0:
        return None
    if volume <= 1:
        return base
    return base + (volume - 1) * extra if extra is not None and extra >= 0 else None


def build(raw: SourceBundle) -> Snapshot:
    start, end = date.fromisoformat(raw.date_from), date.fromisoformat(raw.date_to)
    if (end - start).days != 6:
        raise ValueError("Нужен период ровно семь дней")
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    sources = normalize_sources(raw)
    by_day = {entry.day: entry for entry in sources.tariffs}
    tariffs = {}
    for day in days:
        data = by_day[day]
        if data.currency != "RUB":
            raise ValueError("Тарифы должны быть в RUB")
        tariffs[day] = {norm(t.name): t for t in data.warehouses}
    cards = {c.article: c for c in sources.cards}
    fees = {c.subject_id: c for c in sources.commissions}
    fees_by_subject = {}
    for fee in fees.values():
        fees_by_subject.setdefault(norm(fee.subject), []).append(fee)
    rows: dict[tuple[str, str], RowAccumulator] = {}
    excluded = Counter()

    def item(article: str, scheme: str, source: FinanceEntry | StorageEntry) -> RowAccumulator:
        key = (str(article), scheme)
        if key not in rows:
            card = cards.get(str(article), ProductCard())
            volume = card.volume
            subject = card.subject or source.subject
            matches = fees_by_subject.get(norm(subject), []) if subject else []
            fee = fees.get(card.subject_id) or (matches[0] if len(matches) == 1 else CommissionRate())
            rate = (fee.fbw if scheme == "FBW" else fee.fbs) if scheme in ("FBW", "FBS") else None
            rows[key] = RowAccumulator(
                RowDraft(
                    article=str(article),
                    scheme=scheme,
                    name=card.title or source.title or source.vendor or str(article),
                    vendor=card.vendor or source.vendor or "",
                    subject=card.subject or source.subject or "",
                    volume=volume,
                    sales=0,
                    returns=0,
                    turnover=0.0,
                    commission_rate=rate,
                    commission=0.0,
                    storage=0.0,
                    stock_days=0,
                    logistics=None,
                    details=[],
                )
            )
        return rows[key]

    def tariff_for(day: str, warehouse: str | None) -> WarehouseTariff:
        name = norm(warehouse)
        name = {"склад wb": "склад wb рф", "склад поставщика - везу на склад wb": "свой склад рф"}.get(
            name, name
        )
        return tariffs[day].get(name, WarehouseTariff())

    seen = set()
    for source in sources.finance:
        if source.row_id in seen:
            raise ValueError("Повтор строки финансового отчёта")
        seen.add(source.row_id)
        day = source.day
        if day not in days:
            raise ValueError("Финансовая строка вне выбранной недели")
        operation = source.operation
        if operation not in ("Продажа", "Возврат"):
            excluded[operation or "Без операции"] += 1
            continue
        office = norm(source.warehouse)
        scheme = (
            "FBS"
            if office == "склад поставщика - везу на склад wb"
            else "FBW"
            if office in ("склад wb", "склад wb рф") or office in tariffs[day]
            else "Не определена"
        )
        acc = item(source.article, scheme, source)
        row = acc.row
        quantity, price = source.quantity, source.price
        if quantity is None or quantity < 0 or price is None or source.currency != "RUB":
            acc.missing.add("Нет корректной рублёвой базы комиссии")
            acc.bad_commission = True
            continue
        sign = -1 if operation == "Возврат" else 1
        if sign < 0:
            row.returns += quantity
        else:
            row.sales += quantity
        amount = sign * price * quantity
        row.turnover += amount
        rate = row.commission_rate
        if rate is None or not 0 <= rate <= 100:
            acc.bad_commission = True
            acc.missing.add("Нет стандартной комиссии для предмета / схемы")
        else:
            row.commission += amount * rate / 100
        if sign > 0:
            row.sales_turnover = row.sales_turnover + price * quantity
            tariff = tariff_for(days[-1], source.warehouse)
            delivery = cost(tariff, scheme, row.volume) if scheme in ("FBS", "FBW") else None
            acc.sales_terms.append(
                DeliveryTerm(
                    warehouse=source.warehouse,
                    tariff=tariff.name,
                    volume=row.volume,
                    quantity=quantity,
                    base=tariff.fbs_base if scheme == "FBS" else tariff.fbw_base,
                    extra=tariff.fbs_extra if scheme == "FBS" else tariff.fbw_extra,
                    rate=delivery,
                )
            )
            acc.sales_delivery_weight += quantity
            if delivery is None:
                acc.bad_sales_delivery = True
            else:
                acc.sales_delivery_sum += delivery * quantity

    storage_dates = set()
    for source in sources.storage:
        day = source.day
        if day not in days:
            raise ValueError("Строка хранения вне выбранной недели")
        storage_dates.add(day)
        if "скидка" in norm(source.calc_type):
            continue
        quantity = source.quantity
        acc = item(source.article, "FBW", source)
        row = acc.row
        if quantity == 0:
            if source.amount not in (0, None) or source.pallets not in (0, None):
                acc.bad_storage = True
                acc.missing.add("Начисление хранения без количества товара")
            continue
        volume = source.volume
        tariff = tariff_for(days[-1], source.warehouse)
        # Actual base charge and the individual discount are separate report rows.
        # Use the pre-discount amount, not today's un-fixed stock storage tariff.
        amount = source.amount
        storage = (
            amount / quantity if amount is not None and amount >= 0 and quantity and quantity > 0 else None
        )
        if source.discount not in (0, None):
            storage = None
        delivery = cost(tariff, "FBW", volume)
        supported = norm(source.calc_type) in (
            "короба: товары свыше базы",
            "короба: товары ниже базы",
            "паллеты: основные",
        )
        if quantity is None or quantity < 0 or not supported:
            storage = delivery = None
            acc.missing.add("Не поддержан тип хранения или отсутствует количество")
        qty = max(quantity or 0, 0)
        acc.stock_terms.append(
            DeliveryTerm(
                warehouse=source.warehouse,
                tariff=tariff.name,
                volume=volume,
                quantity=qty,
                base=tariff.fbw_base,
                extra=tariff.fbw_extra,
                rate=delivery,
            )
        )
        row.stock_days += qty
        acc.delivery_weight += qty
        if storage is None:
            acc.bad_storage = True
            acc.missing.add("Нет подтверждённой суммы хранения до скидок")
        else:
            row.storage += storage * qty
        if delivery is None:
            acc.bad_delivery = True
        else:
            acc.delivery_sum += delivery * qty
        row.details.append(
            StorageDetail(
                day=day,
                warehouse=source.warehouse,
                quantity=qty,
                volume=volume,
                storage_rate=storage,
                storage=storage * qty if storage is not None else None,
                delivery_rate=delivery,
                tariff=tariff.name,
            )
        )

    result = []
    for acc in rows.values():
        row = acc.row
        terms = acc.stock_terms if acc.delivery_weight else acc.sales_terms
        grouped = {}
        for term in terms:
            key = (term.warehouse, term.tariff, term.volume, term.base, term.extra, term.rate)
            if key not in grouped:
                grouped[key] = term.model_copy(update={"quantity": 0})
            grouped[key] = grouped[key].model_copy(update={"quantity": grouped[key].quantity + term.quantity})
        row.logistics_calculation = LogisticsCalculation(
            localization_index=DEFAULT_LOCALIZATION_INDEX,
            localization_index_source="default",
            terms=list(grouped.values()),
            weight=acc.delivery_weight or acc.sales_delivery_weight,
            weighted_sum=acc.delivery_sum if acc.delivery_weight else acc.sales_delivery_sum,
            basis="stock" if acc.delivery_weight else "sales",
            tariff_date=days[-1],
        )
        row.details.sort(key=lambda d: (d.day, d.warehouse or "", d.volume or 0))
        if acc.delivery_weight:
            row.logistics_basis = "Тариф на " + days[-1] + "; веса и объёмы — по остаткам недели"
            if not acc.bad_delivery:
                row.logistics = acc.delivery_sum / acc.delivery_weight
        else:
            row.logistics_basis = "Тариф на " + days[-1] + "; по складам продаж, объём текущей карточки"
            if acc.sales_delivery_weight and not acc.bad_sales_delivery:
                row.logistics = acc.sales_delivery_sum / acc.sales_delivery_weight
        if row.logistics is not None:
            row.logistics *= DEFAULT_LOCALIZATION_INDEX
        if row.logistics is None:
            acc.missing.add("Нет полной базы для тарифа доставки")
        if acc.bad_storage:
            row.storage = None
        if row.scheme == "FBW" and not row.details:
            row.storage = None
            acc.missing.add("В отчёте WB нет базовых строк хранения товара за неделю")
        if acc.bad_commission:
            row.commission = None
        if row.scheme == "Не определена":
            acc.missing.add("Схема продажи не определена")
        row.unit_price = row.sales_turnover / row.sales if row.sales > 0 else None
        row.commission_unit = (
            row.unit_price * row.commission_rate / 100
            if row.unit_price is not None and row.commission_rate is not None and not acc.bad_commission
            else None
        )
        row.storage_unit_day = (
            row.storage / row.stock_days
            if row.storage is not None and row.stock_days > 0
            else 0.0
            if row.scheme == "FBS"
            else None
        )
        if row.commission_unit is None:
            acc.missing.add("Нет цены продажи для комиссии на штуку")
        if row.storage_unit_day is None:
            acc.missing.add("Нет данных хранения на штуку в день")
        row.missing = tuple(sorted(acc.missing))
        row.complete = not row.missing
        result.append(ReportRow.model_validate(row.model_dump(exclude_unset=True)))
    return Snapshot(
        version=5,
        date_from=raw.date_from,
        date_to=raw.date_to,
        updated_at=raw.updated_at,
        commission_date=raw.commission_date,
        rows=sorted(result, key=lambda r: (-r.sales, r.article, r.scheme)),
        finance_rows=len(raw.finance),
        storage_rows=len(raw.storage),
        storage_dates=sorted(storage_dates),
        excluded_operations=dict(excluded),
        sources=raw.sources,
    )
