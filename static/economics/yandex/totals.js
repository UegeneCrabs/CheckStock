(function () {
    'use strict';
    function known(value) { return value != null && value !== '' && Number.isFinite(Number(value)); }
    window.CheckStockYandexTotals = function (products) {
        var result = {0: {title: 'Количество товаров'}};
        function metric(index, rows, value, unit, title, partial) {
            result[index] = {
                value: rows.length ? value : null, unit: unit, title: title,
                partial: rows.length < products.length || !!partial,
            };
        }
        function sum(index, get, unit, title, coverage) {
            var rows = products.filter(function (p) { return known(get(p)); });
            metric(index, rows, rows.reduce(function (s, p) { return s + Number(get(p)); }, 0), unit, title,
                coverage && rows.some(function (p) { return !coverage(p) || !coverage(p).complete; }));
        }
        function ratio(index, rows, top, bottom, factor, unit, title, partial) {
            var numerator = 0, denominator = 0;
            rows.forEach(function (p) { numerator += top(p); denominator += bottom(p); });
            metric(index, rows, denominator > 0 ? numerator / denominator * factor : null, unit, title, partial);
        }
        var current = products.filter(function (p) {
            var c = p.current_economics || {}, v = (p.ym_economics || {}).values || {};
            return known(c.margin) && known(c.orders) && known(c.buyout_percent) && known(v.purchase_price);
        });
        function bought(p) { var c = p.current_economics; return c.orders * c.buyout_percent / 100; }
        function currentProfit(p) { return p.current_economics.margin * bought(p); }
        ratio(2, current, currentProfit, bought, 1, 'money', 'Маржа на штуку: прибыль / ожидаемые выкупы за сегодня.');
        ratio(3, current, currentProfit, function (p) { return p.ym_economics.values.purchase_price * bought(p); },
            100, 'percent', 'ROI за сегодня: прибыль / закупочная стоимость ожидаемых выкупов.');
        sum(4, function (p) { return p.economics_7d.turnover; }, 'money', 'Сумма ТО после отмен.', function (p) { return p.economics_7d.turnover_coverage; });
        sum(5, function (p) { return p.economics_7d.margin; }, 'money', 'Сумма сохранённой прибыли за выбранный период.', function (p) { return p.economics_7d.margin_coverage; });
        var period = products.filter(function (p) {
            return known(p.economics_7d.margin) && known(p.economics_7d.purchase_value);
        });
        ratio(6, period, function (p) { return p.economics_7d.margin; }, function (p) { return p.economics_7d.purchase_value; },
            100, 'percent', 'ROI: суммарная прибыль / закупочная стоимость по тем же сохранённым дням.',
            period.some(function (p) { return !p.economics_7d.complete; }));
        var ads = products.filter(function (p) {
            var a = p.advertising;
            return known(a.drr_spend) && known(a.orders_amount) && known(a.buyout_percent) && a.buyout_percent > 0;
        });
        ratio(7, ads, function (p) { return p.advertising.drr_spend; }, function (p) {
            return p.advertising.orders_amount * p.advertising.buyout_percent / 100;
        }, 100, 'percent', 'ДРР: расходы / оборот с учётом выкупа. Для каждого товара берутся совпадающие дни заказов и рекламы.',
            ads.some(function (p) { return !p.advertising.drr_coverage.complete; }));
        // Preserve the same no-turnover convention as individual WB/YM rows.
        if (ads.length && result[7].value == null) result[7].value = ads.some(function (p) { return p.advertising.drr_spend > 0; }) ? 100 : 0;
        sum(8, function (p) { return p.advertising.spend; }, 'money', 'Общие расходы на рекламу.', function (p) { return p.advertising.coverage; });
        var clicks = products.filter(function (p) { return known(p.advertising.clicks) && known(p.advertising.impressions) && known(p.advertising.spend); });
        var adsPartial = clicks.some(function (p) { return !p.advertising.coverage.complete; });
        ratio(9, clicks, function (p) { return p.advertising.clicks; }, function (p) { return p.advertising.impressions; }, 100, 'percent', 'CTR: всего кликов / всего показов.', adsPartial);
        ratio(10, clicks, function (p) { return p.advertising.spend; }, function (p) { return p.advertising.clicks; }, 1, 'money', 'CPC: расходы / клики.', adsPartial);
        [[11, 'goal_week'], [12, 'goal_day'], [16, 'fact'], [17, 'plan']].forEach(function (pair) {
            sum(pair[0], function (p) { return (p.tag_data || {})[pair[1]]; }, '', 'Сумма значений.');
        });
        [[18, 'total'], [19, 'fbs'], [20, 'fbo'], [21, 'fulfillment'], [25, 'inbound']].forEach(function (pair) {
            sum(pair[0], function (p) { return p.stock[pair[1]]; }, '', pair[1] === 'total'
                ? 'Доступные остатки FBS + FBO + ФФ. Поставки в пути показаны отдельно.' : 'Сумма остатков.',
                pair[1] === 'inbound' ? function (p) { return {complete: !p.stock.inbound_partial}; } : null);
        });
        result[25].title = 'Количество в утверждённых заявках и отправленных поставках. При неполных данных показана известная часть со знаком ≥.';
        result[25].lowerBound = result[25].partial && result[25].value != null;
        var stock = products.filter(function (p) { return known(p.stock.total) && known(p.stock.orders_21d); });
        ratio(22, stock, function (p) { return p.stock.total; }, function (p) { return p.stock.orders_21d / 21; }, 1, '', 'Запас в днях: общие остатки / среднесуточные заказы за 21 день.');
        return result;
    };
})();
