import json
import logging
import math
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

MARKETPLACE = "WB"
MAX_SELLER_DISCOUNT = 99
PRICE_UPLOAD_STATUS_ATTEMPTS = 60
PRICE_UPLOAD_STATUS_PAUSE_SECONDS = 2.0
PRICE_SYNC_AFTER_UPLOAD_DELAY_SECONDS = 10.0
PRICE_UPLOAD_SUCCESS_STATUSES = {3, 5}
PRICE_UPLOAD_FAILURE_STATUSES = {4, 6}
WALLET_SALE_BAN = 1 << 20
POSTPAID_BOOKING = 1 << 29


class PriceChangeError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _today() -> date:
    return datetime.now().date()


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return round(parsed, 2)


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _nm_id(article: object) -> str:
    return str(article or "").partition(" / ")[0].strip()


def _tech_size(article: object) -> str:
    return str(article or "").partition(" / ")[2].strip()


def _friendly_error(error: Exception) -> str:
    if isinstance(error, wb_api.WBApiError):
        return error.friendly
    return f"{type(error).__name__}: {error}"


def calculate_wallet_price(
    customer_price_with_spp: object,
    discount_percent: object,
    sale_conditions: object = 0,
) -> float | None:
    """Mirror WB's public-wallet discount rounding for RUB prices."""

    try:
        price = Decimal(str(customer_price_with_spp))
        percent = Decimal(str(discount_percent))
        conditions = int(sale_conditions or 0)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if (
        not price.is_finite()
        or price <= 0
        or not percent.is_finite()
        or percent <= 0
        or percent >= 100
        or conditions & WALLET_SALE_BAN
        or conditions & POSTPAID_BOOKING
    ):
        return None
    discount = (price * percent / Decimal("100")).to_integral_value(rounding=ROUND_CEILING)
    wallet_price = price - discount
    if wallet_price <= 0:
        return None
    return float(wallet_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _wallet_discount_report() -> dict:
    try:
        percent = wb_api.get_default_wallet_discount_percent()
    except Exception as error:
        message = _friendly_error(error)
        logger.warning("Скидка WB Кошелька не загружена: %s", message)
        return {"ok": False, "percent": None, "error": message}
    return {"ok": True, "percent": percent, "error": None}


def _storefront_price_rows(products: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen_articles: set[str] = set()
    for product in products:
        nm_id = str(product.get("id") or product.get("nmID") or product.get("nmId") or "").strip()
        if not nm_id:
            continue
        sizes = product.get("sizes")
        price_sources = sizes if isinstance(sizes, list) and sizes else []
        multiple_sizes = len(price_sources) > 1
        for source in price_sources:
            if not isinstance(source, dict):
                continue
            price = source.get("price")
            if not isinstance(price, dict):
                continue
            product_kopecks = _number(price.get("product"))
            logistics_kopecks = _number(price.get("logistics")) or 0
            if product_kopecks is None or product_kopecks <= 0:
                continue
            customer_price = round((product_kopecks + logistics_kopecks) / 100, 2)
            tech_size = str(source.get("origName") or source.get("name") or "").strip()
            size_id = _integer(source.get("optionId"))
            article = f"{nm_id} / {tech_size}" if multiple_sizes and tech_size else nm_id
            if article in seen_articles:
                suffix = tech_size or str(size_id or len(result) + 1)
                article = f"{nm_id} / {suffix}"
            seen_articles.add(article)
            result.append(
                {
                    "article": article,
                    "nm_id": nm_id,
                    "size_id": size_id,
                    "tech_size_name": tech_size or None,
                    "currency": "RUB",
                    "customer_price_with_spp": customer_price,
                    "sale_conditions": _integer(source.get("saleConditions")) or 0,
                }
            )
    return result


def _storefront_reputation_rows(products: list[dict]) -> list[dict]:
    result: list[dict] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        nm_id = str(product.get("id") or product.get("nmID") or product.get("nmId") or "").strip()
        if not nm_id:
            continue
        rating = _number(product.get("reviewRating"))
        reviews_count = _integer(product.get("feedbacks"))
        if rating is None and reviews_count is None:
            continue
        result.append(
            {
                "nm_id": nm_id,
                "rating": rating,
                "reviews_count": reviews_count,
            }
        )
    return result


def _resolve_customer_price_with_spp(
    storefront_price: object,
    retail_price: object,
    reliable_price: dict | None,
) -> tuple[float | None, bool]:
    """Protect the buyer price when cards/v4 returns the seller price instead."""

    storefront = _number(storefront_price)
    retail = _number(retail_price)
    if storefront is None or retail is None or retail <= 0:
        return storefront, False

    same_price_tolerance = max(1.0, retail * 0.005)
    if abs(storefront - retail) > same_price_tolerance:
        return storefront, False

    reference_retail = _number((reliable_price or {}).get("retail_price"))
    reference_customer = _number((reliable_price or {}).get("customer_price_with_spp"))
    if (
        reference_retail is None
        or reference_retail <= 0
        or reference_customer is None
        or reference_customer <= 0
        or reference_customer > reference_retail * 0.995
    ):
        return storefront, False

    factor = Decimal(str(reference_customer)) / Decimal(str(reference_retail))
    estimated = (Decimal(str(retail)) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(estimated), True


def _seller_price_rows(goods: list[dict]) -> list[dict]:
    result: list[dict] = []
    for product in goods:
        if not isinstance(product, dict):
            continue
        nm_id = str(product.get("nmID") or product.get("nmId") or "").strip()
        if not nm_id:
            continue
        sizes = product.get("sizes")
        price_sources = sizes if isinstance(sizes, list) and sizes else []
        multiple_sizes = len(price_sources) > 1
        for source in price_sources:
            if not isinstance(source, dict):
                continue
            seller_base_price = _number(source.get("price"))
            retail_price = _number(source.get("discountedPrice"))
            if retail_price is None or retail_price <= 0:
                continue
            tech_size = str(source.get("techSizeName") or "").strip()
            article = f"{nm_id} / {tech_size}" if multiple_sizes and tech_size else nm_id
            result.append(
                {
                    "article": article,
                    "nm_id": nm_id,
                    "size_id": _integer(source.get("sizeID")),
                    "tech_size_name": tech_size or None,
                    "vendor_code": str(product.get("vendorCode") or "").strip() or None,
                    "seller_base_price": seller_base_price,
                    "retail_price": retail_price,
                    "club_discounted_price": _number(source.get("clubDiscountedPrice")),
                }
            )
    return result


def calculate_seller_discount(base_price: object, target_price: object) -> tuple[int, float]:
    try:
        base = Decimal(str(base_price))
        target = Decimal(str(target_price))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PriceChangeError("Цены должны быть числами") from error
    if not base.is_finite() or base <= 0:
        raise PriceChangeError("Базовая цена WB должна быть больше нуля")
    if not target.is_finite() or target <= 0:
        raise PriceChangeError("Цена без СПП должна быть больше нуля")
    if target > base:
        raise PriceChangeError(f"Цена без СПП {target} ₽ выше базовой цены WB {base} ₽")
    raw_discount = (Decimal("1") - target / base) * Decimal("100")
    discount = int(raw_discount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if discount > MAX_SELLER_DISCOUNT:
        raise PriceChangeError(f"Для цены {target} ₽ нужна скидка более {MAX_SELLER_DISCOUNT}%")
    discount = max(discount, 0)
    calculated = (base * (Decimal("100") - discount) / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return discount, float(calculated)


def _base_price_for_product(product: dict) -> Decimal:
    if product.get("editableSizePrice") is True:
        raise PriceChangeError("Для товара включены отдельные цены по размерам")
    sizes = product.get("sizes")
    if not isinstance(sizes, list) or not sizes:
        raise PriceChangeError("WB не вернул базовую цену товара")
    prices: set[Decimal] = set()
    for size in sizes:
        if not isinstance(size, dict) or size.get("price") is None:
            continue
        try:
            value = Decimal(str(size["price"]))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if value.is_finite() and value > 0:
            prices.add(value)
    if not prices:
        raise PriceChangeError("WB не вернул базовую цену товара")
    if len(prices) != 1:
        raise PriceChangeError("У размеров товара разные базовые цены")
    base = prices.pop()
    if base != base.to_integral_value():
        raise PriceChangeError(f"Базовая цена WB {base} ₽ не является целым числом")
    return base


def _retail_price_for_product(product: dict) -> Decimal:
    sizes = product.get("sizes")
    if not isinstance(sizes, list) or not sizes:
        raise PriceChangeError("WB не вернул текущую цену без СПП")
    prices: set[Decimal] = set()
    for size in sizes:
        if not isinstance(size, dict) or size.get("discountedPrice") is None:
            continue
        try:
            value = Decimal(str(size["discountedPrice"]))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if value.is_finite() and value > 0:
            prices.add(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    if not prices:
        raise PriceChangeError("WB не вернул текущую цену без СПП")
    if len(prices) != 1:
        raise PriceChangeError("У размеров товара разные цены без СПП")
    return prices.pop()


def _current_discount_for_product(
    product: dict,
    base_price: Decimal,
    retail_price: Decimal,
) -> int:
    discount = _integer(product.get("discount"))
    if discount is None:
        raw = (Decimal("1") - retail_price / base_price) * Decimal("100")
        discount = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return min(max(discount, 0), MAX_SELLER_DISCOUNT)


def _price_change_score(
    base_price: int,
    discount: int,
    current_base_price: int,
    current_discount: int,
) -> float:
    base_change = math.log(max(base_price, 1) / max(current_base_price, 1))
    discount_change = (discount - current_discount) / 50
    return base_change * base_change + discount_change * discount_change


def _round_ruble(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _price_factors(snapshot: dict) -> tuple[Decimal, Decimal | None]:
    retail = _number(snapshot.get("retail_price"))
    spp = _number(snapshot.get("customer_price_with_spp"))
    wallet = _number(snapshot.get("customer_price_with_wallet"))
    if retail is None or retail <= 0 or spp is None or spp <= 0:
        raise PriceChangeError("В БД нет актуального соотношения цены без СПП и цены с СПП")
    spp_factor = Decimal(str(spp)) / Decimal(str(retail))
    wallet_factor = Decimal(str(wallet)) / Decimal(str(spp)) if wallet is not None and wallet > 0 else None
    return spp_factor, wallet_factor


def _project_customer_prices(
    retail_price: Decimal,
    spp_factor: Decimal,
    wallet_discount_percent: float | None,
    wallet_factor: Decimal | None,
) -> tuple[int, int | None]:
    spp_price = _round_ruble(retail_price * spp_factor)
    wallet_price = (
        calculate_wallet_price(spp_price, wallet_discount_percent)
        if wallet_discount_percent is not None and wallet_factor is not None
        else None
    )
    if wallet_price is None and wallet_factor is not None:
        wallet_price = float(_round_ruble(Decimal(spp_price) * wallet_factor))
    return spp_price, int(wallet_price) if wallet_price is not None else None


def _plan_price_change(
    product: dict,
    snapshot: dict,
    change: dict,
    wallet_discount_percent: float | None,
) -> dict:
    base_price = _base_price_for_product(product)
    retail_price = _retail_price_for_product(product)
    current_base = int(base_price)
    current_discount = _current_discount_for_product(product, base_price, retail_price)
    spp_factor, wallet_factor = _price_factors(snapshot)
    target_kind = str(change.get("target_kind") or "retail").strip().lower()
    if target_kind not in {"retail", "spp", "wallet"}:
        raise PriceChangeError("Неизвестный тип целевой цены")
    try:
        requested_target = Decimal(str(change.get("target_price")))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PriceChangeError("Целевая цена должна быть числом") from error
    if not requested_target.is_finite() or requested_target <= 0:
        raise PriceChangeError("Целевая цена должна быть больше нуля")
    retail_target_has_kopecks = (
        target_kind == "retail" and requested_target != requested_target.to_integral_value()
    )
    target = requested_target.quantize(
        Decimal("0.01") if retail_target_has_kopecks else Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    if target_kind == "wallet" and wallet_factor is None:
        raise PriceChangeError("Для товара сейчас недоступна цена с WB Кошельком")

    desired_spp = target
    if target_kind == "wallet":
        if wallet_discount_percent is not None:
            desired_spp = target / (Decimal("1") - Decimal(str(wallet_discount_percent)) / Decimal("100"))
        else:
            desired_spp = target / max(wallet_factor or Decimal("1"), Decimal("0.0001"))
    desired_retail = target if target_kind == "retail" else desired_spp / max(spp_factor, Decimal("0.0001"))

    candidates: list[dict] = []
    for discount in range(MAX_SELLER_DISCOUNT + 1):
        seller_factor = Decimal(100 - discount) / Decimal("100")
        ideal_base = desired_retail / seller_factor
        center = int(ideal_base.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        base_candidates = set(range(max(1, center - 24), center + 25))
        base_candidates.add(current_base)
        for candidate_base in base_candidates:
            if candidate_base > 1_000_000_000:
                continue
            candidate_retail = (Decimal(candidate_base) * seller_factor).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            candidate_spp, candidate_wallet = _project_customer_prices(
                candidate_retail,
                spp_factor,
                wallet_discount_percent,
                wallet_factor,
            )
            achieved = (
                candidate_retail
                if target_kind == "retail" and retail_target_has_kopecks
                else Decimal(
                    {
                        "retail": _round_ruble(candidate_retail),
                        "spp": candidate_spp,
                        "wallet": candidate_wallet,
                    }[target_kind]
                )
            )
            if achieved is None:
                continue
            error = abs(achieved - target)
            candidates.append(
                {
                    "base_price": candidate_base,
                    "discount": discount,
                    "retail_price": candidate_retail,
                    "spp_price": candidate_spp,
                    "wallet_price": candidate_wallet,
                    "achieved": achieved,
                    "error": error,
                    "score": _price_change_score(
                        candidate_base,
                        discount,
                        current_base,
                        current_discount,
                    ),
                }
            )
    if not candidates:
        raise PriceChangeError("Не удалось подобрать параметры цены WB")
    best = min(
        candidates,
        key=lambda item: (
            item["error"] != 0,
            item["error"],
            item["score"],
            abs(item["discount"] - current_discount),
            abs(item["base_price"] - current_base),
        ),
    )
    if best["error"] != 0:
        raise PriceChangeError(f"Не удалось получить на витрине ровно {target} ₽")
    previous_spp = _number(snapshot.get("customer_price_with_spp"))
    previous_wallet = _number(snapshot.get("customer_price_with_wallet"))
    return {
        "base_price": best["base_price"],
        "discount": best["discount"],
        "calculated_price": float(best["retail_price"]),
        "display_retail_price": _round_ruble(best["retail_price"]),
        "predicted_spp_price": best["spp_price"],
        "predicted_wallet_price": best["wallet_price"],
        "previous_base_price": current_base,
        "previous_discount": current_discount,
        "previous_retail_price": float(retail_price),
        "previous_spp_price": previous_spp,
        "previous_wallet_price": previous_wallet,
        "target_kind": target_kind,
        "target_price": (float(target) if retail_target_has_kopecks else _round_ruble(target)),
        "achieved_target_price": (
            float(best["achieved"]) if retail_target_has_kopecks else int(best["achieved"])
        ),
        "quarantine_risk": best["retail_price"] * Decimal("3") <= retail_price,
    }


def _prepare_price_changes(store_slug: str, changes: list[dict]) -> dict:
    if not changes:
        return {"ok": True, "payload": [], "accepted": [], "errors": []}
    token = wb_tokens.get_token(store_slug)
    requested_nm_ids = [
        int(nm_id) for change in changes if (nm_id := _nm_id(change.get("article"))).isdigit()
    ]
    goods = wb_api.get_goods_prices_by_nm_ids(token, requested_nm_ids)
    by_nm_id = {
        str(product.get("nmID") or product.get("nmId") or "").strip(): product
        for product in goods
        if isinstance(product, dict)
    }
    snapshots = {
        str(row["article"]): row for row in db.get_unit_economics_1c_latest_daily_prices((store_slug,))
    }
    try:
        wallet_discount_percent = wb_api.get_default_wallet_discount_percent()
    except Exception:
        wallet_discount_percent = None

    payload: list[dict] = []
    accepted: list[dict] = []
    errors: list[dict] = []
    seen_nm_ids: set[str] = set()
    for change in changes:
        article = str(change.get("article") or "").strip()
        product_id = f"{store_slug}:{article}"
        nm_id = _nm_id(article)
        try:
            if not nm_id.isdigit():
                raise PriceChangeError(f"Некорректный артикул WB: {article}")
            if nm_id in seen_nm_ids:
                raise PriceChangeError("Один артикул WB передан несколько раз")
            seen_nm_ids.add(nm_id)
            product = by_nm_id.get(nm_id)
            if product is None:
                raise PriceChangeError("Товар не найден в актуальных ценах WB")
            snapshot = snapshots.get(article)
            if snapshot is None:
                raise PriceChangeError("В БД нет актуальных цен товара")
            plan = _plan_price_change(
                product,
                snapshot,
                change,
                wallet_discount_percent,
            )
        except PriceChangeError as error:
            errors.append({"product_id": product_id, "article": article, "error": str(error)})
            continue
        payload.append({"nmID": int(nm_id), "price": plan["base_price"], "discount": plan["discount"]})
        accepted.append(
            {
                "product_id": product_id,
                "article": article,
                "nm_id": nm_id,
                **plan,
            }
        )
    return {
        "ok": not errors,
        "payload": payload,
        "accepted": accepted,
        "errors": errors,
    }


def preview_price_changes(store_slug: str, changes: list[dict]) -> dict:
    prepared = _prepare_price_changes(store_slug, changes)
    return {
        "ok": prepared["ok"],
        "planned": len(prepared["payload"]),
        "accepted": prepared["accepted"],
        "errors": prepared["errors"],
    }


def submit_price_changes(store_slug: str, changes: list[dict]) -> dict:
    if not changes:
        return {"ok": True, "sent": 0, "accepted": [], "errors": []}
    token = wb_tokens.get_token(store_slug)
    prepared = _prepare_price_changes(store_slug, changes)
    payload = prepared["payload"]
    upload = wb_api.upload_goods_prices_and_discounts(token, payload) if payload else None
    return {
        "ok": prepared["ok"],
        "sent": len(payload),
        "upload_id": upload.get("id") if upload else None,
        "already_exists": bool(upload and upload.get("alreadyExists")),
        "accepted": prepared["accepted"],
        "errors": prepared["errors"],
    }


def wait_for_price_upload(
    store_slug: str,
    upload_id: int,
    *,
    attempts: int = PRICE_UPLOAD_STATUS_ATTEMPTS,
    pause_seconds: float = PRICE_UPLOAD_STATUS_PAUSE_SECONDS,
) -> dict:
    token = wb_tokens.get_token(store_slug)
    last_status: dict | None = None
    for attempt in range(max(1, attempts)):
        try:
            last_status = wb_api.get_price_upload_status(token, upload_id)
        except wb_api.WBApiError as error:
            if error.status not in {400, 404}:
                raise
        status = _integer((last_status or {}).get("status"))
        if status in PRICE_UPLOAD_SUCCESS_STATUSES | PRICE_UPLOAD_FAILURE_STATUSES:
            return last_status or {"uploadID": upload_id, "status": status}
        if attempt + 1 < max(1, attempts):
            time.sleep(max(0.0, pause_seconds))
    raise PriceChangeError(
        f"WB не подтвердил обработку загрузки {upload_id} за "
        f"{max(1, attempts) * max(0.0, pause_seconds):.0f} сек."
    )


def finalize_price_change_report(store_slug: str, report: dict) -> dict:
    upload_id = _integer(report.get("upload_id"))
    if not report.get("sent") or upload_id is None:
        report["price_data_refreshed"] = False
        return report

    upload_status = wait_for_price_upload(store_slug, upload_id)
    status = _integer(upload_status.get("status"))
    report["upload_status"] = upload_status
    if status == 4:
        canceled = list(report.get("accepted") or [])
        report["accepted"] = []
        report.setdefault("errors", []).extend(
            {
                "product_id": item["product_id"],
                "article": item["article"],
                "error": "WB отменил загрузку цен",
            }
            for item in canceled
        )
        report["ok"] = False
        report["price_data_refreshed"] = False
        return report

    if status in {5, 6}:
        details = wb_api.get_price_upload_details(wb_tokens.get_token(store_slug), upload_id)
        failed_by_nm_id = {
            str(item.get("nmID") or item.get("nmId") or "").strip(): str(
                item.get("errorText") or "WB не применил цену"
            )
            for item in details
            if str(item.get("errorText") or "").strip()
        }
        accepted = list(report.get("accepted") or [])
        if status == 6 and not failed_by_nm_id:
            failed_by_nm_id = {str(item.get("nm_id") or ""): "WB не применил цену" for item in accepted}
        report["accepted"] = [
            item for item in accepted if str(item.get("nm_id") or "") not in failed_by_nm_id
        ]
        report.setdefault("errors", []).extend(
            {
                "product_id": item["product_id"],
                "article": item["article"],
                "error": failed_by_nm_id[str(item.get("nm_id") or "")],
            }
            for item in accepted
            if str(item.get("nm_id") or "") in failed_by_nm_id
        )

    time.sleep(PRICE_SYNC_AFTER_UPLOAD_DELAY_SECONDS)
    sync_report = sync_store(store_slug)
    report["sync"] = sync_report
    report["price_data_refreshed"] = bool(sync_report.get("ok") or int(sync_report.get("rows") or 0) > 0)
    report["ok"] = not report.get("errors") and report["price_data_refreshed"]
    return report


def _order_retail_price_rows(store_slug: str, snapshot_day: date) -> list[dict]:
    """Return the latest seller price from WB orders as a token-scope fallback."""
    rows = db.get_unit_economics_1c_wb_order_price_rows(
        store_slug,
        (snapshot_day - timedelta(days=90)).isoformat(),
    )
    latest: dict[str, dict] = {}
    for row in rows:
        try:
            raw = json.loads(str(row.get("raw_json") or "{}"))
        except (TypeError, ValueError):
            continue
        if not isinstance(raw, dict) or raw.get("isCancel"):
            continue
        nm_id = str(raw.get("nmId") or raw.get("nmID") or "").strip()
        retail_price = _number(raw.get("priceWithDisc"))
        if not nm_id or retail_price is None or retail_price <= 0:
            continue
        latest[nm_id] = {
            "article": nm_id,
            "nm_id": nm_id,
            "size_id": None,
            "tech_size_name": None,
            "vendor_code": str(raw.get("supplierArticle") or "").strip() or None,
            "retail_price": retail_price,
        }
    return list(latest.values())


def _match_article(article: str, candidates: list[dict]) -> dict | None:
    tech_size = _tech_size(article).casefold()
    matched = next(
        (
            candidate
            for candidate in candidates
            if tech_size and str(candidate.get("tech_size_name") or "").strip().casefold() == tech_size
        ),
        None,
    )
    if matched is None and len(candidates) == 1:
        matched = candidates[0]
    if matched is None and candidates and not tech_size:
        matched = candidates[0]
    return matched


def _targets(store_slug: str, storefront_rows: list[dict], seller_rows: list[dict]) -> list[dict]:
    catalog = db.get_catalog_items(store_slug, MARKETPLACE)
    storefront_by_nm: dict[str, list[dict]] = defaultdict(list)
    for row in storefront_rows:
        storefront_by_nm[row["nm_id"]].append(row)
    seller_by_nm: dict[str, list[dict]] = defaultdict(list)
    for row in seller_rows:
        seller_by_nm[row["nm_id"]].append(row)

    targets: list[dict] = []
    for item in catalog:
        article = str(item.get("article") or "").strip()
        if not article:
            continue
        nm_id = _nm_id(article)
        seller = _match_article(article, seller_by_nm.get(nm_id, []))
        storefront = _match_article(article, storefront_by_nm.get(nm_id, []))
        target = dict(seller or {})
        target.update(storefront or {})
        target.update({"article": article, "nm_id": nm_id})
        targets.append(target)
    return targets


def _seller_price_report(store_slug: str) -> dict:
    if not wb_tokens.has_token(store_slug):
        return {"ok": False, "goods": [], "error": "нет WB-токена для кабинета"}
    try:
        goods = wb_api.get_goods_prices(wb_tokens.get_token(store_slug))
    except Exception as error:
        return {"ok": False, "goods": [], "error": _friendly_error(error)}
    return {"ok": True, "goods": goods, "error": None}


def _record_state(
    store_slug: str,
    *,
    status: str,
    orders_ok: bool,
    retail_ok: bool,
    attempted_at: str,
    rows_saved: int,
    errors: list[str],
) -> None:
    error = "; ".join(errors)[:1500] or None
    db.record_unit_economics_1c_price_sync_state(
        store_slug,
        status=status,
        orders_ok=orders_ok,
        retail_ok=retail_ok,
        attempted_at=attempted_at,
        rows_saved=rows_saved,
        error=error,
    )
    db.record_sync_health(
        store_slug,
        MARKETPLACE,
        "unit_economics_1c_prices",
        status == "ok",
        error,
        attempted_at,
    )


def _catalog_nm_ids(store_slugs: tuple[str, ...]) -> tuple[str, ...]:
    nm_ids = {
        nm_id
        for store_slug in store_slugs
        for item in db.get_catalog_items(store_slug, MARKETPLACE)
        if (nm_id := _nm_id(item.get("article"))) and nm_id.isdigit()
    }
    return tuple(sorted(nm_ids, key=int))


def _sync_store(
    store_slug: str,
    snapshot_day: date,
    storefront_report: dict,
    *,
    load_retail_prices: bool = True,
    wallet_discount_report: dict | None = None,
    record_state: bool = True,
) -> dict:
    snapshot_day = snapshot_day or _today()
    attempted_at = _now()
    errors: list[str] = []
    wallet_discount_report = wallet_discount_report or _wallet_discount_report()
    wallet_discount_percent = (
        _number(wallet_discount_report.get("percent")) if wallet_discount_report.get("ok") else None
    )
    previous_rows = db.get_unit_economics_1c_latest_daily_prices((store_slug,))
    previous = {str(row["article"]): row for row in previous_rows}
    reliable_spp_rows = db.get_unit_economics_1c_latest_reliable_spp_prices(store_slug)
    reliable_spp = {str(row["article"]): row for row in reliable_spp_rows}
    if load_retail_prices:
        logger.info("Цены 1С [%s]: загружаем цены продавца без СПП", store_slug)
        seller_report = _seller_price_report(store_slug)
    else:
        logger.info("Цены 1С [%s]: цена без СПП отключена для этого запуска", store_slug)
        seller_report = {"ok": True, "goods": [], "error": None}
    seller_rows = _seller_price_rows(list(seller_report.get("goods") or []))
    if load_retail_prices and seller_report.get("ok"):
        logger.info("Цены 1С [%s]: цен продавца получено=%s", store_slug, len(seller_rows))
    elif load_retail_prices:
        errors.append(f"цена без СПП: {seller_report.get('error') or 'не загружена'}")
    storefront_products = list(storefront_report.get("products") or [])
    storefront_rows = _storefront_price_rows(storefront_products)
    targets = _targets(store_slug, storefront_rows, seller_rows)
    order_retail_rows: list[dict] = []
    if load_retail_prices:
        missing_without_spp = [
            target
            for target in targets
            if _number(target.get("retail_price")) is None
            and _number((previous.get(str(target.get("article") or "")) or {}).get("retail_price")) is None
        ]
        if missing_without_spp:
            order_retail_rows = _order_retail_price_rows(store_slug, snapshot_day)
            targets = _targets(
                store_slug,
                storefront_rows,
                [*seller_rows, *order_retail_rows],
            )
    catalog_nm_ids = {str(target["nm_id"]) for target in targets}
    reputation_rows = [
        row for row in _storefront_reputation_rows(storefront_products) if row["nm_id"] in catalog_nm_ids
    ]
    reputation_rows_saved = db.upsert_unit_economics_1c_product_reputation(
        store_slug,
        reputation_rows,
        attempted_at,
    )
    returned_products = {
        str(product.get("id") or product.get("nmID") or product.get("nmId") or ""): product
        for product in storefront_products
        if isinstance(product, dict)
    }
    returned_nm_ids = catalog_nm_ids & set(returned_products)
    priced_nm_ids = catalog_nm_ids & {str(row["nm_id"]) for row in storefront_rows}
    omitted_nm_ids = catalog_nm_ids - returned_nm_ids
    returned_without_price_nm_ids = returned_nm_ids - priced_nm_ids
    out_of_stock_nm_ids = {
        nm_id
        for nm_id in returned_without_price_nm_ids
        if _integer(returned_products[nm_id].get("totalQuantity")) == 0
    }
    retail_missing_targets = (
        [
            target
            for target in targets
            if _number(target.get("retail_price")) is None
            and _number((previous.get(str(target.get("article") or "")) or {}).get("retail_price")) is None
        ]
        if load_retail_prices
        else []
    )
    missing_targets = [target for target in targets if target.get("customer_price_with_spp") is None]
    missing_nm_ids = {str(target["nm_id"]) for target in missing_targets}
    failed_nm_ids = {str(value) for value in storefront_report.get("failed_nm_ids") or []}
    affected_nm_ids = missing_nm_ids | (failed_nm_ids & {str(target["nm_id"]) for target in targets})
    storefront_ok = not affected_nm_ids
    retail_ok = not load_retail_prices or not retail_missing_targets
    logger.info(
        "Цены 1С [%s]: товаров в каталоге=%s, WB вернул=%s, с ценой=%s, "
        "не вернул=%s, без цены=%s (без остатка=%s), без актуальной цены=%s",
        store_slug,
        len(catalog_nm_ids),
        len(returned_nm_ids),
        len(priced_nm_ids),
        len(omitted_nm_ids),
        len(returned_without_price_nm_ids),
        len(out_of_stock_nm_ids),
        len(missing_targets),
    )
    if affected_nm_ids:
        errors.append(
            f"витрина WB: нет актуальной цены для {len(affected_nm_ids)} товаров "
            f"(не возвращены: {len(omitted_nm_ids)}, без цены: "
            f"{len(returned_without_price_nm_ids)}, из них без остатка: "
            f"{len(out_of_stock_nm_ids)})"
        )
    if load_retail_prices and retail_missing_targets and seller_report.get("ok"):
        errors.append(f"цена без СПП: нет значения для {len(retail_missing_targets)} товаров")

    orders_ok = storefront_ok
    if missing_targets:
        logger.info(
            "Цены 1С [%s]: витрина не дала цену с СПП для %s товаров; цена по заказам не используется",
            store_slug,
            len(missing_targets),
        )

    snapshots: list[dict] = []
    storefront_rows_saved = 0
    wallet_rows_saved = 0
    estimated_spp_rows = 0
    unresolved_rows = 0
    for target in targets:
        article = str(target.get("article") or "").strip()
        nm_id = str(target.get("nm_id") or _nm_id(article)).strip()
        if not article or not nm_id:
            continue
        existing = previous.get(article) or {}
        retail_price = _number(target.get("retail_price")) or _number(existing.get("retail_price"))
        storefront_price, spp_estimated = _resolve_customer_price_with_spp(
            target.get("customer_price_with_spp"),
            retail_price,
            reliable_spp.get(article),
        )
        if storefront_price is None:
            unresolved_rows += 1
            continue
        if spp_estimated:
            estimated_spp_rows += 1
        storefront_rows_saved += 1
        if wallet_discount_report.get("ok"):
            wallet_price = calculate_wallet_price(
                storefront_price,
                wallet_discount_percent,
                target.get("sale_conditions"),
            )
        else:
            wallet_price = _number(existing.get("customer_price_with_wallet"))
        if wallet_price is not None:
            wallet_rows_saved += 1
        snapshots.append(
            {
                "store_slug": store_slug,
                "article": article,
                "day": snapshot_day.isoformat(),
                "marketplace": MARKETPLACE,
                "nm_id": nm_id,
                "size_id": target.get("size_id") or existing.get("size_id"),
                "tech_size_name": target.get("tech_size_name") or existing.get("tech_size_name"),
                "vendor_code": target.get("vendor_code") or existing.get("vendor_code"),
                "currency": "RUB",
                "seller_base_price": _number(target.get("seller_base_price"))
                or _number(existing.get("seller_base_price")),
                "retail_price": retail_price,
                "club_discounted_price": _number(target.get("club_discounted_price"))
                or _number(existing.get("club_discounted_price")),
                "customer_price_with_spp": storefront_price,
                "customer_price_with_wallet": wallet_price,
                "customer_price_window_days": None,
                "customer_price_orders_count": 0,
                "last_order_at": None,
                "orders_synced_at": existing.get("orders_synced_at"),
                "retail_synced_at": (
                    attempted_at
                    if load_retail_prices and _number(target.get("retail_price")) is not None
                    else existing.get("retail_synced_at")
                ),
                "updated_at": attempted_at,
            }
        )

    rows_saved = db.upsert_unit_economics_1c_daily_prices(snapshots)
    if unresolved_rows or not retail_ok:
        status = "partial" if rows_saved else "error"
    elif load_retail_prices and not seller_report.get("ok"):
        status = "fallback"
    else:
        status = "ok"
    if record_state:
        _record_state(
            store_slug,
            status=status,
            orders_ok=orders_ok,
            retail_ok=retail_ok,
            attempted_at=attempted_at,
            rows_saved=rows_saved,
            errors=errors,
        )
    report = {
        "ok": status in {"ok", "fallback"},
        "status": status,
        "rows": rows_saved,
        "orders_ok": orders_ok,
        "retail_ok": retail_ok,
        "storefront_ok": storefront_ok,
        "retail_price_requested": load_retail_prices,
        "storefront_rows": storefront_rows_saved,
        "reputation_rows": reputation_rows_saved,
        "wallet_rows": wallet_rows_saved,
        "wallet_discount_percent": wallet_discount_percent,
        "wallet_discount_ok": bool(wallet_discount_report.get("ok")),
        "estimated_spp_rows": estimated_spp_rows,
        "catalog_products": len(catalog_nm_ids),
        "storefront_returned_products": len(returned_nm_ids),
        "storefront_priced_products": len(priced_nm_ids),
        "storefront_omitted_products": len(omitted_nm_ids),
        "storefront_without_price_products": len(returned_without_price_nm_ids),
        "storefront_out_of_stock_products": len(out_of_stock_nm_ids),
        "retail_rows": len(seller_rows),
        "retail_fallback_rows": len(order_retail_rows),
        "retail_missing_rows": len(retail_missing_targets),
        "unresolved_rows": unresolved_rows,
    }
    if errors:
        report["error"] = "; ".join(errors)
    if wallet_discount_report.get("error"):
        report["wallet_error"] = wallet_discount_report["error"]
    logger.info(
        "Цены 1С [%s]: завершено, статус=%s, сохранено=%s, витрина=%s, СПП восстановлено=%s, не обновлено=%s",
        store_slug,
        status,
        rows_saved,
        storefront_rows_saved,
        estimated_spp_rows,
        unresolved_rows,
    )
    return report


def sync_store(
    store_slug: str,
    snapshot_day: date | None = None,
    *,
    load_retail_prices: bool = True,
    storefront_batch_size: int | None = None,
    record_state: bool = True,
) -> dict:
    snapshot_day = snapshot_day or _today()
    nm_ids = _catalog_nm_ids((store_slug,))
    wallet_discount_report = _wallet_discount_report()
    storefront_report = wb_api.get_storefront_products(nm_ids, batch_size=storefront_batch_size)
    return _sync_store(
        store_slug,
        snapshot_day,
        storefront_report,
        load_retail_prices=load_retail_prices,
        wallet_discount_report=wallet_discount_report,
        record_state=record_state,
    )


def sync_stores(
    store_slugs: tuple[str, ...],
    snapshot_day: date | None = None,
    *,
    load_retail_prices: bool = True,
    storefront_batch_size: int | None = None,
    record_state: bool = True,
) -> dict[str, dict]:
    snapshot_day = snapshot_day or _today()
    report: dict[str, dict] = {}
    nm_ids = _catalog_nm_ids(store_slugs)
    wallet_discount_report = _wallet_discount_report()
    logger.info(
        "Цены 1С: начинаем синхронизацию, кабинетов=%s, уникальных WB-артикулов=%s",
        len(store_slugs),
        len(nm_ids),
    )
    try:
        storefront_report = wb_api.get_storefront_products(
            nm_ids,
            batch_size=storefront_batch_size,
        )
    except Exception as error:
        message = _friendly_error(error)
        logger.exception("Витринные цены WB не загружены: %s", message)
        storefront_report = {
            "products": [],
            "failed_nm_ids": sorted(nm_ids),
            "errors": [message],
        }
    for store_slug in store_slugs:
        try:
            logger.info("Цены 1С [%s]: обрабатываем кабинет", store_slug)
            report[store_slug] = _sync_store(
                store_slug,
                snapshot_day,
                storefront_report,
                load_retail_prices=load_retail_prices,
                wallet_discount_report=wallet_discount_report,
                record_state=record_state,
            )
        except Exception as error:
            attempted_at = _now()
            message = _friendly_error(error)
            logger.exception("Цены юнитки 1С %s не обновлены: %s", store_slug, message)
            if record_state:
                _record_state(
                    store_slug,
                    status="error",
                    orders_ok=False,
                    retail_ok=False,
                    attempted_at=attempted_at,
                    rows_saved=0,
                    errors=[message],
                )
            report[store_slug] = {"ok": False, "status": "error", "rows": 0, "error": message}
    return report


def sync_all() -> dict[str, dict]:
    return sync_stores(tuple(STORES))
