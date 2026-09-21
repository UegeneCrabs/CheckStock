(function () {
    'use strict';
    var api = '/api/unit-economics-1c/yandex-market/';
    var groups = [
        [
            'Цены и себестоимость',
            [
                ['seller_price', 'Цена без СПП', '₽'],
                ['buyer_price', 'Цена с СПП', '₽'],
                ['pay_price', 'Цена с картой Пэй', '₽'],
                ['purchase_price', 'Закупочная стоимость', '₽'],
            ],
        ],
        [
            'Продажи и реклама',
            [
                ['plan_drr', 'ДРР с выкупом', '%'],
                ['buyout_percent', 'Процент выкупа', '%'],
            ],
        ],
        [
            'Услуги Маркета',
            [
                ['commission_percent', 'Комиссия YM, %', '%'],
                ['payment_acceptance', 'Приём платежа', '₽'],
                ['payment_transfer_percent', 'Эквайринг, %', '%'],
                ['delivery_cost', 'Логистика, руб', '₽/шт.'],
                ['volume_l', 'Объём упаковки', 'л'],
                ['return_middle_mile', 'Средняя миля для невыкупа', '₽'],
                ['return_cost', 'Расход на один невыкуп', '₽'],
                ['transit_cost', 'Транзит', '₽/шт.'],
            ],
        ],
        [
            'Налоги и собственные расходы',
            [
                ['vat_percent', 'НДС, %', '%'],
                ['usn_percent', 'УСН, %', '%'],
                ['company_commission_percent', 'Комиссия компании, %', '%'],
                ['loss_percent', 'Потери к цене продавца', '%'],
                ['disposal_cost', 'Утилизация одного невыкупленного товара', '₽'],
            ],
        ],
        [
            'Данные для расчёта тарифов',
            [
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
        'vat_percent',
        'usn_percent',
        'company_commission_percent',
        'loss_percent',
        'disposal_cost',
        'transit_cost',
        'frequency',
        'payment_delay_weeks',
    ];
    var scenarioOnly = ['seller_price', 'buyer_price', 'pay_price', 'advertising_mode', 'plan_drr', 'advertising_spend'];
    window.YandexEconomicsFields = {
        groups: groups,
        expandedLabels: {
            seller_price: 'Цена без СПП, руб',
            buyer_price: 'Цена с СПП, руб',
            pay_price: 'Цена с картой Пэй, руб',
            plan_drr: 'ДРР с выкупом, % (7 дней)',
        },
        hints: {
            buyer_price: 'Цена покупателя без скидки по карте Пэй',
            plan_drr: 'ДРР с выкупом за последние 7 завершённых дней',
            delivery_cost: 'Доставка выкупленного товара. Невыкупы и транзит учитываются отдельно.',
            company_commission_percent: 'Рассчитывается от цены без СПП',
            vat_percent: 'НДС = цена с СПП × ставка НДС / (100 + ставка НДС)',
            usn_percent: 'УСН = (цена с СПП − НДС) × ставка УСН / 100',
        },
        cabinetFields: cabinetFields,
        scenarioOnly: scenarioOnly,
        derived: ['volume_l', 'return_middle_mile', 'return_cost'],
        esc: esc,
        number: number,
        request: request,
    };
})();
