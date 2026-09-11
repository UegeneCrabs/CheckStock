(function () {
    'use strict';
    var fields = window.YandexEconomicsFields;
    var esc = fields.esc, number = fields.number, request = fields.request;
    var compact = ['seller_price', 'buyer_price', 'plan_drr', 'buyout_percent', 'fulfillment_cost', 'purchase_price'];
    var specs = [].concat.apply([], fields.groups.slice(0, 4).map(function (g) { return g[1]; }));
    var costs = {commission: 'Размещение', payment_acceptance: 'Приём платежа', payment_transfer: 'Перевод платежа',
        delivery: 'Доставка', returns: 'Невыкупы и возвраты', storage: 'Хранение', transit: 'Транзит',
        purchase: 'Закупка', fulfillment: 'Фулфилмент', other: 'Прочие', tax: 'Налог',
        capital: 'Стоимость капитала', loss: 'Потери', disposal: 'Утилизация', advertising: 'Реклама', tariff_extra: 'Другие услуги ЯМ'};
    function metric(label, value, unit) {
        return '<div><span>' + label + '</span><strong class="' + (value == null ? '' : value < 0 ? 'is-negative' : 'is-positive') + '">'
            + number(value) + (value == null ? '' : unit) + '</strong></div>';
    }
    function render(container, product, options) {
        if (container._ymDispose) container._ymDispose();
        var data = product.ym_economics;
        if (!data) { container.textContent = 'Данные экономики недоступны. Обновите страницу.'; return; }
        container.classList.add('ym-economics');
        var scheme = data.scheme || 'FBY', mode = 'current', changed = {}, sequence = 0, timer, alive = true, expanded = false;
        var historyCache = {}, historySequence = 0, parameters = options.parameters;
        var productPath = encodeURIComponent(product.store_slug) + '/' + encodeURIComponent(product.article);
        container._ymDispose = function () { alive = false; clearTimeout(timer); sequence++; historySequence++; };
        function message(text, error) {
            var node = container.querySelector('[data-ym-message]');
            if (node) { node.textContent = text; node.classList.toggle('is-error', !!error); }
        }
        function settingsUrl() {
            return '/admin/integrations?ym_store=' + encodeURIComponent(product.store_slug) + '&ym_article='
                + encodeURIComponent(product.article) + '&ym_scheme=' + scheme + '#yandex-economics-settings';
        }
        function field(spec, index) {
            var key = spec[0], value = data.values[key], label = spec[1], order = compact.indexOf(key);
            if (key === 'plan_drr') label = mode === 'current' ? 'ДРР с выкупом · сегодня' : 'Плановый ДРР с выкупом';
            var control, disabled = mode === 'current' || (key === 'plan_drr' && data.values.advertising_mode !== 'plan');
            if (spec[3] && typeof spec[3] === 'object') {
                control = '<select data-ym-field="' + key + '" aria-label="' + esc(label) + '"' + (disabled ? ' disabled' : '') + '>'
                    + '<option value="">Не задано</option>' + Object.keys(spec[3]).map(function (v) {
                        return '<option value="' + esc(v) + '"' + (String(value) === v ? ' selected' : '') + '>' + esc(spec[3][v]) + '</option>';
                    }).join('') + '</select>';
            } else {
                if (key === 'plan_drr' && data.values.advertising_mode !== 'plan') {
                    value = data.result.costs.advertising != null && data.values.seller_price > 0
                        ? data.result.costs.advertising / data.values.seller_price * 100 : null;
                }
                control = '<input data-ym-field="' + key + '" aria-label="' + esc(label) + '" type="number" min="0" step="any"'
                    + (spec[2] === '%' ? ' max="100"' : '') + (disabled ? ' disabled' : '')
                    + ' placeholder="—" value="' + esc(value) + '"><b>' + esc(spec[2]) + '</b>';
            }
            return '<label class="ue1c-calculator-row' + (order < 0 ? ' ue1c-calculator-row--expanded' : '') + '"'
                + ' style="--compact-order:' + order + ';--expanded-order:' + index + '"><span title="' + esc(data.origins[key] || 'Не задано') + '">'
                + esc(label) + '<em class="ue1c-expanded-only ym-origin" data-ym-origin="' + key + '">'
                + esc(data.origins[key] || 'Не задано') + '</em></span><span class="ue1c-calculator-field">' + control + '</span></label>';
        }
        function parameter(label, value, unit, origin, copy) {
            var display = value == null || value === '' ? '—' : typeof value === 'number' ? number(value) : value;
            return '<div class="ue1c-parameter"><span>' + esc(label) + '</span>'
                + (copy && value ? '<button type="button" class="ue1c-copy-value" data-copy-value="' + esc(value)
                    + '" data-copy-kind="' + esc(label) + '" data-copy-tooltip="нажмите чтобы скопировать"'
                    + ' data-copy-tooltip-default="нажмите чтобы скопировать" aria-label="Скопировать ' + esc(label) + '">' : '')
                + '<strong title="' + esc(origin || display) + '">' + esc(display) + (display === '—' ? '' : esc(unit || '')) + '</strong>'
                + (copy && value ? '</button>' : '') + '</div>';
        }
        function parameterGroup(title, content) {
            return '<section class="ue1c-parameter-group"><h4>' + esc(title)
                + '</h4><div class="ue1c-parameter-grid">' + content + '</div></section>';
        }
        function renderParameters() {
            var values = data.calculator_values || data.values, origins = data.calculator_origins || data.origins;
            var definitions = [].concat.apply([], fields.groups.map(function (group) { return group[1]; }));
            function parametersFor(keys) {
                return keys.map(function (key) {
                    var spec = definitions.find(function (entry) { return entry[0] === key; }), value = values[key];
                    if (spec[3] && typeof spec[3] === 'object' && value != null) value = spec[3][value] || value;
                    return parameter(spec[1], value, spec[2] ? ' ' + spec[2] : '', origins[key]);
                }).join('');
            }
            parameters.innerHTML = parameterGroup('Товар',
                parameter('Категория', values.category_name) + parameter('Артикул', product.article, '', '', true)
                + parameter('Баркод', product.barcode, '', '', true) + parameter('Магазин', product.store_name)
                + parameter('Закупочная цена', values.purchase_price, ' ₽') + parameter('Модель работы', scheme))
                + parameterGroup('Комиссии и налоги', parametersFor([
                    'commission_percent', 'payment_acceptance', 'payment_transfer_percent', 'tax_base', 'tax_percent']))
                + parameterGroup('Продажи и реклама', parametersFor(['seller_price', 'buyer_price', 'buyout_percent'])
                    + parameter('ДРР с выкупом · выбранный период', product.advertising.drr, '%')
                    + parameter('Реклама · выбранный период', product.advertising.spend, ' ₽'))
                + parameterGroup('Логистика', parametersFor(['fulfillment_cost', 'delivery_cost', 'return_cost',
                    'transit_cost', 'length', 'width', 'height', 'weight']))
                + parameterGroup('Хранение', parametersFor(['storage_per_day', 'storage_days']))
                + parameterGroup('Прочие расходы', parametersFor(['other_percent', 'other_cost', 'capital_percent',
                    'turnover_days', 'loss_percent', 'disposal_cost']))
                + '<div class="ue1c-parameter-save"><span>Действующие параметры · ' + esc(scheme) + '</span>'
                + (options.canManageSettings ? '<a class="ue1c-save-price ym-settings-link" href="' + esc(settingsUrl())
                    + '">Настроить параметры</a>' : '<span>Параметры задаёт администратор</span>') + '</div>'
                + '<section class="ue1c-parameter-group"><h4>Экономика за период</h4><div class="ym-period" data-ym-period></div></section>'
                + '<section class="ue1c-parameter-group"><h4 data-ym-costs-title>Расшифровка расходов</h4>'
                + '<dl class="ym-costs" data-ym-costs></dl><p class="ym-diagnostics" data-ym-diagnostics></p></section>';
        }
        function draw() {
            container.innerHTML = '<header class="ue1c-calculator-head"><h3>Расчёт чистой прибыли</h3>'
                + '<div class="ue1c-calculator-tools">'
                + '<label class="ue1c-calculator-mode"><span>Подробный расчёт</span><input data-ym-expanded type="checkbox" role="switch"'
                + (expanded ? ' checked' : '') + ' aria-controls="ym-calculator-inputs"><i aria-hidden="true"></i></label>'
                + '<details class="ue1c-formula-popover"><summary aria-label="Показать формулу ЯМ">?</summary><div>'
                + '<p><strong>Чистая прибыль</strong> = цена продавца − все расходы.</p><p><strong>ROI</strong> = прибыль ÷ закупочная цена × 100%.</p>'
                + '<p><strong>Маржинальность</strong> = прибыль ÷ цена покупателя без Пэй × 100%.</p>'
                + '<p>Невыкупы и утилизация умножаются на (1 − выкуп). Реклама сегодня = расходы ÷ (заказы × выкуп). '
                + 'В сценарии плановая реклама = цена продавца × ДРР.</p></div></details>'
                + '<button class="ue1c-calculator-reset" type="button" data-ym-reset>Сбросить</button></div></header>'
                + '<div class="ym-mode-bar" role="group" aria-label="Режим расчёта">'
                + ['current', 'calculator'].map(function (key) { return '<button class="ue1c-drawer-tab ' + (mode === key ? 'is-active' : '')
                    + '" type="button" data-ym-mode="' + key + '" aria-pressed="' + (mode === key) + '">'
                    + (key === 'current' ? 'Текущая экономика' : 'Калькулятор') + '</button>'; }).join('')
                + '<select class="ym-scheme" data-ym-scheme aria-label="Модель работы"><option value="FBY"'
                + (scheme === 'FBY' ? ' selected' : '') + '>FBY</option><option value="FBS"'
                + (scheme === 'FBS' ? ' selected' : '') + '>FBS</option></select></div>'
                + '<p class="ue1c-calculator-note">' + (mode === 'current'
                    ? 'Цены и реклама — за сегодня (МСК). Закупка и расходы — из действующих настроек.'
                    : 'Пробный расчёт. Изменения полей действуют только в этом сценарии.') + '</p>'
                + '<div class="ue1c-calculator-body"><div class="ue1c-calculator-inputs' + (expanded ? ' is-expanded' : '')
                + '" id="ym-calculator-inputs">' + specs.map(field).join('') + '</div>'
                + '<aside class="ue1c-calculator-results" data-ym-result aria-live="polite"></aside></div>'
                + '<p class="ue1c-calculator-note" data-ym-message role="status"></p>'
                + '<footer class="ue1c-calculator-actions">'
                + (mode === 'calculator' ? '<button type="button" class="ue1c-break-even" data-ym-quote>Пересчитать тариф API</button>'
                    : '<button type="button" class="ue1c-break-even" data-ym-calculator>Калькулятор</button>')
                + (options.canManageSettings ? '<a class="ue1c-save-price ym-settings-link" href="' + esc(settingsUrl()) + '">Настроить закупку и расходы</a>'
                    : '<span title="Постоянные параметры задаёт администратор в разделе API-ключи и фоновые выгрузки">Параметры задаёт администратор</span>') + '</footer>';
            renderParameters();
            showResult(data);
            container.querySelector('[data-ym-expanded]').onchange = function (event) {
                expanded = event.target.checked;
                container.querySelector('#ym-calculator-inputs').classList.toggle('is-expanded', expanded);
            };
            container.querySelector('[data-ym-scheme]').onchange = function (event) { load(mode, event.target.value); };
            container.querySelectorAll('[data-ym-mode]').forEach(function (button) {
                button.onclick = function () { load(button.dataset.ymMode, scheme); };
            });
            container.querySelector('[data-ym-reset]').onclick = function () { load(mode, scheme); };
            container.querySelectorAll('[data-ym-field]').forEach(function (input) { input.oninput = function () {
                var key = input.dataset.ymField;
                changed[key] = input.value === '' ? null : input.type === 'number' ? Number(input.value) : input.value;
                container.querySelector('[data-ym-origin="' + key + '"]').textContent = 'Сценарий';
                if (key === 'advertising_mode') {
                    var drr = container.querySelector('[data-ym-field="plan_drr"]');
                    drr.disabled = input.value !== 'plan';
                    drr.value = changed.plan_drr != null ? changed.plan_drr : data.values.plan_drr == null ? '' : data.values.plan_drr;
                }
                clearTimeout(timer); sequence++; pending();
                timer = setTimeout(function () { preview(false); }, 350);
            }; });
            var quote = container.querySelector('[data-ym-quote]');
            if (quote) quote.onclick = function () { clearTimeout(timer); preview(true); };
            var calculatorButton = container.querySelector('[data-ym-calculator]');
            if (calculatorButton) calculatorButton.onclick = function () { load('calculator', scheme); };
            loadHistory();
        }
        function pending() {
            container.querySelector('[data-ym-result]').innerHTML = metric('Чистая прибыль', null, ' ₽') + metric('ROI', null, '%')
                + metric('Маржинальность', null, '%');
            parameters.querySelector('[data-ym-costs]').innerHTML = '';
            message('Пересчитываем…');
        }
        function showResult(state) {
            var r = state.result, period = product.ym_economics.scheme === scheme
                ? product.ym_economics.period || {} : data.period || {}, coverage = period.coverage || {};
            var periodDays = coverage.expected_days || 7;
            container.querySelector('[data-ym-result]').innerHTML = metric('Чистая прибыль', r.margin, ' ₽') + metric('ROI', r.roi, '%')
                + metric('Маржинальность', r.margin_percent, '%');
            parameters.querySelector('[data-ym-costs]').innerHTML = Object.keys(r.costs || {}).map(function (key) {
                return '<div><dt>' + esc(costs[key] || key) + '</dt><dd>' + number(r.costs[key]) + ' ₽</dd></div>';
            }).join('');
            parameters.querySelector('[data-ym-period]').innerHTML = '<strong>Экономика за ' + periodDays + ' завершённых дней · ' + scheme + '</strong>'
                + '<span>Прибыль: ' + number(period.margin) + ' ₽ · ROI: ' + number(period.roi) + '%</span><small>Дней с данными: '
                + (coverage.days || 0) + '/' + periodDays + (coverage.complete ? '' : ' · история ещё не полная') + '</small>';
            parameters.querySelector('[data-ym-costs-title]').textContent = 'Расходы · ' + (mode === 'calculator' ? 'сценарий' : 'текущая экономика');
            parameters.querySelector('[data-ym-diagnostics]').textContent = r.messages.join(' ');
            message(r.messages.length ? 'Не хватает данных для расчёта. Подробности — во вкладке «Параметры».' : mode === 'calculator'
                ? 'Сценарий рассчитан. Тариф API — оценка; при изменении цены пересчитайте тариф.'
                : 'Действующие параметры · ' + state.advertising_day + ' (МСК)', !!r.messages.length);
            var drr = container.querySelector('[data-ym-field="plan_drr"]');
            if (state.values.advertising_mode !== 'plan' && drr) {
                drr.value = r.costs.advertising != null && state.values.seller_price > 0
                    ? Number((r.costs.advertising / state.values.seller_price * 100).toFixed(2)) : '';
            }
        }
        async function load(nextMode, nextScheme) {
            clearTimeout(timer); var current = ++sequence; pending();
            container.querySelectorAll('[data-ym-field]').forEach(function (input) { input.disabled = true; });
            try {
                var result = await request('calculate/' + productPath, 'POST', {scheme: nextScheme, mode: nextMode, values: {}});
                if (alive && current === sequence) { data = result.economics; mode = nextMode; scheme = nextScheme; changed = {}; draw(); }
            } catch (error) { if (alive && current === sequence) { draw(); message(error.message, true); } }
        }
        async function preview(quote) {
            var current = ++sequence; pending();
            var invalid = Array.from(container.querySelectorAll('[data-ym-field]')).find(function (input) { return !input.checkValidity(); });
            if (invalid) { message('Проверьте значение: ' + invalid.getAttribute('aria-label'), true); return; }
            try {
                var result = await request('calculate/' + productPath, 'POST', {scheme: scheme, mode: 'calculator', values: changed, refresh_tariffs: quote});
                if (alive && current === sequence) showResult(result.economics);
            } catch (error) { if (alive && current === sequence) message(error.message, true); }
        }
        function showHistory(rows) {
            options.onHistory(rows, data.advertising_day);
            options.historyContainer.innerHTML = '<details class="ym-history"><summary>История по дням</summary>'
                + (rows.length ? '<table><thead><tr><th>Дата</th><th>Заказы</th><th>Выкупы, расчёт</th><th>Реклама</th><th>Прибыль</th></tr></thead><tbody>'
                    + rows.slice().sort(function (a, b) { return b.day.localeCompare(a.day); }).map(function (row) {
                        return '<tr><td>' + esc(row.day) + '</td><td>' + number(row.orders_count) + '</td><td>' + number(row.expected_buyouts)
                            + '</td><td>' + number(row.advertising_spend) + ' ₽</td><td>' + number(row.profit) + ' ₽</td></tr>';
                    }).join('') + '</tbody></table>'
                    : '<p>Снимки ещё не накоплены. Прошлые дни не заполняются сегодняшними параметрами.</p>') + '</details>';
        }
        async function loadHistory() {
            var current = ++historySequence, currentScheme = scheme;
            if (historyCache[scheme]) { showHistory(historyCache[scheme]); return; }
            options.onHistory([], data.advertising_day, 'Загружаем историю…');
            options.historyContainer.innerHTML = '';
            try {
                var result = await request('economics-history/' + productPath + '?scheme=' + scheme);
                if (!alive || current !== historySequence || scheme !== currentScheme) return;
                historyCache[scheme] = result.history || [];
                showHistory(historyCache[scheme]);
            } catch (error) {
                if (!alive || current !== historySequence) return;
                options.onHistory([], data.advertising_day, error.message);
                options.historyContainer.innerHTML = '<div class="ym-history"><button type="button" class="ue1c-calculator-reset" data-ym-history-retry>Повторить загрузку</button></div>';
                options.historyContainer.querySelector('[data-ym-history-retry]').onclick = loadHistory;
            }
        }
        draw();
    }
    window.YandexEconomics = {render: render};
}());
