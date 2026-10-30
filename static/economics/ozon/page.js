window.OzonEconomicsUI = (() => {
    'use strict';
    const messages = {
        stock_missing: 'Не загружены остатки товара Ozon', stock_partial: 'Остатки Ozon FBS/FBO загружены не полностью',
        reputation_missing: 'Оценки и отзывы Ozon ещё не загружены', source_missing: 'Товар не найден в листах OZON таблицы 1С',
        inbound_missing: 'Нет полного свежего снимка поставок Ozon', turnover_unavailable: 'Нет полной выгрузки оборота за выбранный период',
        orders_unavailable: 'Нет полной выгрузки заказов за выбранный период', advertising_unavailable: 'Нет выгрузки Performance API за выбранный период',
    };
    const fields = { purchase_price: 'себестоимость', fulfillment_cost: 'затраты на ФФ', team_commission_percent: 'комиссия компании', tag_raw: 'тег' };
    const sources = { reputation: 'Отзывы Ozon', source: 'Данные 1С Ozon', advertising: 'Реклама Ozon' };
    const sourceErrors = {
        performance_authentication_failed: 'Ozon отклонил рекламные ключи. Проверьте доступы Performance API этого магазина.',
        performance_permission_denied: 'У рекламного ключа нет доступа к запрошенным данным Ozon.',
        performance_daily_limit: 'Достигнут суточный лимит рекламных отчётов Ozon.',
        performance_rate_limit: 'Ozon временно ограничил частоту рекламных запросов.',
    };
    const marginLabels = {
        seller_price: 'Цена с учётом акции', purchase: 'Закупка', fulfillment: 'ФФ', commission_amount: 'Комиссия Ozon',
        logistics_amount: 'Логистика на штуку', storage_amount: 'Хранение на штуку', advertising_amount: 'Реклама на штуку',
        acquiring_amount: 'Эквайринг', team_amount: 'Комиссия компании', unallocated_amount: 'Прочее нераспределённое', vat_amount: 'НДС', usn_amount: 'УСН',
        snapshot: 'Нет дневного снимка параметров', orders_count: 'Количество заказов', buyout_percent: 'Процент выкупа',
        advertising_spend: 'Расходы на рекламу',
        orders_partial: 'Выгрузка заказов устарела', advertising_partial: 'Реклама загружена не полностью или устарела',
        sources_stale: 'Часть параметров из предыдущей выгрузки', logistics_partial: 'Начисления логистики неполные',
        storage_partial: 'Данные хранения неполные', buyout_default: 'Применён выкуп по умолчанию',
    };
    const calculationMessages = day => [
        ...(day?.missing?.includes('snapshot') ? ['snapshot'] : day?.missing || []).map(key => 'Нет данных: ' + (marginLabels[key] || key)),
        ...(day?.issues || []).map(key => marginLabels[key] || key),
    ];

    function product(value) {
        const current = value.current_economics || {};
        const period = value.economics_7d || {};
        const advertising = value.advertising || {};
        const drrBasis = advertising.drr_basis || {};
        const currentMessages = calculationMessages(current.calculation);
        const periodMessages = [...new Set((period.daily || []).flatMap(calculationMessages))];
        const coverage = (value, label) => value ? { ...value, messages: [
            label, 'Учтено ' + value.days + ' из ' + value.expected_days + ' дней.',
            ...(value.missing_dates.length ? ['Нет расчёта за: ' + value.missing_dates.map(day => day.split('-').reverse().join('.')).join(', ')] : []),
            ...periodMessages,
        ] } : null;
        const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
        const inputs = current.calculation?.inputs || {};
        const formula = Object.entries(inputs).map(([key, amount]) =>
            (marginLabels[key] || key) + ': ' + (amount == null ? '—' : numbers.format(Number(amount)) + ' ₽')).join('\n');
        if (current.expected_buyouts != null && Number(current.expected_buyouts) === 0) currentMessages.push('Нет ожидаемых выкупов: маржа на штуку не определена. Расходы на рекламу учтены в прибыли дня.');
        return { ...value,
            advertising: { ...advertising, orders_amount: drrBasis.turnover ?? null,
                drr_spend: drrBasis.spend ?? null, buyout_percent: drrBasis.buyout_percent ?? null },
            current_economics: { ...current, formula, issues: { margin: currentMessages, roi: currentMessages }, messages: currentMessages, daily_messages: currentMessages },
            economics_7d: { ...period, messages: periodMessages,
                margin_coverage: coverage(period.margin_coverage, 'Сумма прибыли по отдельным дням.'),
                roi_coverage: coverage(period.roi_coverage, 'Сумма прибыли / закупочная стоимость ожидаемых выкупов за те же дни × 100%.'),
                missing_parameters: Object.fromEntries((period.daily || []).map(day => [day.day, day.missing.map(key => key === 'purchase' ? 'purchase_price' : key)])) },
            data_errors: (value.data_issues || []).map(issue => {
                if (issue.code === 'source_field_missing') return 'В данных 1С Ozon не заполнены: ' + (fields[issue.field] || issue.field);
                if (issue.code === 'source_error') {
                    const detail = issue.detail || '';
                    const code = detail === 'performance_http_error:401' ? 'performance_authentication_failed'
                        : detail === 'performance_http_error:403' ? 'performance_permission_denied' : detail.split(':')[0];
                    return (issue.source ? (sources[issue.source] || issue.source) + ': ' : '') + (sourceErrors[code] || detail || 'Ошибка источника');
                }
                return messages[issue.code] || 'Данные недоступны';
            }),
            stock: { ...value.stock, inbound_message: value.stock.inbound_state === 'awaiting_acceptance'
                ? 'Поставка Ozon ещё не принята на склад FBO.' : 'Нет полного свежего снимка поставок Ozon.' },
        };
    }

    window.CheckStockPage.register(url => url.pathname === '/sales/unit-economics-1c/ozon', async (context, url) => {
        const page = {
            title: 'CheckStock — Юнит-экономика 1С — Ozon', section: 'unit_economics_1c',
            contentClass: 'content--unit-1c', alertMarketplace: 'OZON',
        };
        if (!context.economics_preview) return { ...page, values: {}, template: '<section class="panel"><h1>Раздел Ozon в разработке</h1></section>' };
        if (url.searchParams.get('calendar') === '1') return window.OzonCalendar.page(context, page);
        const periodQuery = new URLSearchParams();
        url.searchParams.forEach((value, key) => {
            if (['period_days', 'date_from', 'date_to'].includes(key)) periodQuery.append(key, value);
        });
        const periodDays = Number(periodQuery.get('period_days'));
        const productsEndpoint = '/api/unit-economics-1c/ozon';
        const config = {
            userKey: String(context.user.id), storageNamespace: 'checkstock.unit-economics-ozon', marketplaceLabel: 'Ozon',
            placeholderMode: true, ozonPreview: true, periodSelection: true, canEdit: false,
            canChangeOzonPrices: context.user.is_active === true,
            periodDays: [7, 14, 30].includes(periodDays) ? periodDays : 7,
            periodMode: (periodQuery.get('date_from') || '').trim() || (periodQuery.get('date_to') || '').trim() ? 'custom' : 'preset',
            stores: context.stores.filter(store => store.marketplaces.includes('OZON')),
            products: [], productsLoaded: false, productsEndpoint,
            initialProductsUrl: productsEndpoint + (periodQuery.size ? '?' + periodQuery.toString() : ''),
        };
        return { ...page, values: {
            unit_1c_config: JSON.stringify(config).replace(/</g, '\\u003c'), marketplace_name: 'Ozon',
            marketplace_label: 'Ozon', calendar_link: '<a class="ue1c-ghost-button" id="ue1c-calendar-link" href="/sales/unit-economics-1c/ozon?calendar=1">Параметры по дням</a>', loading_description: 'Загружаем товары, отзывы и остатки из базы данных.', unit_1c_notice: '',
        } };
    });
    return { product, marginLabels };
})();
