(function () {
    'use strict';
    var fields = window.YandexEconomicsFields;
    var esc = fields.esc,
        number = fields.number,
        request = fields.request;
    var compact = [
        'seller_price',
        'buyer_price',
        'plan_drr',
        'buyout_percent',
        'fulfillment_cost',
        'purchase_price',
    ];
    var specs = [].concat.apply(
        [],
        fields.groups.slice(0, 4).map(function (g) {
            return g[1];
        }),
    );
    var costs = {
        commission: 'Размещение',
        payment_acceptance: 'Приём платежа',
        payment_transfer: 'Перевод платежа',
        delivery: 'Доставка',
        returns: 'Невыкупы и возвраты',
        storage: 'Хранение',
        transit: 'Транзит',
        purchase: 'Закупка',
        fulfillment: 'Фулфилмент',
        other: 'Прочие',
        tax: 'Налог',
        capital: 'Стоимость капитала',
        loss: 'Потери',
        disposal: 'Утилизация',
        advertising: 'Реклама',
        tariff_extra: 'Другие услуги ЯМ',
    };
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
        var data = product.ym_economics;
        if (!data) {
            container.textContent = 'Данные экономики недоступны. Обновите страницу.';
            return;
        }
        container.classList.add('ym-economics');
        var scheme = data.scheme || 'FBY',
            mode = 'current',
            changed = {},
            sequence = 0,
            timer,
            alive = true,
            expanded = false;
        var historyCache = {},
            historySequence = 0,
            parameters = options.parameters;
        var productPath = encodeURIComponent(product.store_slug) + '/' + encodeURIComponent(product.article);
        container._ymDispose = function () {
            alive = false;
            clearTimeout(timer);
            sequence++;
            historySequence++;
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
        function field(spec, index) {
            var key = spec[0],
                value = data.values[key],
                label = spec[1],
                order = compact.indexOf(key);
            if (key === 'plan_drr')
                label = mode === 'current' ? 'ДРР с выкупом · сегодня' : 'Плановый ДРР с выкупом';
            var control,
                disabled =
                    mode === 'current' || (key === 'plan_drr' && data.values.advertising_mode !== 'plan');
            if (spec[3] && typeof spec[3] === 'object') {
                control = window.CheckStockUI.render('economics/yandex/calculator/field-2', {
                    key: key,
                    label: label,
                    content: disabled ? ' disabled' : '',
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
                if (key === 'plan_drr' && data.values.advertising_mode !== 'plan') {
                    value =
                        data.result.costs.advertising != null && data.values.seller_price > 0
                            ? (data.result.costs.advertising / data.values.seller_price) * 100
                            : null;
                }
                control = window.CheckStockUI.render('economics/yandex/calculator/field-3', {
                    key: key,
                    label: label,
                    content: spec[2] === '%' ? ' max="100"' : '',
                    content_2: disabled ? ' disabled' : '',
                    value: value,
                    content_3: spec[2],
                });
            }
            return window.CheckStockUI.render('economics/yandex/calculator/field-4', {
                content: order < 0 ? ' ue1c-calculator-row--expanded' : '',
                order: order,
                index: index,
                content_2: data.origins[key] || 'Не задано',
                label: label,
                key: key,
                content_3: data.origins[key] || 'Не задано',
                control: control,
            });
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
                                parameter('Закупочная цена', values.purchase_price, ' ₽') +
                                parameter('Модель работы', scheme),
                        ) +
                        parameterGroup(
                            'Комиссии и налоги',
                            parametersFor([
                                'commission_percent',
                                'payment_acceptance',
                                'payment_transfer_percent',
                                'tax_base',
                                'tax_percent',
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
                                'fulfillment_cost',
                                'delivery_cost',
                                'return_cost',
                                'transit_cost',
                                'length',
                                'width',
                                'height',
                                'weight',
                            ]),
                        ) +
                        parameterGroup('Хранение', parametersFor(['storage_per_day', 'storage_days'])) +
                        parameterGroup(
                            'Прочие расходы',
                            parametersFor([
                                'other_percent',
                                'other_cost',
                                'capital_percent',
                                'turnover_days',
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
            container.innerHTML = window.CheckStockUI.render('economics/yandex/calculator/draw-6', {
                content: expanded ? ' checked' : '',
                content_2: ['current', 'calculator']
                    .map(function (key) {
                        return window.CheckStockUI.render('economics/yandex/calculator/draw', {
                            content: mode === key ? 'is-active' : '',
                            key: key,
                            content_2: mode === key,
                            content_3: key === 'current' ? 'Текущая экономика' : 'Калькулятор',
                        });
                    })
                    .join(''),
                content_3: scheme === 'FBY' ? ' selected' : '',
                content_4: scheme === 'FBS' ? ' selected' : '',
                content_5:
                    mode === 'current'
                        ? 'Цены и реклама — за сегодня (МСК). Закупка и расходы — из действующих настроек.'
                        : 'Пробный расчёт. Изменения полей действуют только в этом сценарии.',
                content_6: expanded ? ' is-expanded' : '',
                content_7: specs.map(field).join(''),
                content_8:
                    mode === 'calculator'
                        ? window.CheckStockUI.render('economics/yandex/calculator/draw-2')
                        : window.CheckStockUI.render('economics/yandex/calculator/draw-3'),
                content_9: options.canManageSettings
                    ? window.CheckStockUI.render('economics/yandex/calculator/draw-4', {
                          content: settingsUrl(),
                      })
                    : window.CheckStockUI.render('economics/yandex/calculator/draw-5'),
            });
            renderParameters();
            showResult(data);
            container.querySelector('[data-ym-expanded]').onchange = function (event) {
                expanded = event.target.checked;
                container.querySelector('#ym-calculator-inputs').classList.toggle('is-expanded', expanded);
            };
            container.querySelector('[data-ym-scheme]').onchange = function (event) {
                load(mode, event.target.value);
            };
            container.querySelectorAll('[data-ym-mode]').forEach(function (button) {
                button.onclick = function () {
                    load(button.dataset.ymMode, scheme);
                };
            });
            container.querySelector('[data-ym-reset]').onclick = function () {
                load(mode, scheme);
            };
            container.querySelectorAll('[data-ym-field]').forEach(function (input) {
                input.oninput = function () {
                    var key = input.dataset.ymField;
                    changed[key] =
                        input.value === ''
                            ? null
                            : input.type === 'number'
                              ? Number(input.value)
                              : input.value;
                    container.querySelector('[data-ym-origin="' + key + '"]').textContent = 'Сценарий';
                    if (key === 'advertising_mode') {
                        var drr = container.querySelector('[data-ym-field="plan_drr"]');
                        drr.disabled = input.value !== 'plan';
                        drr.value =
                            changed.plan_drr != null
                                ? changed.plan_drr
                                : data.values.plan_drr == null
                                  ? ''
                                  : data.values.plan_drr;
                    }
                    clearTimeout(timer);
                    sequence++;
                    pending();
                    timer = setTimeout(function () {
                        preview(false);
                    }, 350);
                };
            });
            var quote = container.querySelector('[data-ym-quote]');
            if (quote)
                quote.onclick = function () {
                    clearTimeout(timer);
                    preview(true);
                };
            var calculatorButton = container.querySelector('[data-ym-calculator]');
            if (calculatorButton)
                calculatorButton.onclick = function () {
                    load('calculator', scheme);
                };
            loadHistory();
        }
        function pending() {
            container.querySelector('[data-ym-result]').innerHTML =
                metric('Чистая прибыль', null, ' ₽') +
                metric('ROI', null, '%') +
                metric('Маржинальность', null, '%');
            parameters.querySelector('[data-ym-costs]').innerHTML = '';
            message('Пересчитываем…');
        }
        function showResult(state) {
            var r = state.result,
                period =
                    product.ym_economics.scheme === scheme
                        ? product.ym_economics.period || {}
                        : data.period || {},
                coverage = period.coverage || {};
            var periodDays = coverage.expected_days || 7;
            container.querySelector('[data-ym-result]').innerHTML =
                metric('Чистая прибыль', r.margin, ' ₽') +
                metric('ROI', r.roi, '%') +
                metric('Маржинальность', r.margin_percent, '%');
            parameters.querySelector('[data-ym-costs]').innerHTML = Object.keys(r.costs || {})
                .map(function (key) {
                    return window.CheckStockUI.render('economics/yandex/calculator/show-result', {
                        content: costs[key] || key,
                        content_2: number(r.costs[key]),
                    });
                })
                .join('');
            parameters.querySelector('[data-ym-period]').innerHTML = window.CheckStockUI.render(
                'economics/yandex/calculator/show-result-2',
                {
                    periodDays: periodDays,
                    scheme: scheme,
                    margin: number(period.margin),
                    roi: number(period.roi),
                    content: coverage.days || 0,
                    periodDays_2: periodDays,
                    content_2: coverage.complete ? '' : ' · история ещё не полная',
                },
            );
            parameters.querySelector('[data-ym-costs-title]').textContent =
                'Расходы · ' + (mode === 'calculator' ? 'сценарий' : 'текущая экономика');
            parameters.querySelector('[data-ym-diagnostics]').textContent = r.messages.join(' ');
            message(
                r.messages.length
                    ? 'Не хватает данных для расчёта. Подробности — во вкладке «Параметры».'
                    : mode === 'calculator'
                      ? 'Сценарий рассчитан. Тариф API — оценка; при изменении цены пересчитайте тариф.'
                      : 'Действующие параметры · ' + state.advertising_day + ' (МСК)',
                !!r.messages.length,
            );
            var drr = container.querySelector('[data-ym-field="plan_drr"]');
            if (state.values.advertising_mode !== 'plan' && drr) {
                drr.value =
                    r.costs.advertising != null && state.values.seller_price > 0
                        ? Number(((r.costs.advertising / state.values.seller_price) * 100).toFixed(2))
                        : '';
            }
        }
        async function load(nextMode, nextScheme) {
            clearTimeout(timer);
            var current = ++sequence;
            pending();
            container.querySelectorAll('[data-ym-field]').forEach(function (input) {
                input.disabled = true;
            });
            try {
                var result = await request('calculate/' + productPath, 'POST', {
                    scheme: nextScheme,
                    mode: nextMode,
                    values: {},
                });
                if (alive && current === sequence) {
                    data = result.economics;
                    mode = nextMode;
                    scheme = nextScheme;
                    changed = {};
                    draw();
                }
            } catch (error) {
                if (alive && current === sequence) {
                    draw();
                    message(error.message, true);
                }
            }
        }
        async function preview(quote) {
            var current = ++sequence;
            pending();
            var invalid = Array.from(container.querySelectorAll('[data-ym-field]')).find(function (input) {
                return !input.checkValidity();
            });
            if (invalid) {
                message('Проверьте значение: ' + invalid.getAttribute('aria-label'), true);
                return;
            }
            try {
                var result = await request('calculate/' + productPath, 'POST', {
                    scheme: scheme,
                    mode: 'calculator',
                    values: changed,
                    refresh_tariffs: quote,
                });
                if (alive && current === sequence) showResult(result.economics);
            } catch (error) {
                if (alive && current === sequence) message(error.message, true);
            }
        }
        function showHistory(rows) {
            options.onHistory(rows, data.advertising_day);
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
                options.historyContainer.innerHTML = window.CheckStockUI.render(
                    'economics/yandex/calculator/load-history',
                );
                options.historyContainer.querySelector('[data-ym-history-retry]').onclick = loadHistory;
            }
        }
        draw();
    }
    window.YandexEconomics = { render: render };
})();
