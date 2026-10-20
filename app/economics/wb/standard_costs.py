"""Standard costs from real daily WB reports, independent of the calculator."""

import math
from collections import Counter
from datetime import date, timedelta

# Explicit report assumption requested by the user, not an observed WB index.
DEFAULT_LOCALIZATION_INDEX = 1.0


def number(value):
    try:
        value = float(str(value).replace(",", ".").replace(" ", ""))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def norm(value):
    return " ".join(str(value or "").casefold().split())


def cost(tariff, prefix, volume):
    base, extra = number(tariff.get(prefix + "Base")), number(tariff.get(prefix + "Liter"))
    if volume is None or volume <= 0 or base is None or base < 0:
        return None
    if volume <= 1:
        return base
    return base + (volume - 1) * extra if extra is not None and extra >= 0 else None


def build(raw):
    start, end = date.fromisoformat(raw["date_from"]), date.fromisoformat(raw["date_to"])
    if (end - start).days != 6:
        raise ValueError("Нужен период ровно семь дней")
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    tariffs = {}
    for day in days:
        data = raw["tariffs"][day]["response"]["data"]
        if data.get("currency") != "RUB":
            raise ValueError("Тарифы должны быть в RUB")
        tariffs[day] = {norm(t["warehouseName"]): t for t in data["warehouseList"]}
    cards = {str(c["nmID"]): c for c in raw["cards"]}
    fees = {str(c["subjectID"]): c for c in raw["commissions"]["report"]}
    fees_by_subject = {}
    for fee in fees.values():
        fees_by_subject.setdefault(norm(fee.get("subjectName")), []).append(fee)
    rows, excluded = {}, Counter()

    def item(article, scheme, source):
        key = (str(article), scheme)
        if key not in rows:
            card = cards.get(str(article), {})
            dimensions = card.get("dimensions") or {}
            sides = [number(dimensions.get(k)) for k in ("length", "width", "height")]
            volume = math.prod(sides) / 1000 if all(v is not None and v > 0 for v in sides) else None
            subject = card.get("subjectName") or source.get("subjectName") or source.get("subject")
            matches = fees_by_subject.get(norm(subject), []) if subject else []
            fee = fees.get(str(card.get("subjectID")), {}) or (matches[0] if len(matches) == 1 else {})
            rate = (
                number(fee.get("paidStorageKgvp" if scheme == "FBW" else "kgvpMarketplace"))
                if scheme in ("FBW", "FBS")
                else None
            )
            rows[key] = dict(
                article=str(article),
                scheme=scheme,
                name=card.get("title") or source.get("title") or source.get("vendorCode") or str(article),
                vendor=card.get("vendorCode") or source.get("vendorCode") or "",
                subject=card.get("subjectName") or source.get("subjectName") or source.get("subject") or "",
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
                _stock_terms=[],
                _sales_terms=[],
                missing=set(),
                _delivery_sum=0.0,
                _delivery_weight=0,
                _sales_delivery_sum=0.0,
                _sales_delivery_weight=0,
                _bad_delivery=False,
                _bad_sales_delivery=False,
                _bad_storage=False,
                _bad_commission=False,
            )
        return rows[key]

    def tariff_for(day, warehouse):
        name = norm(warehouse)
        name = {"склад wb": "склад wb рф", "склад поставщика - везу на склад wb": "свой склад рф"}.get(
            name, name
        )
        return tariffs[day].get(name, {})

    seen = set()
    for source in raw["finance"]:
        if source["rrdId"] in seen:
            raise ValueError("Повтор строки финансового отчёта")
        seen.add(source["rrdId"])
        day = str(source.get("rrDate", ""))[:10]
        if day not in days:
            raise ValueError("Финансовая строка вне выбранной недели")
        operation = source.get("sellerOperName")
        if operation not in ("Продажа", "Возврат"):
            excluded[operation or "Без операции"] += 1
            continue
        office = norm(source.get("officeName"))
        scheme = (
            "FBS"
            if office == "склад поставщика - везу на склад wb"
            else "FBW"
            if office in ("склад wb", "склад wb рф") or office in tariffs[day]
            else "Не определена"
        )
        row = item(source["nmId"], scheme, source)
        quantity, price = number(source.get("quantity")), number(source.get("retailPriceWithDisc"))
        if quantity is None or quantity < 0 or price is None or source.get("currency") != "RUB":
            row["missing"].add("Нет корректной рублёвой базы комиссии")
            row["_bad_commission"] = True
            continue
        sign = -1 if operation == "Возврат" else 1
        row["returns" if sign < 0 else "sales"] += quantity
        amount = sign * price * quantity
        row["turnover"] += amount
        rate = row["commission_rate"]
        if rate is None or not 0 <= rate <= 100:
            row["_bad_commission"] = True
            row["missing"].add("Нет стандартной комиссии для предмета / схемы")
        else:
            row["commission"] += amount * rate / 100
        if sign > 0:
            row["sales_turnover"] = row.get("sales_turnover", 0.0) + price * quantity
            tariff = tariff_for(days[-1], source.get("officeName"))
            prefix = "boxDeliveryMarketplace" if scheme == "FBS" else "boxDelivery"
            delivery = cost(tariff, prefix, row["volume"]) if scheme in ("FBS", "FBW") else None
            row["_sales_terms"].append(
                dict(
                    warehouse=source.get("officeName"),
                    tariff=tariff.get("warehouseName"),
                    volume=row["volume"],
                    quantity=quantity,
                    base=number(tariff.get(prefix + "Base")),
                    extra=number(tariff.get(prefix + "Liter")),
                    rate=delivery,
                )
            )
            row["_sales_delivery_weight"] += quantity
            if delivery is None:
                row["_bad_sales_delivery"] = True
            else:
                row["_sales_delivery_sum"] += delivery * quantity

    storage_dates = set()
    for source in raw["storage"]:
        day = str(source.get("date", ""))[:10]
        if day not in days:
            raise ValueError("Строка хранения вне выбранной недели")
        storage_dates.add(day)
        if "скидка" in norm(source.get("calcType")):
            continue
        quantity = number(source.get("barcodesCount"))
        row = item(source["nmId"], "FBW", source)
        if quantity == 0:
            if number(source.get("warehousePrice")) not in (0, None) or number(
                source.get("palletCount")
            ) not in (0, None):
                row["_bad_storage"] = True
                row["missing"].add("Начисление хранения без количества товара")
            continue
        volume = number(source.get("volume"))
        tariff = tariff_for(days[-1], source.get("warehouse"))
        # Actual base charge and the individual discount are separate report rows.
        # Use the pre-discount amount, not today's un-fixed stock storage tariff.
        amount = number(source.get("warehousePrice"))
        storage = (
            amount / quantity if amount is not None and amount >= 0 and quantity and quantity > 0 else None
        )
        if number(source.get("loyaltyDiscount")) not in (0, None):
            storage = None
        delivery = cost(tariff, "boxDelivery", volume)
        supported = norm(source.get("calcType")) in (
            "короба: товары свыше базы",
            "короба: товары ниже базы",
            "паллеты: основные",
        )
        if quantity is None or quantity < 0 or not supported:
            storage = delivery = None
            row["missing"].add("Не поддержан тип хранения или отсутствует количество")
        qty = max(quantity or 0, 0)
        row["_stock_terms"].append(
            dict(
                warehouse=source.get("warehouse"),
                tariff=tariff.get("warehouseName"),
                volume=volume,
                quantity=qty,
                base=number(tariff.get("boxDeliveryBase")),
                extra=number(tariff.get("boxDeliveryLiter")),
                rate=delivery,
            )
        )
        row["stock_days"] += qty
        row["_delivery_weight"] += qty
        if storage is None:
            row["_bad_storage"] = True
            row["missing"].add("Нет подтверждённой суммы хранения до скидок")
        else:
            row["storage"] += storage * qty
        if delivery is None:
            row["_bad_delivery"] = True
        else:
            row["_delivery_sum"] += delivery * qty
        row["details"].append(
            dict(
                day=day,
                warehouse=source.get("warehouse"),
                quantity=qty,
                volume=volume,
                storage_rate=storage,
                storage=storage * qty if storage is not None else None,
                delivery_rate=delivery,
                tariff=tariff.get("warehouseName"),
            )
        )

    result = []
    for row in rows.values():
        terms = row["_stock_terms"] if row["_delivery_weight"] else row["_sales_terms"]
        grouped = {}
        for term in terms:
            key = tuple(term[k] for k in ("warehouse", "tariff", "volume", "base", "extra", "rate"))
            if key not in grouped:
                grouped[key] = {**term, "quantity": 0}
            grouped[key]["quantity"] += term["quantity"]
        row["logistics_calculation"] = dict(
            localization_index=DEFAULT_LOCALIZATION_INDEX,
            localization_index_source="default",
            terms=list(grouped.values()),
            weight=row["_delivery_weight"] or row["_sales_delivery_weight"],
            weighted_sum=row["_delivery_sum"] if row["_delivery_weight"] else row["_sales_delivery_sum"],
            basis="stock" if row["_delivery_weight"] else "sales",
            tariff_date=days[-1],
        )
        row["details"].sort(key=lambda d: (d["day"], d["warehouse"] or "", d["volume"] or 0))
        if row["_delivery_weight"]:
            row["logistics_basis"] = "Тариф на " + days[-1] + "; веса и объёмы — по остаткам недели"
            if not row["_bad_delivery"]:
                row["logistics"] = row["_delivery_sum"] / row["_delivery_weight"]
        else:
            row["logistics_basis"] = "Тариф на " + days[-1] + "; по складам продаж, объём текущей карточки"
            if row["_sales_delivery_weight"] and not row["_bad_sales_delivery"]:
                row["logistics"] = row["_sales_delivery_sum"] / row["_sales_delivery_weight"]
        if row["logistics"] is not None:
            row["logistics"] *= DEFAULT_LOCALIZATION_INDEX
        if row["logistics"] is None:
            row["missing"].add("Нет полной базы для тарифа доставки")
        if row["_bad_storage"]:
            row["storage"] = None
        if row["scheme"] == "FBW" and not row["details"]:
            row["storage"] = None
            row["missing"].add("В отчёте WB нет базовых строк хранения товара за неделю")
        if row["_bad_commission"]:
            row["commission"] = None
        if row["scheme"] == "Не определена":
            row["missing"].add("Схема продажи не определена")
        row["unit_price"] = row.get("sales_turnover", 0.0) / row["sales"] if row["sales"] > 0 else None
        row["commission_unit"] = (
            row["unit_price"] * row["commission_rate"] / 100
            if row["unit_price"] is not None
            and row["commission_rate"] is not None
            and not row["_bad_commission"]
            else None
        )
        row["storage_unit_day"] = (
            row["storage"] / row["stock_days"]
            if row["storage"] is not None and row["stock_days"] > 0
            else 0.0
            if row["scheme"] == "FBS"
            else None
        )
        if row["commission_unit"] is None:
            row["missing"].add("Нет цены продажи для комиссии на штуку")
        if row["storage_unit_day"] is None:
            row["missing"].add("Нет данных хранения на штуку в день")
        row["missing"] = sorted(row["missing"])
        row["complete"] = not row["missing"]
        result.append({k: v for k, v in row.items() if not k.startswith("_")})
    return dict(
        version=5,
        date_from=raw["date_from"],
        date_to=raw["date_to"],
        updated_at=raw["updated_at"],
        commission_date=raw["commission_date"],
        rows=sorted(result, key=lambda r: (-r["sales"], r["article"], r["scheme"])),
        finance_rows=len(raw["finance"]),
        storage_rows=len(raw["storage"]),
        storage_dates=sorted(storage_dates),
        excluded_operations=dict(excluded),
        sources=raw["sources"],
    )
