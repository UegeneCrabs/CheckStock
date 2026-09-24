from app.repositories.yandex_financial import _sum_values
from app.yandex.financial_reports import (
    _cost_for_items,
    _daily_order_sheets,
    _is_financial_return,
    _pnl_values,
)


def test_pidata_transfer_to_account_formula() -> None:
    """The 8–14 August PIData example: 267,485 - expenses = 79,167."""

    values = _pnl_values(
        {
            "payment_transfer": [{"orderId": 1, "merchantPrice": 267_485, "servicePrice": 4_279.78}],
            "placement": [
                {"orderId": 1, "amountWithoutBonuses": 146_542.75},
                # An unrelated placement operation is not a redeemed good.
                {"orderId": 2, "amountWithoutBonuses": 3_327.50},
            ],
            "delivery": [{"servicePrice": 13_374.25}],
            "crossregional_delivery": [{"servicePrice": 7_507}],
            "boost": [{"postpaid": 16_354.27, "servicePrice": 97.64}],
            "loyalty_and_reviews": [{"customerBonusAmount": 250, "servicePrice": 250}],
            "payment_accepting": [{"servicePrice": 9.84}],
        },
        {"orders_and_offers_transactions": []},
    )

    assert values["commission"] == 146_542.75
    assert values["logistics"] == 20_881.25
    assert values["other_direct_expenses"] == 4_289.62
    assert values["advertising"] == 16_354.27
    assert values["bank_receipt"] == 79_167.11


def test_buyout_turnover_subtracts_returned_goods() -> None:
    values = _pnl_values(
        {"payment_transfer": [{"merchantPrice": 1_000}]},
        {
            "orders_and_offers_transactions": [
                {"offerStatus": "Доставлен покупателю", "buyerPaymentAmount": 900}
            ],
            "returns": [
                {
                    "partnerPriceForDelivery": 300,
                    "refundBuyerPaymentAmount": -270,
                }
            ],
        },
    )

    assert values["buyout_seller_turnover"] == 700
    assert values["buyout_buyer_turnover"] == 630


def test_realisation_report_is_authoritative_for_completed_returns() -> None:
    values = _pnl_values(
        {"payment_transfer": [{"merchantPrice": 1_000}]},
        {
            "orders_and_offers_transactions": [],
            "returns": [{"partnerPriceForDelivery": 300}],
            "services_and_orders_margin": [
                {
                    "orderStatus": "Полный возврат принят на складе",
                    "sumBillingPriceOfItems": 250,
                    "buyerPayment": 200,
                }
            ],
        },
    )

    assert values["buyout_seller_turnover"] == 750
    assert values["returned_count"] == 1


def test_return_is_posted_to_the_return_event_day() -> None:
    order_sheets = {
        "orders_and_offers_transactions": [
            {
                "orderId": 1,
                "offerStatus": "Возврат принят на складе",
                "deliveryDate": "2026-09-07 13:00:00",
                "statusChanged": "2026-09-15 08:00:00",
                "partnerPriceForDelivery": 1_000,
            }
        ]
    }

    delivery_day, _ = _daily_order_sheets(order_sheets, "2026-09-07")
    status_day, _ = _daily_order_sheets(order_sheets, "2026-09-15")

    assert delivery_day["returns"] == []
    assert status_day["returns"] == order_sheets["orders_and_offers_transactions"]


def test_only_financial_cancellations_are_counted_as_returns() -> None:
    assert _is_financial_return({"offerStatus": "Возврат оформлен"})
    assert _is_financial_return(
        {
            "offerStatus": "Отменён",
            "deliveryDate": "2026-09-20",
            "refundBuyerPaymentAmount": -500,
        }
    )
    assert _is_financial_return(
        {
            "offerStatus": "Невыкуп отправлен",
            "deliveryDate": "2026-09-20",
            "refundBuyerPaymentAmount": -500,
        }
    )
    assert not _is_financial_return(
        {"offerStatus": "Отменён", "refundBuyerPaymentAmount": -500}
    )
    assert not _is_financial_return({"offerStatus": "Невыкуп принят на складе"})
    assert _is_financial_return({"offerStatus": "Возврат отправлен"})


