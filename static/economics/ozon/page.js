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

    function product(value) {
        return { ...value,
            data_errors: (value.data_issues || []).map(issue => {
                if (issue.code === 'source_field_missing') return 'В данных 1С Ozon не заполнены: ' + (fields[issue.field] || issue.field);
                if (issue.code === 'source_error') return (issue.source ? (sources[issue.source] || issue.source) + ': ' : '') + (issue.detail || 'Ошибка источника');
                return messages[issue.code] || 'Данные недоступны';
            }),
            stock: { ...value.stock, inbound_message: value.stock.inbound_state === 'awaiting_acceptance'
                ? 'Поставка Ozon ещё не принята на склад FBO.' : 'Нет полного свежего снимка поставок Ozon.' },
        };
    }

    window.CheckStockPage.register(url => url.pathname === '/sales/unit-economics-1c/ozon', (context, url) => {
        const page = {
            title: 'CheckStock — Юнит-экономика 1С — Ozon', section: 'unit_economics_1c',
            contentClass: 'content--unit-1c', alertMarketplace: 'OZON',
        };
        if (!context.economics_preview) return { ...page, values: {}, template: '<section class="panel"><h1>Раздел Ozon в разработке</h1></section>' };
        const periodQuery = new URLSearchParams();
        url.searchParams.forEach((value, key) => {
            if (['period_days', 'date_from', 'date_to'].includes(key)) periodQuery.append(key, value);
        });
        const periodDays = Number(periodQuery.get('period_days'));
        const productsEndpoint = '/api/unit-economics-1c/ozon';
        const config = {
            userKey: String(context.user.id), storageNamespace: 'checkstock.unit-economics-ozon', marketplaceLabel: 'Ozon',
            placeholderMode: true, ozonPreview: true, periodSelection: true, canEdit: false,
            periodDays: [7, 14, 30].includes(periodDays) ? periodDays : 7,
            periodMode: (periodQuery.get('date_from') || '').trim() || (periodQuery.get('date_to') || '').trim() ? 'custom' : 'preset',
            stores: context.stores.filter(store => store.marketplaces.includes('OZON')),
            products: [], productsLoaded: false, productsEndpoint,
            initialProductsUrl: productsEndpoint + (periodQuery.size ? '?' + periodQuery.toString() : ''),
        };
        return { ...page, values: {
            unit_1c_config: JSON.stringify(config).replace(/</g, '\\u003c'), marketplace_name: 'Ozon',
            marketplace_label: 'Ozon', calendar_link: '', loading_description: 'Загружаем товары, отзывы и остатки из базы данных.', unit_1c_notice: '',
        } };
    });
    return { product };
})();
