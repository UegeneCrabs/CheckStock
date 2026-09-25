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
            var c = p.current_economics || {};
            return known(c.day_profit) || (known(c.margin) && known(c.orders) && known(c.buyout_percent));
        });
        function currentPurchase(p) {
            var values = (p.ym_economics || {}).values || {};
            return values.purchase_price == null ? (p.current_economics || {}).purchase_value : values.purchase_price;
        }
        function bought(p) { var c = p.current_economics; return known(c.expected_buyouts) ? Number(c.expected_buyouts) : c.orders * c.buyout_percent / 100; }
        function dayPurchase(p) {
            var c = p.current_economics;
            if (c.day_purchase_value !== undefined) return c.day_purchase_value;
            if (bought(p) === 0) return 0;
            return known(currentPurchase(p)) ? currentPurchase(p) * bought(p) : null;
        }
        function currentPartial(p) {
            var c = p.current_economics;
            return (c.daily_complete === undefined ? c.complete : c.daily_complete) === false;
        }
        function currentProfit(p) {
            var c = p.current_economics;
            if (known(c.day_profit)) return Number(c.day_profit);
            return c.orders === 0 && Number(c.advertising_spend) > 0
                ? Number(c.margin) : Number(c.margin) * bought(p);
        }
        ratio(2, current, currentProfit, bought, 1, 'money', 'Маржа на штуку: прибыль / ожидаемые выкупы за сегодня.', current.some(currentPartial));
        ratio(3, current, currentProfit, dayPurchase,
            100, 'percent', 'ROI за сегодня: прибыль / закупочная стоимость ожидаемых выкупов.', current.some(currentPartial));
        if (current.some(function (p) { return !known(dayPurchase(p)); })) result[3].value = null;
        sum(4, function (p) { return p.economics_7d.turnover; }, 'money', 'Сумма ТО после отмен.', function (p) { return p.economics_7d.turnover_coverage; });
        sum(5, function (p) { return p.economics_7d.margin; }, 'money', 'Сумма сохранённой прибыли за выбранный период.', function (p) { return p.economics_7d.margin_coverage; });
        function periodPurchase(p) { var e = p.economics_7d; return e.roi_purchase_value !== undefined ? e.roi_purchase_value : e.purchase_value; }
        var period = products.filter(function (p) { return known(p.economics_7d.margin); });
        ratio(6, period, function (p) { return p.economics_7d.margin; }, function (p) { return periodPurchase(p); },
            100, 'percent', 'ROI: суммарная прибыль / закупочная стоимость по тем же сохранённым дням.',
            period.some(function (p) { return !p.economics_7d.complete; }));
        if (period.some(function (p) { return !known(periodPurchase(p)); })) result[6].value = null;
        [2, 3, 5, 6].forEach(function (index) {
            var currentMetric = index < 4;
            var messages = [];
            products.forEach(function (p) {
                var e = currentMetric ? p.current_economics : p.economics_7d;
                (currentMetric ? e.daily_messages || e.messages || [] : e.messages || []).forEach(function (message) { if (messages.indexOf(message) < 0) messages.push(message); });
                if (currentMetric ? current.indexOf(p) < 0 : e.margin == null) messages.push('Не рассчитан товар: ' + (p.article || p.name));
            });
            result[index].messages = messages;
            result[index].title += messages.length ? '\n' + messages.join('\n') : '';
        });
        function buyout(p) {
            var values = (p.ym_economics || {}).values || {};
            return known(values.buyout_percent) ? Number(values.buyout_percent) : p.advertising.buyout_percent;
        }
        var adRows = products.filter(function (p) { return known(p.advertising.spend); });
        var drrRows = products.filter(function (p) {
            return known(p.advertising.drr_spend) && known(p.advertising.orders_amount) &&
                (Number(p.advertising.orders_amount) === 0 || known(buyout(p)));
        });
        var totalSpend = drrRows.reduce(function (s, p) { return s + Number(p.advertising.drr_spend); }, 0);
        var boughtTurnover = drrRows.reduce(function (s, p) {
            return s + (Number(p.advertising.orders_amount) === 0 ? 0 : Number(p.advertising.orders_amount) * Number(buyout(p)) / 100);
        }, 0);
        metric(7, drrRows, boughtTurnover > 0 ? totalSpend / boughtTurnover * 100 : null,
            'percent', 'ДРР: расходы на рекламу / Σ(сумма заказов × процент выкупа / 100) × 100%. Учитываются только дни, за которые есть и заказы, и реклама.',
            drrRows.some(function (p) { return !p.advertising.drr_coverage || !p.advertising.drr_coverage.complete; }));
        sum(8, function (p) { return p.advertising.spend; }, 'money', 'Общие расходы на рекламу.', function (p) { return p.advertising.coverage; });
        var clicks = products.filter(function (p) { return known(p.advertising.clicks) && known(p.advertising.impressions) && known(p.advertising.spend); });
        var adsPartial = clicks.some(function (p) { return !p.advertising.coverage || !p.advertising.coverage.complete; });
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
        ratio(22, stock, function (p) { return p.stock.total; }, function (p) {
            return Number(p.stock.average_daily_orders || 0);
        }, 1, '', 'Запас в днях: общие остатки / сумма среднесуточных заказов по доступным дням.',
        stock.some(function (p) { return !p.stock.coverage || !p.stock.coverage.complete; }));
        return result;
    };
})();
