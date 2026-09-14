(function () {
    'use strict';
    var api = '/api/unit-economics-1c/yandex-market/';
    var groups = [
        [
            'Цены и себестоимость',
            [
                ['seller_price', 'Цена продавца', '₽'],
                ['buyer_price', 'Цена покупателя без Пэй', '₽'],
                ['purchase_price', 'Закупочная цена', '₽'],
                ['fulfillment_cost', 'Фулфилмент', '₽'],
            ],
        ],
        [
            'Продажи и реклама',
            [
                ['advertising_mode', 'Реклама', '', { actual: 'Расходы за сегодня', plan: 'Плановый ДРР' }],
                ['plan_drr', 'Плановый ДРР к выкупленному обороту', '%'],
                ['buyout_percent', 'Ожидаемый выкуп', '%'],
            ],
        ],
        [
            'Услуги Маркета',
            [
                ['commission_percent', 'Комиссия размещения', '%'],
                ['payment_acceptance', 'Приём платежа', '₽'],
                ['payment_transfer_percent', 'Перевод платежа', '%'],
                ['delivery_cost', 'Успешная доставка', '₽/шт.'],
                ['return_cost', 'Расход на один невыкуп', '₽'],
                ['transit_cost', 'Транзит', '₽/шт.'],
                ['storage_per_day', 'Хранение единицы в день', '₽'],
                ['storage_days', 'Срок хранения', 'дней'],
            ],
        ],
        [
            'Налоги и собственные расходы',
            [
                ['tax_base', 'База налога', '', { buyer: 'Цена покупателя', seller: 'Цена продавца' }],
                ['tax_percent', 'Налоговая ставка', '%'],
                ['other_percent', 'Прочие к цене продавца', '%'],
                ['other_cost', 'Прочие фиксированные', '₽/шт.'],
                ['capital_percent', 'Стоимость капитала в год', '%'],
                ['turnover_days', 'Оборачиваемость', 'дней'],
                ['loss_percent', 'Потери к цене продавца', '%'],
                ['disposal_cost', 'Утилизация одного невыкупленного товара', '₽'],
            ],
        ],
        [
            'Данные для расчёта тарифов',
            [
                ['category_id', 'ID категории Маркета', ''],
                ['category_name', 'Категория', '', 'text'],
                ['length', 'Длина упаковки', 'см'],
                ['width', 'Ширина', 'см'],
                ['height', 'Высота', 'см'],
                ['weight', 'Вес', 'кг'],
                ['campaign_id', 'Кампания магазина', ''],
                [
                    'frequency',
                    'Выплаты',
                    '',
                    {
                        DAILY: 'Ежедневно',
                        WEEKLY: 'Еженедельно',
                        BIWEEKLY: 'Раз в две недели',
                        MONTHLY: 'Ежемесячно',
                    },
                ],
                [
                    'payment_delay_weeks',
                    'Отсрочка при еженедельных выплатах',
                    '',
                    { 0: 'Без отсрочки', 1: '1 неделя', 2: '2 недели', 4: '4 недели' },
                ],
            ],
        ],
    ];
    var esc = window.CheckStockUI.escapeHtml;
    function number(value) {
        return value == null ? '—' : Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 });
    }
    async function request(path, method, body) {
        var response = await fetch(api + path, {
            method: method || 'GET',
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
                'X-Requested-With': 'fetch',
            },
            body: body == null ? undefined : JSON.stringify(body),
        });
        var result = await response.json();
        if (!response.ok || !result.ok) {
            var error = result.error || result.detail || 'Не удалось выполнить запрос';
            if (Array.isArray(error))
                error = error
                    .map(function (e) {
                        return e.loc.slice(-1)[0] + ': ' + e.msg;
                    })
                    .join('; ');
            throw new Error(error);
        }
        return result;
    }

    var cabinetFields = [
        'fulfillment_cost',
        'storage_days',
        'tax_base',
        'tax_percent',
        'other_percent',
        'other_cost',
        'capital_percent',
        'turnover_days',
        'loss_percent',
        'disposal_cost',
        'transit_cost',
        'frequency',
        'payment_delay_weeks',
    ];
    var scenarioOnly = ['seller_price', 'buyer_price', 'advertising_mode', 'plan_drr'];
    window.YandexEconomicsFields = {
        groups: groups,
        cabinetFields: cabinetFields,
        scenarioOnly: scenarioOnly,
        esc: esc,
        number: number,
        request: request,
    };
})();
