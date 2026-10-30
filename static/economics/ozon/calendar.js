window.OzonCalendar = (() => {
    'use strict';
    const fields = [
        ['seller_price', 'Цена с учётом акции', '₽'],
        ['purchase', 'Закупка', '₽/шт.'],
        ['fulfillment', 'ФФ', '₽/шт.'],
        ['commission_amount', 'Комиссия Ozon FBO', '₽/шт.'],
        ['logistics_amount', 'Логистика', '₽/шт.'],
        ['storage_amount', 'Хранение', '₽/шт.'],
        ['acquiring_amount', 'Эквайринг', '₽/шт.'],
        ['team_amount', 'Комиссия компании', '₽/шт.'],
        ['unallocated_amount', 'Прочее нераспределённое', '₽/шт.'],
        ['vat_amount', 'НДС', '₽/шт.'],
        ['usn_amount', 'УСН', '₽/шт.'],
        ['buyout_percent', 'Выкуп', '%'],
        ['orders_count', 'Заказы FBO + FBS до отмен', 'шт.', false],
        ['advertising_spend', 'Расходы на рекламу', '₽/день', false],
        ['advertising_amount', 'Реклама на ожидаемый выкуп', '₽/шт.', false],
    ].map(([key, label, unit, editable = true]) => ({key, label, unit, editable}));
    const messages = {
        snapshot: 'Нет дневного снимка параметров',
        orders_partial: 'Выгрузка заказов устарела',
        advertising_partial: 'Реклама загружена не полностью или устарела',
        sources_stale: 'Часть параметров из предыдущей выгрузки',
        logistics_partial: 'Начисления логистики неполные',
        storage_partial: 'Данные хранения неполные',
        buyout_default: 'Применён выкуп по умолчанию',
        no_expected_buyouts: 'Нет ожидаемых выкупов: маржа на штуку и ROI не определены. Известные расходы на рекламу учтены в прибыли дня.',
    };
    const errors = {
        forbidden: 'Нет доступа к магазину.',
        not_found: 'Снимок за день отсутствует или товар не найден в актуальном каталоге.',
        settings_conflict: 'Данные изменились. Откройте ячейку заново и проверьте влияние.',
        invalid_target: 'Проверьте значение. Выкуп — от 0 до 100%, отрицательные суммы допустимы для логистики и хранения.',
        invalid_period: 'Будущие даты недоступны.',
    };
    async function page(context, page) {
        const response = await window.CheckStockUI.request('/api/unit-economics-1c/ozon/calendar/config');
        const origins = Object.fromEntries(fields.map(field => [field.key, 'Дневной снимок параметров Ozon: ' + field.label]));
        origins.orders_count = 'Сохранённые заказы FBO + FBS выбранного дня до отмен';
        origins.advertising_spend = 'Сохранённые дневные источники рекламы Ozon';
        origins.advertising_amount = 'Расходы дня / (заказы дня × выкуп / 100)';
        const config = {
            endpoint: '/api/unit-economics-1c/ozon/calendar', stores: response.stores,
            today: response.today, marketplace: 'OZON', fields, origins, messages, errors,
            numericValues: true, actors: {ozon_margin_daily_sync: 'Автоматическое обновление Ozon'},
            computed: [
                {key: 'margin', label: 'Маржа на штуку', unit: '₽/шт. · после рекламы'},
                {key: 'roi', label: 'ROI', unit: '% · после рекламы'},
                {key: 'day_profit', label: 'Прибыль за день', unit: '₽ · после рекламы'},
            ],
        };
        return {...page, title: 'CheckStock — Параметры по дням Ozon', values: {
            calendar_config: JSON.stringify(config).replace(/</g, '\\u003c'),
            economics_url: '/sales/unit-economics-1c/ozon',
        }};
    }
    return {page};
})();
