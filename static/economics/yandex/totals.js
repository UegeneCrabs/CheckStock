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
        function buyout(p) {
            var values = (p.ym_economics || {}).values || {};
            return known(values.buyout_percent) ? Number(values.buyout_percent) : p.advertising.buyout_percent;
        }
        var adRows = products.filter(function (p) { return known(p.advertising.spend); });
        var turnoverRows = products.filter(function (p) {
            return known(p.economics_7d.turnover) &&
                (Number(p.economics_7d.turnover) === 0 || known(buyout(p)));
        });
        var totalSpend = adRows.reduce(function (s, p) { return s + Number(p.advertising.spend); }, 0);
        var boughtTurnover = turnoverRows.reduce(function (s, p) {
            return s + (Number(p.economics_7d.turnover) === 0 ? 0 : Number(p.economics_7d.turnover) * Number(buyout(p)) / 100);
        }, 0);
        metric(7, products, adRows.length && boughtTurnover > 0 ? totalSpend / boughtTurnover * 100 : null,
            'percent', 'ДРР: все рекламные расходы / Σ(ТО товара × процент выкупа товара / 100) × 100%. Расходы и ТО — за выбранный период. При нулевом знаменателе ДРР не определён.',
            adRows.length < products.length || turnoverRows.length < products.length ||
            adRows.some(function (p) { return !p.advertising.coverage || !p.advertising.coverage.complete; }) ||
            turnoverRows.some(function (p) { return !p.economics_7d.turnover_coverage || !p.economics_7d.turnover_coverage.complete; }));
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