def test_unredeemed_payment_transfer_is_not_buyout_turnover() -> None:
    values = _pnl_values(
        {
            "payment_transfer": [
                {"orderId": 1, "merchantPrice": 10_000, "servicePrice": 160},
                {"orderId": 2, "merchantPrice": 7_968, "servicePrice": 127.49},
            ]
        },
        {
            "orders_and_offers_transactions": [
                {"orderId": 1, "offerStatus": "Доставлен покупателю"},
                {"orderId": 2, "offerStatus": "Невыкуп готов к передаче вам"},
            ]
        },
    )

    assert values["buyout_seller_turnover"] == 10_000
    assert values["other_direct_expenses"] == 287.49


def test_other_combines_acquiring_and_marketplace_other_credit() -> None:
    values = _pnl_values(
        {
            "payment_transfer": [{"servicePrice": 8_800.36}],
            "payment_accepting": [{"servicePrice": 15.84}],
            "placement": [{"lateOrderExecutionFeeTariff": 425}],
        },
        {"orders_and_offers_transactions": []},
    )

    assert values["other_direct_expenses"] == 9_241.20


def test_payment_forecast_includes_all_pidata_marketing_and_processing_services() -> None:
    values = _pnl_values(
        {
            "payment_transfer": [{"orderId": 1, "merchantPrice": 533_287, "servicePrice": 8_800.36}],
            "payment_accepting": [{"servicePrice": 15.84}],
            "placement": [{"orderId": 1, "amountWithoutBonuses": 264_391.02}],
            "delivery": [{"servicePrice": 27_102.95}],
            "crossregional_delivery": [{"servicePrice": 2_012.84}],
            "boost": [{"postpaid": 73_013.12}],
            "cpm-boost": [{"payment": 9_328.93}],
            "product-banners": [{"payment": 0.46, "servicePrice": 0.46}],
            "loyalty_and_reviews": [{"customerBonusAmount": 200}],
            "order_processing": [{"servicePrice": 15}],
            "order_processing_on_warehouse": [{"servicePrice": 210}],
        },
        {"orders_and_offers_transactions": [{"orderId": 1}]},
    )

    assert values["advertising"] == 82_342.51
    assert values["bank_receipt"] == 148_196.48


def test_payment_forecast_includes_all_pidata_other_marketplace_services() -> None:
    """Storage and manager services belong to PiData's ``Другие`` bucket."""

    values = _pnl_values(
        {
            "payment_transfer": [{"orderId": 1, "merchantPrice": 500_000}],
            "placement": [{"orderId": 1, "amountWithoutBonuses": 100_000}],
            "loyalty_and_reviews": [{"customerBonusAmount": 2_000}],
            "paid_storage_after_01-06-22": [{"paidStorage": 30_000}],
            "personal_manager": [{"servicePrice": 5_000}],
            "reception_of_surplus": [{"servicePrice": 300}],
            "order_processing": [{"servicePrice": 200}],
            "storage_of_returns": [{"servicePrice": 100}],
        },
        {"orders_and_offers_transactions": [{"orderId": 1}]},
    )

    assert values["other"] == 0
    assert values["bank_receipt"] == 362_400


def test_personal_manager_is_charged_for_each_configured_campaign() -> None:
    values = _pnl_values(
        {
            "payment_transfer": [{"merchantPrice": 100_000}],
            "personal_manager": [{"servicePrice": 1_000}],
        },
        {"orders_and_offers_transactions": []},
        campaign_count=3,
    )

    assert values["other"] == 0
    assert values["bank_receipt"] == 97_000


