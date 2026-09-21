(function () {
    'use strict';
    var fields = window.YandexEconomicsFields;
    var esc = fields.esc,
        number = fields.number,
        request = fields.request;
    var compact = [
        'seller_price',
        'buyer_price',
        'pay_price',
        'plan_drr',
        'buyout_percent',
        'advertising_spend',
        'purchase_price',
    ];
    // Common fields follow the WB calculator; YM logistics stay beside delivery.
    var expandedOrder = [
        'seller_price',
        'buyer_price',
        'pay_price',
        'category_name',
        'commission_percent',
        'commission_rub',
        'delivery_cost',
        'length',
        'width',
        'height',
        'weight',
        'volume_l',
        'return_middle_mile',
        'return_cost',
        'transit_cost',
        'plan_drr',
        'buyout_percent',
        'advertising_spend',
        'acquiring_percent',
        'payment_acceptance',
        'purchase_price',
        'company_commission_percent',
        'vat_percent',
        'usn_percent',
        'loss_percent',
        'disposal_cost',
    ];
    var specs = [].concat.apply(
        [],
        fields.groups.map(function (g) {
            return g[1];
        }),
    ).concat([['commission_rub', 'Комиссия YM, руб', '₽']]).filter(function (spec) {
        return !['campaign_id', 'frequency', 'payment_delay_weeks'].includes(spec[0]);
    }).sort(function (a, b) {
        return expandedOrder.indexOf(a[0]) - expandedOrder.indexOf(b[0]);
    });
    var tariffFields = ['commission_percent', 'payment_acceptance', 'delivery_cost'];
    var quoteFields = ['seller_price', 'length', 'width', 'height', 'weight'];
    var logisticsFields = ['length', 'width', 'height', 'weight', 'volume_l',
        'return_middle_mile', 'return_cost', 'transit_cost'];
    var costs = {
        commission: 'Комиссия YM',
        payment_acceptance: 'Приём платежа (Экваиринг 2)',
        acquiring: 'Перевод платежа (Экваринг1)',
        delivery: 'Доставка выкупленного товара',
        returns: 'Невыкупы и возвраты',
        transit: 'Транзит',
        purchase: 'Закупочная стоимость',
        company_commission: 'Комиссия компании',
        vat: 'Налог НДС, руб',
        usn: 'Налог УСН, руб',
        loss: 'Потери от закупочной цены',
        disposal: 'Утилизация',
        advertising: 'Реклама',
    };
    function inputValue(key, value) {
        if (value == null) return '';
        return key === 'commission_percent' || key === 'commission_rub' || key === 'advertising_spend'
            ? Number(value).toFixed(2) : String(value);
    }
    function metric(label, value, unit) {
        return window.CheckStockUI.render('economics/yandex/calculator/metric', {
            label: label,
            content: value == null ? '' : value < 0 ? 'is-negative' : 'is-positive',
            value: number(value),
            content_2: value == null ? '' : unit,
        });
    }
    function render(container, product, options) {
        if (container._ymDispose) container._ymDispose();
        var initial = product.ym_economics;
        if (!initial) {
            container.textContent = 'Данные экономики недоступны. Обновите страницу.';
            return;
        }
        var data = Object.assign({}, initial, {
            values: initial.calculator_values || initial.values,
            origins: initial.calculator_origins || initial.origins,
            result: initial.calculator_result || initial.result,
            pricing: initial.calculator_pricing || initial.pricing,
            tariff: initial.calculator_tariff || initial.tariff,
        });
        container.classList.add('ym-economics');
        var scheme = data.scheme || 'FBY',
            changed = {},
            sequence = 0,
            timer,
            alive = true,
            expanded = false;
        var picker, categoryEdited = false, tariffNeedsQuote = false, manualTariffs = {};
        var logisticsOpen = false, tariffDetails = null;
        var historyCache = {},
            historySequence = 0,
            parameters = options.parameters;
        var productPath = encodeURIComponent(product.store_slug) + '/' + encodeURIComponent(product.article);
        container._ymDispose = function () {
            alive = false;
            clearTimeout(timer);
            sequence++;
            historySequence++;
            if (picker) picker.dispose();
        };
        function message(text, error) {
            var node = container.querySelector('[data-ym-message]');
            if (node) {
                node.textContent = text;
                node.classList.toggle('is-error', !!error);
            }
        }
        function settingsUrl() {
            return (
                '/admin/integrations?ym_store=' +
                encodeURIComponent(product.store_slug) +
                '&ym_article=' +
                encodeURIComponent(product.article) +
                '&ym_scheme=' +
                scheme +
                '#yandex-economics-settings'
            );
        }
        function computedRow(key, label, index, hint, className) {
            return window.CheckStockUI.render('economics/yandex/calculator/computed-row', {
                key: key, label: label, index: index, hint: hint || '', className: className || '',
            });
        }
        function logisticsBlock() {
            function input(key) {
                return field(specs.find(function (spec) { return spec[0] === key; }), true);
            }
            return window.CheckStockUI.render('economics/yandex/calculator/logistics', {
                index: expandedOrder.indexOf('delivery_cost'),
                open: logisticsOpen ? ' open' : '',
                content: input('length') + input('volume_l') + input('delivery_cost') +
                    computedRow('delivery-customer', 'Доставка покупателю', 0, 'Входит в доставку выкупленного товара.', 'ym-tariff-part') +
                    computedRow('delivery-middle', 'Средняя миля', 0, 'Входит в доставку выкупленного товара.', 'ym-tariff-part') +
                    computedRow('delivery-other', 'Другие услуги доставки', 0, '', 'ym-tariff-part') +
                    window.CheckStockUI.render('economics/yandex/calculator/delivery-note') +
                    input('return_middle_mile') + input('return_cost') +
                    computedRow('logistics-returns', 'Невыкупы с учётом процента выкупа', 0,
                        'Расход на один невыкуп × (1 − процент выкупа / 100). Именно эта сумма входит в итог.') +
                    input('transit_cost'),
            });
        }
        function field(spec, inLogistics) {
            var key = spec[0],
                index = expandedOrder.indexOf(key),
                value = data.values[key],
                label = spec[1],
                order = compact.indexOf(key);
            if (!inLogistics && key === 'delivery_cost') return logisticsBlock();
            if (!inLogistics && logisticsFields.includes(key)) return '';
            if (key === 'category_name') return window.CheckStockUI.render('economics/yandex/categories/container', { index: index });
            if (key === 'length') return window.CheckStockUI.render('economics/yandex/calculator/dimensions', {
                index: index,
                content: ['length', 'height', 'width', 'weight'].map(function (dimension, position) {
                    var definition = specs.find(function (entry) { return entry[0] === dimension; });
                    return window.CheckStockUI.render('economics/yandex/calculator/dimension', {
                        key: dimension,
                        label: definition[1],
                        unit: definition[2],
                        value: data.values[dimension],
                        separator: position === 3 ? ', ' : position ? '×' : '',
                    });
                }).join(''),
            });
            if (['height', 'width', 'weight'].includes(key)) return '';
            var control;
            if (spec[3] && typeof spec[3] === 'object') {
                control = window.CheckStockUI.render('economics/yandex/calculator/field-2', {
                    key: key,
                    label: label,
                    content: '',
                    content_2: Object.keys(spec[3])
                        .map(function (v) {
                            return window.CheckStockUI.render('common/ui/select-option', {
                                v: v,
                                content: String(value) === v ? ' selected' : '',
                                content_2: spec[3][v],
                            });
                        })
                        .join(''),
                });
            } else {
                var text = spec[3] === 'text';
                control = window.CheckStockUI.render('economics/yandex/calculator/field-3', {
                    key: key,
                    label: label,
                    type: text ? 'text' : 'number',
                    content: text ? ' maxlength="500"' :
                        ' min="0" step="any"' +
                        (spec[2] === '%' && key !== 'plan_drr' ? ' max="100"' : ''),
                    value: inputValue(key, value),
                    content_3: spec[2],
                });
            }
            var row = window.CheckStockUI.render('economics/yandex/calculator/field-4', {
                content: (order < 0 ? ' ue1c-calculator-row--expanded' : '') +
                    (key === 'plan_drr' ? ' ym-drr-row' : ''),
                order: order,
                index: index,
                label: label,
                expandedLabel: fields.expandedLabels[key] || label,
                hint: fields.hints[key] || '',
                key: key,
                control: control,
            });
            return row + (key === 'buyout_percent' ? window.CheckStockUI.render('economics/yandex/calculator/advertising-spend', {
                order: compact.indexOf('advertising_spend'), index: expandedOrder.indexOf('advertising_spend'),
            }) : '');
        }
        function parameter(label, value, unit, origin, copy) {
            var display =
                value == null || value === '' ? '—' : typeof value === 'number' ? number(value) : value;
            return window.CheckStockUI.render('economics/yandex/calculator/parameter-3', {
                label: label,
                content:
                    copy && value
                        ? window.CheckStockUI.render('economics/yandex/calculator/parameter', {
                              value: value,
                              label: label,
                              label_2: label,
                          })
                        : '',
                content_2: origin || display,
                display: display,
                content_3: display === '—' ? '' : esc(unit || ''),
                content_4:
                    copy && value
                        ? window.CheckStockUI.render('economics/yandex/calculator/parameter-2')
                        : '',
            });
        }
        function parameterGroup(title, content) {
            return window.CheckStockUI.render('economics/yandex/calculator/parameter-group', {
                title: title,
                content: content,
            });
        }
        function renderParameters() {
            var values = data.calculator_values || data.values,
                origins = data.calculator_origins || data.origins;
            var categoryPath = ((data.category || {}).path || [])
                .map(function (entry) { return entry.name; })
                .join(' → ');
            var categoryCommission = data.category_commission || {};
            var definitions = [].concat.apply(
                [],
                fields.groups.map(function (group) {
                    return group[1];
                }),
            );
            function parametersFor(keys) {
                return keys
                    .map(function (key) {
                        var spec = definitions.find(function (entry) {
                                return entry[0] === key;
                            }),
                            value = values[key];
                        if (spec[3] && typeof spec[3] === 'object' && value != null)
                            value = spec[3][value] || value;
                        return parameter(spec[1], value, spec[2] ? ' ' + spec[2] : '', origins[key]);
                    })
                    .join('');
            }
            parameters.innerHTML = window.CheckStockUI.render(
                'economics/yandex/calculator/render-parameters-3',
                {
                    content:
                        parameterGroup(
                            'Товар',
                            parameter('Категория', categoryPath || values.category_name) +
                                parameter(
                                    'Категория проверена',
                                    (data.category || {}).checked_at
                                        ? new Date(data.category.checked_at).toLocaleString('ru-RU') : null,
                                ) +
                                parameter('Артикул', product.article, '', '', true) +
                                parameter('Баркод', product.barcode, '', '', true) +
                                parameter('Магазин', product.store_name) +
                                parameter('Закупочная стоимость', values.purchase_price, ' ₽') +
                                parameter('Модель работы', 'FBY / FBS — общий расчёт'),
                        ) +
                        parameterGroup(
                            'Комиссии и налоги',
                            parametersFor([
                                'commission_percent',
                                'payment_acceptance',
                                'acquiring_percent',
                                'vat_percent',
                                'usn_percent',
                            ]) +
                                parameter('Комиссия по категории', categoryCommission.status === 'ok'
                                    ? categoryCommission.commission_percent : null, ' %') +
                                parameter('Комиссия категории проверена', categoryCommission.checked_at
                                    ? new Date(categoryCommission.checked_at).toLocaleString('ru-RU') : null) +
                                (categoryCommission.error
                                    ? parameter('Проверка комиссии', categoryCommission.error) : '') +
                                (categoryCommission.needs_campaign
                                    ? parameter('Комиссия категории', 'Различается между магазинами; выберите кампанию в параметрах товара') : ''),
                        ) +
                        parameterGroup(
                            'Продажи и реклама',
                            parametersFor(['seller_price', 'buyer_price', 'buyout_percent']) +
                                parameter('ДРР с выкупом · выбранный период', product.advertising.drr, '%') +
                                parameter('Реклама · выбранный период', product.advertising.spend, ' ₽'),
                        ) +
                        parameterGroup(
                            'Логистика',
                            parametersFor([
                                'delivery_cost',
                                'volume_l',
                                'return_middle_mile',
                                'return_cost',
                                'transit_cost',
                                'length',
                                'width',
                                'height',
                                'weight',
                            ]),
                        ) +
                        parameterGroup(
                            'Прочие расходы',
                            parametersFor([
                                'company_commission_percent',
                                'loss_percent',
                                'disposal_cost',
                            ]),
                        ),
                    scheme: scheme,
                    content_2: options.canManageSettings
                        ? window.CheckStockUI.render('economics/yandex/calculator/render-parameters', {
                              content: settingsUrl(),
                          })
                        : window.CheckStockUI.render('economics/yandex/calculator/render-parameters-2'),
                },
            );
        }
        function draw() {
            if (picker) picker.dispose();
            container.innerHTML = window.CheckStockUI.render('economics/yandex/calculator/draw-6', {
                content: expanded ? ' checked' : '',
                content_3: scheme === 'FBY' ? ' selected' : '',
                content_4: scheme === 'FBS' ? ' selected' : '',
                content_6: expanded ? ' is-expanded' : '',
                content_7: specs.map(function (spec) { return field(spec, false); }).join(''),
                content_8: window.CheckStockUI.render('economics/yandex/calculator/draw-2'),
            });
            var logistics = container.querySelector('[data-ym-logistics]');
            logistics.ontoggle = function () { logisticsOpen = logistics.open; };
            arrangeMode();
            renderParameters();
            showResult(data);
            picker = window.YandexCategoryPicker.mount(container.querySelector('[data-ym-categories]'),
                product.store_slug, changed.category_id || data.values.category_id, function (category) {
                    clearTimeout(timer);
                    sequence++;
                    categoryEdited = true;
                    tariffNeedsQuote = true;
                    pending();
                    container.querySelector('[data-ym-break-even]').disabled = true;
                    if (!category) {
                        message('Выберите конечную категорию ЯМ для расчёта комиссии.');
                        return;
                    }
                    changed.category_id = category.id;
                    changed.category_name = category.name;
                    delete manualTariffs.commission_percent;
                    delete changed.commission_percent;
                    preview(false);
                });
            container.querySelector('[data-ym-expanded]').onchange = function (event) {
                expanded = event.target.checked;
                arrangeMode();
            };
            container.querySelector('[data-ym-scheme]').onchange = function (event) {
                scheme = event.target.value;
                data.scheme = scheme;
                renderParameters();
                loadHistory();
            };
            container.querySelector('[data-ym-reset]').onclick = function () {
                load(scheme);
            };
            container.querySelectorAll('[data-ym-field]').forEach(function (input) {
                input.oninput = function () {
                    var key = input.dataset.ymField;
                    var value =
                        input.value === ''
                            ? null
                            : input.type === 'number'
                              ? Number(input.value)
                              : input.value;
                    if (key === 'commission_rub') {
                        var seller = container.querySelector('[data-ym-field="seller_price"]');
                        var price = seller.value === '' ? null : Number(seller.value);
                        input.setCustomValidity(value != null && !(price > 0)
                            ? 'Для комиссии в рублях задайте положительную цену без СПП.' : '');
                        // Rubles and percent are two views of the same commission, not two expenses.
                        // Send the unrounded rate so the entered amount is preserved to the kopeck.
                        value = value == null || !(price > 0) ? null : value / price * 100;
                        key = 'commission_percent';
                        container.querySelector('[data-ym-field="commission_percent"]').value = inputValue(key, value);
                    } else if (key === 'commission_percent' || key === 'seller_price') {
                        container.querySelector('[data-ym-field="commission_rub"]').setCustomValidity('');
                    }
                    changed[key] = value;
                    if (tariffFields.includes(key)) {
                        manualTariffs[key] = changed[key] != null;
                        if (changed[key] == null) tariffNeedsQuote = true;
                    }
                    if (quoteFields.includes(key)) tariffNeedsQuote = true;
                    if (key === 'plan_drr' || key === 'advertising_spend') {
                        delete changed[key === 'plan_drr' ? 'advertising_spend' : 'plan_drr'];
                        changed.advertising_mode = changed[key] == null ? 'weekly' : 'plan';
                    }
                    clearTimeout(timer);
                    sequence++;
                    pending();
                    timer = setTimeout(function () {
                        preview(false);
                    }, 350);
                };
            });
            container.querySelector('[data-ym-break-even]').onclick = function () {
                clearTimeout(timer);
                preview(true);
            };
            loadHistory();
        }
        function arrangeMode() {
            var inputs = container.querySelector('#ym-calculator-inputs');
            inputs.classList.toggle('is-expanded', expanded);
            var buyout = container.querySelector('[data-ym-field="buyout_percent"]').closest('.ue1c-calculator-row');
            // Move the existing control so switching modes keeps unsent edits and focus state.
            if (expanded) container.querySelector('[data-ym-logistics-fields]').prepend(buyout);
            else inputs.appendChild(buyout);
        }
        function showComputed(key, value) {
            var node = container.querySelector('[data-ym-computed="' + key + '"]');
            if (node) node.textContent = number(value) + (value == null ? '' : ' ₽');
        }
        function showLogistics(state) {
            var r = state.result, values = state.values, tariff = state.tariff || {};
            var logistics = r.logistics || {};
            showComputed('logistics-total', logistics.total);
            showComputed('logistics-returns', logistics.returns);
            container.querySelector('[data-ym-logistics]').classList.toggle('is-incomplete', logistics.total == null);
            var basis = JSON.stringify(['seller_price', 'category_id', 'length', 'width', 'height', 'weight']
                .map(function (key) { return values[key] == null ? null : values[key]; }));
            if ((tariff.valid || tariff.approximate) && (tariff.services || []).length) {
                tariffDetails = { basis: basis, services: tariff.services };
            }
            var amounts = { 'delivery-customer': 0, 'delivery-middle': 0, 'delivery-other': 0 };
            var types = {
                DELIVERY_TO_CUSTOMER: 'delivery-customer', MIDDLE_MILE: 'delivery-middle',
                CROSSREGIONAL_DELIVERY: 'delivery-other', EXPRESS_DELIVERY: 'delivery-other', SORTING: 'delivery-other',
            };
            var count = 0;
            if (tariffDetails && tariffDetails.basis === basis) tariffDetails.services.forEach(function (service) {
                var key = types[service.type], amount = Number(service.amount);
                if (key && service.amount != null && Number.isFinite(amount)) {
                    amounts[key] += amount;
                    count++;
                }
            });
            var total = Object.keys(amounts).reduce(function (sum, key) { return sum + amounts[key]; }, 0);
            var matched = count > 0 && values.delivery_cost != null && Math.abs(total - values.delivery_cost) < 0.005;
            Object.keys(amounts).forEach(function (key) {
                var node = container.querySelector('[data-ym-computed="' + key + '"]');
                node.closest('.ue1c-calculator-row').hidden = !matched || (key === 'delivery-other' && !amounts[key]);
                showComputed(key, matched ? amounts[key] : null);
            });
            container.querySelector('[data-ym-delivery-note]').textContent = matched
                ? 'Состав доставки по тарифу ЯМ. Эти суммы уже входят в доставку выкупленного товара выше.'
                : values.delivery_cost == null ? 'Не задана стоимость доставки выкупленного товара.'
                : 'Сумма доставки задана без соответствующей детализации API. Разбивка на доставку покупателю и среднюю милю неизвестна.';
        }
        function pending() {
            container.querySelector('[data-ym-result]').innerHTML =
                metric('Чистая прибыль', null, ' ₽') +
                metric('ROI', null, '%');
            parameters.querySelector('[data-ym-costs]').innerHTML = '';
            container.querySelectorAll('[data-ym-computed]').forEach(function (node) { node.textContent = '—'; });
            message('Пересчитываем…');
        }
        function showResult(state) {
            var r = state.result,
                period =
                    product.ym_economics.scheme === scheme
                        ? product.ym_economics.period || {}
                        : data.period || {},
                coverage = period.coverage || {};
            showLogistics(state);
            specs.forEach(function (spec) {
                var key = spec[0];
                var input = container.querySelector('[data-ym-field="' + key + '"]');
                if (!input) return;
                var value = inputValue(key, key === 'commission_rub' ? r.commission_rub : state.values[key]);
                if (input.value !== value) input.value = value;
                if (key === 'commission_rub') {
                    var sellerPrice = state.values.seller_price;
                    input.max = sellerPrice > 0 ? String(sellerPrice) : '1000000000';
                    input.disabled = !(sellerPrice > 0);
                    input.title = sellerPrice > 0 ? fields.hints.commission_rub
                        : 'Сначала задайте положительную цену без СПП.';
                    input.setCustomValidity('');
                }
            });
            var periodDays = coverage.expected_days || 7;
            container.querySelector('[data-ym-result]').innerHTML =
                metric('Чистая прибыль', r.margin, ' ₽') +
                metric('ROI', r.roi, '%');
            parameters.querySelector('[data-ym-costs]').innerHTML = Object.keys(r.costs || {})
                .map(function (key) {
                    return window.CheckStockUI.render('economics/yandex/calculator/show-result', {
                        content: costs[key] || key,
                        content_2: number(r.costs[key]),
                    });
                })
                .join('');
            var periodElement = parameters.querySelector('[data-ym-period]');
            periodElement.classList.toggle('is-incomplete', coverage.days > 0 && !coverage.complete);
            var coveredDates = (coverage.dates || []).map(function (day) {
                return day.split('-').reverse().join('.');
            });
            periodElement.innerHTML = window.CheckStockUI.render(
                'economics/yandex/calculator/show-result-2',
                {
                    periodDays: periodDays,
                    scheme: scheme,
                    margin: number(period.margin) + (period.margin == null ? '' : ' ₽'),
                    roi: number(period.roi) + (period.roi == null ? '' : '%'),
                    coveredDays: coverage.days || 0,
                    coverageStatus: coverage.complete ? '' : ' · история ещё не полная',
                    coverageDates: coveredDates.length
                        ? 'Учтены дни: ' + coveredDates.join(', ')
                        : 'Нет сохранённых расчётов за завершённые дни.',
                },
            );
            parameters.querySelector('[data-ym-costs-title]').textContent = 'Расходы по расчёту';
            parameters.querySelector('[data-ym-diagnostics]').textContent = r.messages.join(' ');
            message(
                r.messages.length
                    ? 'Не хватает данных для расчёта. Подробности — во вкладке «Параметры».'
                    : '',
                !!r.messages.length,
            );
            var weekly = state.calculator_advertising || {},
                manual = state.values.advertising_mode === 'plan';
            container.querySelector('[data-ym-field="advertising_spend"]').value =
                inputValue('advertising_spend', state.values.advertising_spend);
            container.querySelector('.ym-drr-row').classList.toggle('is-incomplete',
                !manual && (!weekly.complete || weekly.drr == null));
            container.querySelector('.ym-ad-row').classList.toggle('is-incomplete',
                !manual && !weekly.advertising_complete);
            if (!manual && weekly.message) parameters.querySelector('[data-ym-diagnostics]').textContent =
                r.messages.filter(function (text) { return text !== 'Не задано: ДРР с выкупом'; }).concat(weekly.message).join(' ');
        }
        async function load(nextScheme) {
            clearTimeout(timer);
            var current = ++sequence;
            pending();
            container.querySelectorAll('[data-ym-field]').forEach(function (input) {
                input.disabled = true;
            });
            container.querySelector('[data-ym-break-even]').disabled = true;
            if (picker) picker.disable();
            try {
                var result = await request('calculate/' + productPath, 'POST', {
                    scheme: nextScheme,
                    mode: 'calculator',
                    values: {},
                });
                if (alive && current === sequence) {
                    data = result.economics;
                    scheme = nextScheme;
                    changed = {};
                    categoryEdited = false;
                    tariffNeedsQuote = false;
                    manualTariffs = {};
                    draw();
                }
            } catch (error) {
                if (alive && current === sequence) {
                    draw();
                    message(error.message, true);
                }
            }
        }
        async function preview(breakEven) {
            var current = ++sequence;
            pending();
            if (categoryEdited && !picker.complete()) {
                container.querySelector('[data-ym-break-even]').disabled = true;
                message('Выберите конечную категорию ЯМ для расчёта комиссии.');
                return;
            }
            var invalid = Array.from(container.querySelectorAll('[data-ym-field]')).find(function (input) {
                return !input.checkValidity();
            });
            if (invalid) {
                message('Проверьте значение: ' + invalid.getAttribute('aria-label'), true);
                return;
            }
            try {
                if (tariffNeedsQuote) {
                    container.querySelector('[data-ym-break-even]').disabled = true;
                    picker.status('Пересчитываем тарифы…');
                    var quoteValues = Object.assign({}, changed);
                    tariffFields.forEach(function (key) {
                        if (!manualTariffs[key] || (categoryEdited && key === 'commission_percent')) delete quoteValues[key];
                    });
                    var quotePayload = { scheme: scheme, mode: 'calculator', values: quoteValues };
                    if (!categoryEdited) quotePayload.refresh_tariffs = true;
                    var quoted = await request((categoryEdited ? 'category-tariff/' : 'calculate/') + productPath,
                        'POST', quotePayload);
                    if (!alive || current !== sequence) return;
                    var manualCommission = categoryEdited && manualTariffs.commission_percent
                        ? changed.commission_percent : null;
                    if (categoryEdited) {
                        changed = quoted.economics.category_scenario;
                    } else {
                        // Keep this quote in the scenario for subsequent non-tariff edits.
                        // A later dimension/price change removes automatic values before quoting again.
                        tariffFields.forEach(function (key) {
                            if (!manualTariffs[key]) changed[key] = quoted.economics.values[key];
                        });
                    }
                    if (manualCommission != null) changed.commission_percent = manualCommission;
                    tariffNeedsQuote = false;
                    picker.status('');
                    if (!breakEven && manualCommission == null) {
                        showResult(quoted.economics);
                        container.querySelector('[data-ym-break-even]').disabled = false;
                        return;
                    }
                }
                var result = await request('calculate/' + productPath, 'POST', {
                    scheme: scheme,
                    mode: 'calculator',
                    values: changed,
                    break_even: breakEven,
                });
                if (alive && current === sequence) {
                    if (breakEven) changed = result.economics.break_even_scenario;
                    showResult(result.economics);
                    container.querySelector('[data-ym-break-even]').disabled = false;
                    if (breakEven) message('Цена без убытка подставлена.');
                }
            } catch (error) {
                if (alive && current === sequence) {
                    message(error.message, true);
                    if (tariffNeedsQuote) {
                        picker.status('Не удалось пересчитать тарифы. Проверьте параметры и повторите изменение.');
                        container.querySelector('[data-ym-break-even]').disabled = true;
                    }
                }
            }
        }
        function showHistory(result) {
            var rows = result.history || [];
            options.onHistory(result.chart || []);
            options.historyContainer.innerHTML = window.CheckStockUI.render(
                'economics/yandex/calculator/show-history-4',
                {
                    content: rows.length
                        ? window.CheckStockUI.render('economics/yandex/calculator/show-history-2', {
                              content: rows
                                  .slice()
                                  .sort(function (a, b) {
                                      return b.day.localeCompare(a.day);
                                  })
                                  .map(function (row) {
                                      return window.CheckStockUI.render(
                                          'economics/yandex/calculator/show-history',
                                          {
                                              day: row.day,
                                              orders_count: number(row.orders_count),
                                              expected_buyouts: number(row.expected_buyouts),
                                              advertising_spend: number(row.advertising_spend),
                                              profit: number(row.profit),
                                          },
                                      );
                                  })
                                  .join(''),
                          })
                        : window.CheckStockUI.render('economics/yandex/calculator/show-history-3'),
                },
            );
        }
        async function loadHistory() {
            var current = ++historySequence,
                currentScheme = scheme;
            if (historyCache[scheme]) {
                showHistory(historyCache[scheme]);
                return;
            }
            options.onHistory([], 'Загружаем историю…');
            options.historyContainer.innerHTML = '';
            try {
                var result = await request('economics-history/' + productPath + '?scheme=' + scheme);
                if (!alive || current !== historySequence || scheme !== currentScheme) return;
                historyCache[scheme] = result;
                showHistory(historyCache[scheme]);
            } catch (error) {
                if (!alive || current !== historySequence) return;
                options.onHistory([], error.message);
                options.historyContainer.innerHTML = window.CheckStockUI.render(
                    'economics/yandex/calculator/load-history',
                );
                options.historyContainer.querySelector('[data-ym-history-retry]').onclick = loadHistory;
            }
        }
        draw();
        if (!initial.calculator_result && initial.mode !== 'calculator') load(scheme);
    }
    window.YandexEconomics = { render: render };
})();
