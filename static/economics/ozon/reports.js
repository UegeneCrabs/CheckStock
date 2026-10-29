window.OzonReportsUI = (() => {
    'use strict';
    const base = '/api/unit-economics-1c/ozon/reports';
    const pageBase = '/sales/unit-economics-1c/ozon/reports';
    const messages = {
        purchase_required: 'Нужна положительная закупочная стоимость для расчёта ROI.',
        prices_required: 'Нужны согласованные цены продавца, с СПП и с Кошельком.',
        parameters_required: 'Не хватает расходов для расчёта целевой цены.',
        target_unreachable: 'Цель недостижима в пределах 1 млрд ₽. Проверьте ДРР и расходы.',
        partial_sources: 'Часть исходных данных за неделю не подтверждена.',
        settings_conflict: 'Настройки уже изменились. Обновите отчёт и повторите сохранение.',
        forbidden: 'Нет доступа к выбранному кабинету.', not_found: 'Товар не найден или недоступен.',
    };
    function message(code) { return messages[code] || code; }
    function prepareProfit(root) {
        root.classList.add('ozon-profit-report');
        root.querySelector('h1').title = 'Товары с заказами, рекламой или положительным остатком Ozon FBS, FBO, ФФ хотя бы в один день периода. Текущие остатки показаны на момент формирования отчёта.';
        const template = document.getElementById('ozon-report-manager-filter');
        if (template && !document.getElementById('ue1cr-manager-options')) {
            root.querySelector('.ue1cr-filter--articles').before(template.content.cloneNode(true));
        }
    }
    function profitPayload(report) {
        const labels = window.OzonEconomicsUI?.marginLabels || {};
        const diagnostic = value => [
            ...(value.missing || []).map(key => 'Нет данных: ' + (labels[key] || key)),
            ...(value.issues || []).map(key => labels[key] || key),
        ];
        const row = (value = {}) => ({ ...value, subject: value.subject || 'Без категории',
            name: value.row_kind === 'category' ? value.subject || 'Без категории' : value.name,
            messages: value.margin_complete ? [] : ['Неполный расчёт за: ' + (value.margin_missing_days || []).map(day => day.split('-').reverse().join('.')).join(', ')],
            daily_calculations: (value.daily_calculations || []).map(day => ({ ...day,
                messages: diagnostic(day), status: day.complete ? 'ready' : day.available ? 'partial' : 'missing',
            })),
        });
        return { ...report, rows: (report.rows || []).map(row), category_rows: (report.category_rows || []).map(row),
            totals: row(report.totals), filters: { ...report.filters,
                subjects: report.filters.subjects.map(subject => subject || 'Без категории') },
        };
    }
    function targetPayload(report) {
        return { ...report, rows: report.rows.map(row => {
            const costs = row.target_calculation?.costs || {};
            const fields = { purchase: 'purchase', fulfillment: 'fulfillment', commission: 'commission_amount',
                logistics: 'logistics_amount', storage: 'storage_amount', acquiring: 'acquiring_amount',
                company_commission: 'team_amount', vat: 'vat_amount', usn: 'usn_amount', advertising: 'advertising_amount' };
            return { ...row, target_warnings: (row.issues || []).map(message).concat(
                row.missing?.length ? ['Нет данных: ' + row.missing.map(key => window.OzonEconomicsUI?.marginLabels[key] || key).join(', ')] : []),
                current_warnings: (row.current_missing || []).map(key => 'Нет данных: ' + (window.OzonEconomicsUI?.marginLabels[key] || key)),
                current_notes: [], current_drr_warnings: [], current_drr_notes: [],
                target_calculation: row.target_calculation ? { ...row.target_calculation,
                    costs: Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, costs[field]])) } : null,
            };
        }) };
    }
    function prepareTarget(root) {
        root.querySelector('h1 small').textContent = 'Ozon';
        root.querySelectorAll('[data-sort="current_price"], [data-sort="target_price"]').forEach(node => {
            node.textContent = 'Цена с Кошельком, ₽';
        });
        const paragraphs = root.querySelectorAll('details.uetp-help p, .uetp-head details p, .uetp-description p');
        const heading = root.querySelector('.uetp-help-content > strong');
        if (heading) heading.textContent = 'Расчёт Ozon';
        const text = [
            'Рассчитываются товары с текущим остатком FBS, FBO, ФФ или в пути на склады либо с ТО за последние 7 завершённых дней по Москве.',
            'Текущий ДРР — за 7 завершённых дней по совпадающим датам заказов и рекламы. Текущий ROI соответствует калькулятору: свежие цены и расходы, реклама на выкуп за последние 7 завершённых дней.',
            'Цена подбирается по формулам калькулятора Ozon. Сохраняются доли СПП и Кошелька, текущая ставка комиссии, логистика, хранение и расходы. Плановая реклама на выкуп = цена продавца × целевой ДРР / 100. Ставка комиссии фиксируется по текущему снимку; изменение тарифа Ozon после смены цены в сценарии не прогнозируется.',
            'Цели товара имеют приоритет над целями кабинета. ROI можно задать для кодов A/B/C/D/F/NEW/U; пустое поле кода использует общий ROI, ноль сохраняется как отдельная цель.',
            'Сохранение целей не меняет цену в Ozon. Неподтверждённые данные отмечены в строке.',
        ];
        paragraphs.forEach((node, index) => { if (text[index]) node.textContent = text[index]; });
        root.querySelector('#ym-goal-dialog h2').textContent = 'Настройка ROI и ДРР · Ozon';
        root.querySelector('#ym-target-settings').textContent = 'Настройка ROI';
    }
    const scenarioFields = [
        ['seller_price', 'Цена продавца, ₽'], ['buyer_price', 'Цена с СПП, ₽'], ['wallet_price', 'Цена с Кошельком, ₽'],
        ['purchase', 'Закупочная стоимость, ₽'], ['fulfillment', 'Затраты на ФФ, ₽'],
        ['commission_amount', 'Комиссия Ozon при исходной цене, ₽'], ['logistics_amount', 'Логистика на выкуп, ₽'],
        ['storage_amount', 'Хранение на выкуп, ₽'], ['team_percent', 'Комиссия компании, %'],
        ['acquiring_percent', 'Эквайринг, %'], ['vat_percent', 'НДС, %'], ['usn_percent', 'УСН, %'],
    ];
    window.CheckStockPage.register(url => url.pathname === pageBase + '/unit-profit' || url.pathname === pageBase + '/target-price', async (context, url) => {
        if (!context.economics_preview) throw new Error('Раздел Ozon недоступен');
        const target = url.pathname.endsWith('/target-price');
        const response = await fetch('/static/economics/ozon/report-columns.json?v=20261029');
        if (!response.ok) throw new Error('Не удалось загрузить описание отчёта');
        const columns = await response.json();
        const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow' }).format(new Date());
        const start = new Date(today + 'T12:00:00Z'); start.setUTCDate(start.getUTCDate() - 6);
        const config = { marketplace: 'OZON', stores: context.stores.filter(store => store.marketplaces.includes('OZON')),
            dataEndpoint: base + (target ? '/target-price' : '/unit-profit'), filtersEndpoint: base + '/unit-profit/filters',
            exportEndpoint: target ? base + '/target-price.xlsx' : pageBase + '/unit-profit.xlsx',
            defaultDateFrom: start.toISOString().slice(0, 10), defaultDateTo: today,
            canEdit: context.user.is_active, canManageGoals: context.user.is_active,
            dailyColumns: columns.daily, scenarioFields, walletLabel: 'Цена с Кошельком', marketplaceLabel: 'Ozon',
        };
        const encoded = JSON.stringify(config).replace(/</g, '\\u003c');
        return { title: 'CheckStock — ' + (target ? 'Целевая цена' : 'Юниточная прибыль') + ' · Ozon',
            section: 'unit_1c_ozon', contentClass: 'content--unit-1c-report', alertMarketplace: 'OZON',
            values: target ? { target_price_config: encoded } : {
                report_marketplace: 'Ozon', unit_1c_report_config: encoded, unit_1c_manager_filter: '',
            },
        };
    });
    return { message, prepareProfit, profitPayload, targetPayload, prepareTarget };
})();