def test_product_banners_are_allocated_to_each_configured_campaign() -> None:
    values = _pnl_values(
        {
            "payment_transfer": [{"merchantPrice": 100_000}],
            "product-banners": [{"servicePrice": 100}],
        },
        {"orders_and_offers_transactions": []},
        campaign_count=3,
    )

    assert values["advertising"] == 300
    assert values["bank_receipt"] == 99_700


def test_cost_price_uses_the_same_delivered_quantities_as_buyouts() -> None:
    prices = {"sold": 100.0, "returned": 250.0}

    delivered_cost = _cost_for_items(
        [{"article": "sold", "quantity": 2}, {"article": "returned", "quantity": 1}], prices
    )
    returned_cost = _cost_for_items([{"shopSku": "returned", "count": 1}], prices)

    assert delivered_cost == 450
    assert returned_cost == 250
    assert delivered_cost - returned_cost == 200


def test_cost_price_is_unavailable_when_a_buyout_has_no_purchase_price() -> None:
    assert _cost_for_items([{"article": "unknown", "quantity": 1}], {}) is None


def test_range_cost_is_unavailable_when_one_day_has_an_unknown_purchase_price() -> None:
    values = _sum_values(
        [
            {"values_json": '{"sales": 1000, "cost_of_goods": 400}'},
            {"values_json": '{"sales": 2000, "cost_of_goods": null}'},
        ]
    )

    assert values["sales"] == 3000
    assert values["cost_of_goods"] is None


def test_buyer_turnover_uses_the_same_payment_transfer_orders_as_seller_turnover() -> None:
    values = _pnl_values(
        {
            "payment_transfer": [
                {"orderId": 1, "merchantPrice": 10_000},
                {"orderId": 2, "merchantPrice": 7_968},
            ]
        },
        {
            "orders_and_offers_transactions": [
                {"orderId": 1, "offerStatus": "Доставлен покупателю", "buyerPaymentAmount": 4_000},
                {"orderId": 2, "offerStatus": "Невыкуп готов к передаче вам", "buyerPaymentAmount": 3_000},
                {"orderId": 3, "offerStatus": "Доставлен покупателю", "buyerPaymentAmount": 2_000},
            ]
        },
    )

    assert values["buyout_seller_turnover"] == 10_000
    assert values["buyout_buyer_turnover"] == 4_000


def test_all_buyout_metrics_use_the_same_order_sku_basket() -> None:
    """A multi-SKU order must not drop a buyer price or a redeemed quantity."""

    values = _pnl_values(
        {
            "payment_transfer": [
                {"orderId": 1, "shopSku": "first", "merchantPrice": 1_000},
                {"orderId": 1, "shopSku": "second", "merchantPrice": 2_000},
            ]
        },
        {
            "orders_and_offers_transactions": [
                {
                    "orderId": 1,
                    "shopSku": "first",
                    "offerStatus": "Доставлен покупателю",
                    "buyerPaymentAmount": 600,
                },
                {
                    "orderId": 1,
                    "shopSku": "second",
                    "offerStatus": "Доставлен покупателю",
                    "buyerPaymentAmount": 1_200,
                },
            ],
            "returns": [
                {
                    "orderId": 1,
                    "shopSku": "second",
                    "count": 1,
                    "partnerPriceForDelivery": 2_000,
                    "refundBuyerPaymentAmount": 1_200,
                }
            ],
        },
    )

    assert values["buyout_count"] == 1
    assert values["buyout_seller_turnover"] == 1_000
    assert values["buyout_buyer_turnover"] == 600


def test_logistics_nets_delivery_service_reversals() -> None:
    values = _pnl_values(
        {
            "delivery": [
                {"servicePrice": 398.4},
                {"servicePrice": -398.4},
            ],
            "crossregional_delivery": [{"servicePrice": 1.64}, {"servicePrice": -1.64}],
        },
        {},
    )

    assert values["logistics"] == 0
