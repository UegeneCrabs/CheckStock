window.OzonAdvertisingUI = (() => {
    'use strict';
    const escape = window.CheckStockUI.escapeHtml;
    const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 2 });
    const dailyMoney = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    const spendColumnIndex = 8;
    const definitions = Object.freeze([
        { kind: 'cpc', label: 'За клики' },
        { kind: 'cpo_selected', label: 'За заказ (выбранные)' },
        { kind: 'cpo_all', label: 'За заказ (все)' },
        { kind: 'cpo_combo', label: 'Комбо: за заказ' },
        { kind: 'banner', label: 'Баннеры' },
        { kind: 'video', label: 'Видеобаннеры' },
        { kind: 'brand_shelf', label: 'Брендовая полка' },
        { kind: 'blogger', label: 'Блогеры' },
        { kind: 'vk', label: 'ВКонтакте' },
        { kind: 'other', label: 'Прочая реклама' },
    ]);
    const states = Object.freeze({
        missing: 'Отчёт за этот период ещё не загружен.',
    });
    const dayLabel = day => String(day || '').split('-').reverse().join('.');
    const amount = value => value == null ? '—' : money.format(Number(value));

    function summary(product) {
        return product.advertising && product.advertising.spend_summary;
    }

    function spendValue(product) {
        const value = summary(product);
        return value && value.amount != null ? value.amount : null;
    }

    function spendPartial(product) {
        const value = summary(product);
        return !value || !value.complete || value.stale;
    }

    function cellValue(product, index) {
        return Number(index) === spendColumnIndex ? spendValue(product) : undefined;
    }

    function spendTooltip(product) {
        const value = summary(product);
        if (!value) return states.missing;
        const lines = [dayLabel(value.period.start) + ' — ' + dayLabel(value.period.end) + ' · МСК'];
        const knownDays = value.days.filter(day => day.amount != null);
        lines.push('Есть данные за ' + knownDays.length + ' из ' + value.days.length + ' дней.');
        if ((value.missing_dates || []).length) lines.push('Нет данных за ' + value.missing_dates.map(dayLabel).join(', ') + '.');
        if ((value.partial_dates || []).length) lines.push('Расходы подтверждены частично за ' + value.partial_dates.map(dayLabel).join(', ') + '.');
        value.days.forEach(day => {
            const parts = definitions.map(definition => {
                const expense = day.breakdown.find(item => item.kind === definition.kind);
                let text = definition.label + ': ';
                if (!expense || expense.amount == null) {
                    text += 'нет данных';
                } else {
                    text += dailyMoney.format(Number(expense.amount)) + ' руб.';
                    if (expense.state !== 'ready') text += ' (известная часть)';
                }
                if (expense && expense.stale) text += ' (предыдущая выгрузка)';
                if (expense && definition.kind === 'other' && expense.raw_types.length) {
                    text += ' [' + expense.raw_types.join(', ') + ']';
                }
                return text;
            });
            lines.push(dayLabel(day.day) + ' — ' + parts.join('; '));
        });
        lines.push('Итого по известным расходам: ' + amount(value.amount));
        if (!value.complete) lines.push('Отсутствующие данные не считаются нулевыми расходами.');
        lines.push('Нераспределённые расходы и корректировки магазина не включены.');
        return lines.join('\n');
    }

    function spendContent(product) {
        const title = spendTooltip(product);
        const partial = spendValue(product) != null && spendPartial(product);
        const marker = partial ? window.CheckStockUI.render('economics/shared/dashboard/coverage-value') : '';
        return '<span class="ue1c-coverage-value' + (partial ? ' is-partial' : '') + '" tabindex="0" title="' +
            escape(title) + '" aria-label="' + escape(title) + '">' + escape(amount(spendValue(product))) + marker + '</span>';
    }

    function trafficTooltip(product, metric) {
        const value = product.advertising || {};
        const coverage = value.traffic_coverage;
        const lines = [metric === 'ctr' ? 'CTR: клики / показы × 100%.' : 'CPC: расходы на оплату кликов / клики.'];
        lines.push('Кампании «Оплата за клик», ' + dayLabel(value.period_from) + ' — ' + dayLabel(value.period_to) + ' · МСК.');
        if (!coverage || !coverage.days) return lines.concat('Данные о показах и кликах за этот период не загружены.').join('\n');
        lines.push('Есть данные за ' + coverage.days + ' из ' + coverage.expected_days + ' дней.');
        lines.push('Показы: ' + dailyMoney.format(value.impressions) + '. Клики: ' + dailyMoney.format(value.clicks) + '.');
        if (metric === 'cpc') lines.push('Расходы на оплату кликов: ' + amount(value.click_spend) + '.');
        if (metric === 'ctr' && !value.impressions) lines.push('CTR не рассчитывается без показов.');
        if (metric === 'cpc' && !value.clicks) lines.push('CPC не рассчитывается без кликов.');
        if ((coverage.missing_dates || []).length) lines.push('Нет данных за ' + coverage.missing_dates.map(dayLabel).join(', ') + '.');
        if ((coverage.partial_dates || []).length) lines.push('Полнота не подтверждена за ' + coverage.partial_dates.map(dayLabel).join(', ') + '.');
        if (coverage.stale) lines.push('Обновление не удалось, показаны сохранённые данные.');
        return lines.join('\n');
    }

    function trafficContent(product, metric) {
        const advertising = product.advertising || {};
        const value = advertising[metric];
        const title = trafficTooltip(product, metric);
        if (value == null) return '<span title="' + escape(title) + '">—</span>';
        const coverage = advertising.traffic_coverage;
        const partial = coverage && coverage.days > 0 && !coverage.complete;
        return window.CheckStockUI.render('economics/shared/dashboard/coverage-value-2', {
            content: partial ? ' is-partial' : '', content_2: title,
            value: metric === 'ctr' ? dailyMoney.format(value) : amount(value), content_3: metric === 'ctr' ? '%' : '',
            content_4: partial ? window.CheckStockUI.render('economics/shared/dashboard/coverage-value') : '',
        });
    }

    function spendTotal(products) {
        const known = products.filter(product => spendValue(product) != null);
        return {
            value: known.length ? known.reduce((total, product) => total + Number(spendValue(product)), 0) : null,
            unit: 'money',
            partial: products.some(spendPartial),
            title: 'Сумма известных расходов товаров за выбранный период. Нераспределённые расходы и корректировки магазина не включены.',
        };
    }

    const percent = value => value == null ? '—' : dailyMoney.format(Number(value)) + '%';
    const timestamps = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });

    function drrTooltip(product) {
        const basis = (product.advertising || {}).drr_basis || {};
        const buyout = basis.buyout || {};
        return ['ТО после отмен: ' + amount(basis.turnover), 'Расходы на рекламу: ' + amount(basis.spend),
            'Процент выкупа за 30 дней: ' + percent(basis.buyout_percent),
            'Период выкупа: ' + (buyout.period_from || '—') + ' — ' + (buyout.period_to || '—'),
            buyout.default_applied ? 'Применён выкуп по умолчанию из настроек кабинета.' : '',
            buyout.stale ? 'Данные выкупа устарели.' : ''].filter(Boolean).join('\n');
    }

    function drrContent(product) {
        const basis = (product.advertising || {}).drr_basis || {};
        const partial = basis.coverage && !basis.coverage.complete && basis.drr != null;
        return '<span class="ue1c-coverage-value' + (partial ? ' is-partial' : '') + '" tabindex="0" title="' + escape(drrTooltip(product)) + '">' +
            escape(percent(basis.drr)) + (partial ? window.CheckStockUI.render('economics/shared/dashboard/coverage-value') : '') + '</span>';
    }

    function otherBreakdownLines(breakdown) {
        if (breakdown == null) return ['Сумма прочей рекламы используется целиком. Распределение по категориям не задано; после ручной правки «Сбросить» возвращает исходный состав.'];
        const lines = breakdown.map(item => {
            const label = definitions.find(definition => definition.kind === item.kind)?.label || item.kind;
            let text = label + ': ' + amount(item.amount) + '; ДРР ' + percent(item.drr) + '.';
            if (item.amount == null) text += ' Расходы неизвестны, в сумму не включены.';
            else if (item.state !== 'ready') text += ' Включена только известная часть расходов.';
            if (item.state === 'unsupported') text += ' Источник не предоставляет полный расчёт.';
            if (item.stale) text += ' Предыдущая выгрузка; данные устарели.';
            if (item.kind === 'other' && item.raw_types?.length) text += ' Типы: ' + item.raw_types.join(', ') + '.';
            return text;
        });
        lines.push('Итог включает только известные расходы на этот товар. Неизвестные категории не подтверждают ноль и не блокируют общий ДРР. Клики и заказы учтены отдельно.');
        return lines;
    }

    function renderCalculator(root, product) {
        const calculator = root.querySelector('#ue1c-panel-economics > .ue1c-calculator:not(.ue1c-placeholder-calculator)');
        if (!calculator) return;
        const inputs = calculator.querySelector('.ue1c-calculator-inputs');
        const anchor = calculator.querySelector('#ue1c-subject-select').closest('.ue1c-calculator-row');
        ['drr', 'advertisingRub'].forEach(key => {
            const input = calculator.querySelector('[data-calculator-input="' + key + '"]');
            if (input) input.closest('.ue1c-calculator-row').hidden = true;
        });
        const data = product.calculator || {};
        const buyout = data.buyout || {};
        const stamp = value => value ? 'Обновлено ' + timestamps.format(new Date(value)) + ' МСК' : 'Данные ещё не загружены';
        const period = data.day ? 'Реклама за ' + dayLabel(data.day) + ' · МСК.' : 'Сегодняшние данные рекламы ещё не загружены.';
        const buyoutPeriod = buyout.period_from && buyout.period_to ?
            'Выкуп за ' + dayLabel(buyout.period_from) + ' — ' + dayLabel(buyout.period_to) + ' · без сегодняшнего дня.' :
            'Период выкупа ещё не загружен.';
        const buyoutDetails = [buyoutPeriod, 'Период и значение по умолчанию — в параметрах кабинета Ozon. Обновление каждые 4 часа.',
            'Выкупленные без возвратов / (выкупленные без возвратов + отмены + возвраты) × 100%.',
            buyout.retained == null ? '' : 'Выкуплено без возвратов: ' + buyout.retained + '; отменено: ' + buyout.cancelled + '; возвращено: ' + buyout.returned + '.',
            buyout.default_applied ? 'Применён выкуп по умолчанию: ' + percent(buyout.percent) + '. Фактический: ' + percent(buyout.actual_percent) + '.' : '',
            buyout.stale ? 'Сохранённые данные устарели. Ожидается обновление.' : '',
            buyout.error ? 'Последнее обновление не удалось.' : '',
            stamp(buyout.observed_at)].filter(Boolean).join('\n');
        const fields = [
            { key: 'buyoutPercent', label: 'Процент выкупа', unit: '%', value: data.buyout_percent, details: buyoutDetails,
                missing: 'Нет полного расчёта выкупа за выбранный период и значения по умолчанию.' },
        ];
        [['clicks', 'клики', 'click'], ['orders', 'заказы', 'order']].forEach(([key, label, prefix]) => {
            const value = data[key] || {};
            const missing = !value.coverage || !value.coverage.days ? 'Нет совместной выгрузки заказов и рекламы.' :
                value.buyout_percent == null ? 'Нет процента выкупа за период кабинета или значения по умолчанию.' :
                Number(value.turnover) <= 0 ? 'Нет положительного ТО для расчёта ДРР.' : '';
            fields.push(
                { key: key + 'Spend', label: 'Расходы за ' + label, unit: '₽', value: data[prefix + '_spend'],
                    updatedAt: data[prefix + '_updated_at'], missing: 'Данные рекламных расходов за сегодня ещё не загружены.' },
                { key: key + 'Drr', label: 'ДРР за ' + label, unit: '%', value: value.drr,
                    updatedAt: value.updated_at, details: period + '\n' + stamp(value.updated_at) + '\n' + buyoutDetails, missing },
            );
        });
        fields.forEach(spec => {
            let row = calculator.querySelector('[data-ozon-metric-row="' + spec.key + '"]');
            if (!row && spec.key === 'buyoutPercent') {
                row = calculator.querySelector('[data-calculator-input="buyoutPercent"]').closest('.ue1c-calculator-row');
            }
            if (!row) {
                row = document.createElement('label');
                row.className = 'ue1c-calculator-row';
                const caption = document.createElement('span');
                caption.textContent = spec.label;
                const field = document.createElement('span');
                field.className = 'ue1c-calculator-field';
                const unit = document.createElement('b');
                unit.textContent = spec.unit;
                field.append(document.createElement('input'), unit);
                row.append(caption, field);
            }
            row.dataset.ozonMetricRow = spec.key;
            row.hidden = false;
            row.classList.toggle('ue1c-calculator-row--expanded', spec.key !== 'buyoutPercent');
            row.style.setProperty('--compact-order', '3');
            row.style.setProperty('--expanded-order', '8');
            const input = row.querySelector('input');
            input.type = 'number';
            input.step = '0.01';
            if (spec.key === 'buyoutPercent') input.min = '0';
            input.readOnly = true;
            input.disabled = false;
            input.placeholder = '—';
            input.setAttribute('aria-label', spec.label);
            const value = spec.value == null ? null : Number(spec.value);
            const known = value !== null && Number.isFinite(value);
            input.value = known ? String(Math.round(value * 100) / 100) : '';
            row.title = [spec.label, spec.details || period + '\n' + stamp(spec.updatedAt), known ? '' : spec.missing].filter(Boolean).join('\n');
            inputs.insertBefore(row, anchor);
        });
    }

    return { spendColumnIndex, spendValue, spendPartial, spendContent, spendTooltip, spendTotal, cellValue, trafficContent, trafficTooltip, drrTooltip, drrContent, otherBreakdownLines, renderCalculator };
})();
