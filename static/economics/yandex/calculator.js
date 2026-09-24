(function () {
    'use strict';
    var fields = window.YandexEconomicsFields;
    var esc = fields.esc,
        number = fields.number,
        request = fields.request;
    var compact = [
        'seller_price',
        'buyer_price',
        'spp_percent',
        'pay_price',
        'pay_discount_percent',
        'plan_drr',
        'buyout_percent',
        'advertising_per_buyout',
        'purchase_price',
        'fulfillment_cost',
    ];
    // Common fields follow the WB calculator; YM logistics stay beside delivery.
    var expandedOrder = [
        'seller_price',
        'buyer_price',
        'spp_percent',
        'pay_price',
        'pay_discount_percent',
        'category_name',
        'commission_percent',
        'commission_rub',
        'logistics_total',
        'delivery_cost',
        'length',
        'width',
        'height',
        'weight',
        'volume_l',
        'delivery_customer', 'middle_mile', 'delivery_other', 'logistics_returns', 'repeat_delivery',
        'return_cost',
        'transit_cost',
        'plan_drr',
        'buyout_percent',
        'advertising_per_buyout',
        'acquiring_percent',
        'payment_acceptance',
        'purchase_price',
        'fulfillment_cost',
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
    ).concat([['spp_percent', 'СПП', '%'],
        ['pay_discount_percent', 'Скидка Яндекс Пэй', '%'],
        ['commission_rub', 'Комиссия YM, руб', '₽'],
        ['logistics_total', 'Логистика на один выкуп', '₽'],
        ['delivery_customer', 'Доставка покупателю', '₽'],
        ['middle_mile', 'Средняя миля', '₽'],
        ['delivery_other', 'Другие услуги доставки', '₽'],
        ['logistics_returns', 'Невыкупы с учётом процента выкупа', '₽'],
        ['repeat_delivery', 'Повторная доставка', '₽']]).filter(function (spec) {
        return !['campaign_id', 'frequency', 'payment_delay_weeks', 'return_middle_mile'].includes(spec[0]);
    }).sort(function (a, b) {
        return expandedOrder.indexOf(a[0]) - expandedOrder.indexOf(b[0]);
    });
    var tariffFields = ['commission_percent', 'payment_acceptance', 'delivery_cost', 'delivery_customer', 'middle_mile', 'delivery_other'];
    var quoteFields = ['seller_price', 'length', 'width', 'height', 'weight'];
    var priceFields = ['seller_price', 'buyer_price', 'pay_price'];
    var logisticsFields = ['logistics_total', 'length', 'width', 'height', 'weight', 'volume_l',
        'delivery_customer', 'middle_mile', 'delivery_other', 'logistics_returns', 'repeat_delivery', 'return_cost', 'transit_cost'];
    var costs = {
        commission: 'Комиссия YM',
        payment_acceptance: 'Приём платежа (Экваиринг 2)',
        acquiring: 'Перевод платежа (Экваринг1)',
        delivery: 'Доставка выкупленного товара',
        logistics: 'Логистика на один выкуп (вручную)',
        repeat_delivery: 'Повторная доставка',
        returns: 'Невыкупы и возвраты',
        transit: 'Транзит',
        purchase: 'Закупочная стоимость',
        fulfillment: 'Затраты на ФФ',
        company_commission: 'Комиссия компании',
        vat: 'Налог НДС, руб',
        usn: 'Налог УСН, руб',
        loss: 'Потери от закупочной цены',
        disposal: 'Утилизация',
        advertising: 'Реклама',
    };
    function inputValue(key, value) {
        if (value == null) return '';
        return key === 'commission_percent' || key === 'commission_rub' || key === 'advertising_per_buyout'
            ? Number(value).toFixed(2) : String(value);
    }
    function priceFactor(base, discounted, percent) {
        if (Number.isFinite(base) && base > 0 && Number.isFinite(discounted) && discounted > 0 && discounted <= base)
            return discounted / base;
        return Number.isFinite(percent) && percent >= 0 && percent < 100 ? 1 - percent / 100 : null;
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
        var priceFactors, linkedPrices = {}, priceSource = 'seller_price';
        var logisticsOpen = false;
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
        function syncLinkedPrices(source, value) {
            if (priceFields.includes(source)) priceSource = source;
            if (!priceFields.includes(source) || !Number.isFinite(value) || value < 0) return false;
            delete linkedPrices[source];
            var sellerChanged = false;
            function setPrice(key, next) {
                var input = container.querySelector('[data-ym-field="' + key + '"]');
                next = next == null ? null : Math.round((next + Number.EPSILON) * 100) / 100;
                if (key === 'seller_price' && input.value !== inputValue(key, next)) sellerChanged = true;
                input.value = inputValue(key, next);
                changed[key] = next;
                linkedPrices[key] = true;
                return next;
            }
            var buyer;
            if (source === 'seller_price') {
                buyer = setPrice('buyer_price', priceFactors.spp == null ? null : value * priceFactors.spp);
                setPrice('pay_price', buyer == null || priceFactors.pay == null ? null : buyer * priceFactors.pay);
            } else if (source === 'buyer_price') {
                if (priceFactors.spp != null) setPrice('seller_price', value / priceFactors.spp);
                setPrice('pay_price', priceFactors.pay == null ? null : value * priceFactors.pay);
            } else if (priceFactors.pay != null) {
                buyer = setPrice('buyer_price', value / priceFactors.pay);
                if (priceFactors.spp != null) setPrice('seller_price', buyer / priceFactors.spp);
            }
            return sellerChanged;
        }
        function logisticsBlock() {
            function input(key) {
                return field(specs.find(function (spec) { return spec[0] === key; }), true);
            }
            return window.CheckStockUI.render('economics/yandex/calculator/logistics', {
                index: expandedOrder.indexOf('delivery_cost'),
                open: logisticsOpen ? ' open' : '',
                content: input('length') + input('volume_l') + input('delivery_cost') +
                    input('delivery_customer') + input('middle_mile') + input('delivery_other') +
                    input('return_cost') + input('logistics_returns') + input('repeat_delivery') + input('transit_cost'),
            });
        }
        function field(spec, inLogistics) {
            var key = spec[0],
                index = expandedOrder.indexOf(key),
                value = data.values[key],
                label = spec[1],
                order = compact.indexOf(key);
            if (key === 'spp_percent' || key === 'pay_discount_percent') {
                return window.CheckStockUI.render('economics/yandex/calculator/discount-row', {
                    key: key, label: label, order: order, index: index,
                });
            }
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
                order: compact.indexOf('advertising_per_buyout'), index: expandedOrder.indexOf('advertising_per_buyout'),
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
            var definitions = specs;
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
                                parameter('Затраты на ФФ', values.fulfillment_cost, ' ₽', origins.fulfillment_cost) +
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
                                'delivery_customer', 'middle_mile', 'delivery_other',
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
            // Keep the same discounts throughout a scenario, including repeated and reverse edits.
            var pricing = data.pricing || {};
            priceFactors = {
                spp: priceFactor(data.values.seller_price, data.values.buyer_price, pricing.spp_percent),
                pay: priceFactor(data.values.buyer_price, data.values.pay_price, pricing.pay_discount_percent),
            };
            linkedPrices = {};
            priceSource = 'seller_price';
            container.innerHTML = window.CheckStockUI.render('economics/yandex/calculator/draw-6', {
                content: expanded ? ' checked' : '',
                content_3: scheme === 'FBY' ? ' selected' : '',
                content_4: scheme === 'FBS' ? ' selected' : '',
                content_6: expanded ? ' is-expanded' : '',
                content_7: specs.map(function (spec) { return field(spec, false); }).join(''),
                content_8: window.CheckStockUI.render('economics/yandex/calculator/draw-2') +
                    (options.canEdit ? window.CheckStockUI.render('economics/yandex/calculator/save-price') : ''),
            });
            var savePrice = container.querySelector('[data-ym-save-price]');
            if (savePrice) savePrice.onclick = async function () {
                var seller = container.querySelector('[data-ym-field="seller_price"]');
                var source = container.querySelector('[data-ym-field="' + priceSource + '"]');
                if (!source.checkValidity() || !(Number(source.value) > 0) ||
                    !seller.checkValidity() || !(Number(seller.value) > 0)) {
                    message('Укажите положительную цену товара.', true);
                    return;
                }
                if ((priceSource !== 'seller_price' && priceFactors.spp == null) ||
                    (priceSource === 'pay_price' && priceFactors.pay == null)) {
                    message('Скидка площадки неизвестна. Для отправки задайте цену без СПП.', true);
                    return;
                }
                savePrice.disabled = true;
                savePrice.textContent = 'Проверяем…';
                try {
                    await options.onSavePrice(Number(seller.value));
                } catch (error) {
                    if (alive) message(error.message || 'Не удалось проверить цену ЯМ', true);
                } finally {
                    savePrice.disabled = false;
                    savePrice.textContent = 'Сохранить цену';
                }
            };
            var logistics = container.querySelector('[data-ym-logistics]');
            logistics.querySelector('input').onclick = function (event) { event.stopPropagation(); };
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
                    } else if (key === 'commission_percent' || priceFields.includes(key)) {
                        container.querySelector('[data-ym-field="commission_rub"]').setCustomValidity('');
                    }
                    changed[key] = value;
                    if (syncLinkedPrices(key, value)) tariffNeedsQuote = true;
                    if (['delivery_customer', 'middle_mile', 'delivery_other'].includes(key)) {
                        delete changed.delivery_cost;
                        delete manualTariffs.delivery_cost;
                    }
                    if (key === 'middle_mile') delete changed.return_cost;
                    if (['middle_mile', 'return_cost', 'buyout_percent'].includes(key)) delete changed.logistics_returns;
                    if (['delivery_cost', 'delivery_customer', 'middle_mile', 'delivery_other', 'buyout_percent'].includes(key)) delete changed.repeat_delivery;
                    if (key !== 'logistics_total' && (logisticsFields.includes(key) || ['delivery_cost', 'buyout_percent'].includes(key))) delete changed.logistics_total;
                    if (tariffFields.includes(key)) {
                        manualTariffs[key] = changed[key] != null;
                        if (changed[key] == null) tariffNeedsQuote = true;
                    }
                    if (quoteFields.includes(key)) tariffNeedsQuote = true;
                    if (key === 'plan_drr' || key === 'advertising_per_buyout') {
                        delete changed[key === 'plan_drr' ? 'advertising_per_buyout' : 'plan_drr'];
                        changed.advertising_mode = changed[key] == null ? 'weekly' : 'plan';
                    }
                    clearTimeout(timer);
                    sequence++;
                    previewInBrowser();
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
            if (expanded) container.querySelector('.ym-dimensions').after(buyout);
            else inputs.appendChild(buyout);
        }
        function showLogistics(state) {
            var logistics = state.result.logistics || {};
            ['logistics_total', 'logistics_returns', 'repeat_delivery'].forEach(function (key) {
                var value = logistics[{logistics_total: 'total', logistics_returns: 'returns', repeat_delivery: 'repeat_delivery'}[key]];
                container.querySelector('[data-ym-field="' + key + '"]').value = inputValue(key, value);
            });
            container.querySelector('[data-ym-logistics]').classList.toggle('is-incomplete', logistics.total == null);
            container.querySelector('[data-ym-logistics-note]').textContent = state.values.logistics_total != null
                ? 'Итог задан вручную. Измените составляющие или очистите итог, чтобы вернуться к формуле.'
                : 'Итого: доставка + невыкупы + повторная доставка + транзит. Повторная доставка учитывается один раз для доли невыкупов.';
            if (state.values.delivery_cost != null && changed.delivery_cost != null)
                container.querySelector('[data-ym-logistics-note]').textContent += ' Доставка задана вручную; её составляющие ниже сохранены из тарифа. Изменение составляющей возвращает доставку к их сумме.';
        }
        function pending() {
            container.querySelector('[data-ym-result]').innerHTML =
                metric('Чистая прибыль', null, ' ₽') +
                metric('ROI', null, '%');
            parameters.querySelector('[data-ym-costs]').innerHTML = '';
            container.querySelectorAll('[data-ym-computed]').forEach(function (node) { node.textContent = '—'; });
            message('Пересчитываем…');
        }
        function previewInBrowser() {
            var values = Object.assign({}, data.values, changed);
            function amount(key) {
                var value = values[key];
                return value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
            }
            var price = amount('seller_price'), buyer = amount('buyer_price');
            var purchase = amount('purchase_price'), buyout = amount('buyout_percent');
            var delivery = amount('delivery_cost');
            if (['delivery_customer', 'middle_mile', 'delivery_other'].some(function (key) {
                return Object.prototype.hasOwnProperty.call(changed, key);
            })) {
                var parts = ['delivery_customer', 'middle_mile', 'delivery_other'].map(amount);
                delivery = parts.every(function (part) { return part != null; })
                    ? parts.reduce(function (sum, part) { return sum + part; }, 0) : null;
            }
            var returns = amount('return_cost');
            if (Object.prototype.hasOwnProperty.call(changed, 'middle_mile')) {
                returns = amount('middle_mile') == null ? null : amount('middle_mile') + 15;
            }
            var ratio = buyout == null ? null : buyout / 100;
            var logisticsChanged = logisticsFields.some(function (key) {
                return Object.prototype.hasOwnProperty.call(changed, key);
            }) || Object.prototype.hasOwnProperty.call(changed, 'buyout_percent');
            var logistics = !logisticsChanged || changed.logistics_total != null
                ? amount('logistics_total') : null;
            if (logistics == null && changed.logistics_total == null) {
                var returnCharge = amount('logistics_returns');
                if (!Object.prototype.hasOwnProperty.call(changed, 'logistics_returns') &&
                    (returnCharge == null || changed.buyout_percent != null ||
                        changed.middle_mile != null || changed.return_cost != null)) {
                    returnCharge = returns == null || ratio == null ? null : returns * (1 - ratio);
                }
                var repeat = amount('repeat_delivery');
                if (!Object.prototype.hasOwnProperty.call(changed, 'repeat_delivery') &&
                    (repeat == null || changed.buyout_percent != null || changed.delivery_cost != null ||
                    changed.delivery_customer != null || changed.middle_mile != null || changed.delivery_other != null)) {
                    repeat = delivery == null || ratio == null ? null : delivery * (1 - ratio);
                }
                var transit = amount('transit_cost');
                logistics = [delivery, returnCharge, repeat, transit].every(function (item) {
                    return item != null;
                }) ? delivery + returnCharge + repeat + transit : null;
            }
            var advertising;
            if (values.advertising_mode === 'plan' && values.plan_drr != null &&
                changed.advertising_per_buyout == null && changed.advertising_spend == null &&
                (changed.plan_drr != null || data.values.advertising_basis === 'drr')) {
                advertising = price == null || ratio == null ? null : price * amount('plan_drr') / 100 * ratio;
            } else if (values.advertising_mode === 'plan' && amount('advertising_per_buyout') != null) {
                advertising = amount('advertising_per_buyout');
            } else if (changed.advertising_spend != null) {
                var bought = (data.calculator_advertising || {}).orders_count * ratio;
                advertising = bought > 0 ? amount('advertising_spend') / bought : null;
            } else {
                var weekly = data.calculator_advertising || {};
                var expected = weekly.orders_count * ratio;
                advertising = weekly.spend == null || ratio == null ? amount('advertising_per_buyout')
                    : expected > 0 ? weekly.spend / expected : weekly.spend === 0 ? 0 : null;
            }
            var vatRate = amount('vat_percent');
            var vat = buyer == null || vatRate == null ? null : buyer * vatRate / (100 + vatRate);
            var usnRate = amount('usn_percent');
            var charges = [
                price == null || amount('commission_percent') == null ? null : price * amount('commission_percent') / 100,
                amount('payment_acceptance'),
                price == null || amount('acquiring_percent') == null ? null : price * amount('acquiring_percent') / 100,
                logistics,
                purchase,
                amount('fulfillment_cost'),
                price == null || amount('company_commission_percent') == null ? null : price * amount('company_commission_percent') / 100,
                vat,
                buyer == null || vat == null || usnRate == null ? null : (buyer - vat) * usnRate / 100,
                purchase == null || amount('loss_percent') == null ? null : purchase * amount('loss_percent') / 100,
                amount('disposal_cost') == null || amount('loss_percent') == null
                    ? null : amount('disposal_cost') * amount('loss_percent') / 100,
                advertising,
            ];
            var margin = price != null && charges.every(function (item) { return item != null; })
                ? price - charges.reduce(function (sum, item) { return sum + item; }, 0) : null;
            container.querySelector('[data-ym-result]').innerHTML =
                metric('Чистая прибыль · предварительно', margin, ' ₽') +
                metric('ROI · предварительно', margin != null && purchase > 0 ? margin / purchase * 100 : null, '%');
            message('Предварительный расчёт в браузере. Уточняем итог на сервере…');
        }
        function updateHints(state) {
            var v = state.values, r = state.result, c = r.costs || {}, l = r.logistics || {};
            var weekly = state.calculator_advertising || {}, origins = state.origins || {};
            var loss = v.loss_percent == null ? null : v.loss_percent / 100;
            function n(key) { return number(v[key]); }
            function rub(value) { return number(value) + ' ₽'; }
            function charge(formula, amount) { return formula + ' = ' + rub(amount); }
            var nonBuyout = v.buyout_percent == null ? null : 100 - v.buyout_percent;
            var formulas = {
                seller_price: 'Цена продавца до скидок площадки. Доход на единицу в расчёте: ' + rub(v.seller_price) + '.',
                buyer_price: 'Цена покупателя без карты Пэй; база для НДС и УСН: ' + rub(v.buyer_price) + '.',
                pay_price: 'Цена покупателя с картой Пэй. Показана для сравнения; напрямую из прибыли не вычитается.',
                purchase_price: 'Себестоимость из данных 1С (Google-таблица YM), если не задана вручную. В расходах: ' + rub(c.purchase) + '.',
                fulfillment_cost: 'Затраты на ФФ на единицу из колонки «Проч. затр., руб» листа YM. Вычитаются из чистой прибыли один раз: −' + rub(v.fulfillment_cost) + '. Без умножения на процент выкупа.',
                commission_percent: charge(n('seller_price') + ' ₽ × ' + n('commission_percent') + '%', r.commission_rub),
                commission_rub: charge(n('seller_price') + ' ₽ × ' + n('commission_percent') + '%', r.commission_rub) + '. Сумма и процент — одна комиссия.',
                acquiring_percent: charge(n('seller_price') + ' ₽ × ' + n('acquiring_percent') + '%', c.acquiring),
                payment_acceptance: 'Фиксированный расход на единицу из тарифа ЯМ: ' + rub(c.payment_acceptance) + '.',
                company_commission_percent: charge(n('seller_price') + ' ₽ × ' + n('company_commission_percent') + '%', c.company_commission),
                vat_percent: charge(n('buyer_price') + ' ₽ × ' + n('vat_percent') + ' / (100 + ' + n('vat_percent') + ')', c.vat),
                usn_percent: charge('(' + n('buyer_price') + ' ₽ − ' + rub(c.vat) + ') × ' + n('usn_percent') + '%', c.usn),
                loss_percent: charge(n('purchase_price') + ' ₽ × ' + n('loss_percent') + '%', c.loss),
                disposal_cost: charge(n('disposal_cost') + ' ₽ × потери ' + n('loss_percent') + '%', c.disposal == null && v.disposal_cost != null && loss != null ? v.disposal_cost * loss : c.disposal),
                volume_l: n('length') + ' × ' + n('width') + ' × ' + n('height') + ' см / 1000 = ' + n('volume_l') + ' л. Тариф доставки запрашивается по габаритам и весу.',
                delivery_customer: 'Часть стоимости доставки по тарифу ЯМ: ' + rub(v.delivery_customer) + '. Уже включена в доставку выкупленного товара.',
                middle_mile: 'Средняя миля из того же тарифа доставки ЯМ: ' + rub(v.middle_mile) + '. Также используется для расчёта невыкупа.',
                delivery_other: 'Прочие составляющие именно доставки по тарифу ЯМ: ' + rub(v.delivery_other) + '. Уже включены в доставку выкупленного товара.',
                delivery_cost: charge(n('delivery_customer') + ' ₽ + ' + n('middle_mile') + ' ₽ + ' + n('delivery_other') + ' ₽', v.delivery_cost),
                return_cost: charge(n('middle_mile') + ' ₽ + 15 ₽', v.return_cost),
                logistics_returns: charge(n('return_cost') + ' ₽ × (100 − ' + n('buyout_percent') + ') / 100', l.returns),
                repeat_delivery: charge(n('delivery_cost') + ' ₽ × (100 − ' + n('buyout_percent') + ') / 100', l.repeat_delivery),
                transit_cost: 'Транзит на единицу: ' + rub(l.transit) + '. Прибавляется к логистике один раз.',
                logistics_total: charge(rub(l.delivery) + ' + ' + rub(l.returns) + ' + ' + rub(l.repeat_delivery) + ' + ' + rub(l.transit), l.total),
                buyout_percent: 'Выкуп: ' + n('buyout_percent') + '%. Доля невыкупа: ' + number(nonBuyout) + '%. Расчётные выкупы = количество заказов × процент выкупа / 100.',
                plan_drr: state.values.advertising_mode === 'weekly'
                    ? number(weekly.spend) + ' ₽ / (' + number(weekly.orders_amount) + ' ₽ × ' + n('buyout_percent') + '%) × 100 = ' + n('plan_drr') + '%.'
                    : v.advertising_basis === 'drr'
                        ? charge(n('seller_price') + ' ₽ × ' + n('plan_drr') + '% × ' + n('buyout_percent') + '%', c.advertising)
                        : v.plan_drr == null
                            ? 'ДРР не определён при нулевом выкупе и положительном расходе.'
                            : rub(c.advertising) + ' / (' + n('seller_price') + ' ₽ × ' + n('buyout_percent') + '%) × 100 = ' + n('plan_drr') + '%.',
                advertising_per_buyout: state.values.advertising_mode === 'weekly'
                    ? charge(number(weekly.spend) + ' ₽ / (' + number(weekly.orders_count) + ' заказов × ' + n('buyout_percent') + '%)', v.advertising_per_buyout) + '. За ' + (weekly.period_from || '—') + ' — ' + (weekly.period_to || '—') + ', только дни с заказами и рекламой.'
                    : 'Расход на один выкуп в сценарии: ' + rub(c.advertising) + '. ' + (v.advertising_basis === 'drr' ? 'Цена продавца × ставка ДРР / 100 × процент выкупа / 100.' : 'Задан вручную; вычитается из прибыли один раз.'),
            };
            container.querySelectorAll('[data-ym-field]').forEach(function (input) {
                var key = input.dataset.ymField;
                var origin = linkedPrices[key] && changed[key] != null
                    ? 'Сценарий: пересчитано с сохранением СПП и скидки Пэй'
                    : changed[key] != null ? 'Сценарий: введено вручную' : origins[key] || 'Расчёт из параметров товара';
                var formula = formulas[key] || ('Значение: ' + n(key) + '. Используется при запросе тарифа доставки ЯМ.');
                if (['logistics_total', 'logistics_returns', 'repeat_delivery', 'delivery_cost', 'return_cost'].includes(key) && changed[key] != null)
                    formula = 'Ручное значение заменяет автоматический расчёт: ' + rub(Number(input.value)) + '.';
                var hint = origin + '.\n' + formula;
                input.title = hint;
                var label = input.closest('label') || input.closest('summary');
                if (label) label.title = hint;
                var span = label && label.querySelector('span[title]');
                if (span) span.title = hint;
            });
            var results = container.querySelectorAll('[data-ym-result] > div');
            if (results[0]) results[0].title = charge(n('seller_price') + ' ₽ − расходы ' + rub(r.total_cost), r.margin);
            if (results[1]) results[1].title = rub(r.margin) + ' / ' + n('purchase_price') + ' ₽ × 100 = ' + number(r.roi) + '%';
        }
        function showDiscounts(state) {
            ['spp_percent', 'pay_discount_percent'].forEach(function (key) {
                var pay = key === 'pay_discount_percent';
                var base = state.values[pay ? 'buyer_price' : 'seller_price'];
                var target = state.values[pay ? 'pay_price' : 'buyer_price'];
                var percent = typeof base === 'number' && Number.isFinite(base) && base > 0 &&
                    typeof target === 'number' && Number.isFinite(target) && target > 0 && target <= base
                    ? (1 - target / base) * 100 : null;
                var node = container.querySelector('[data-ym-computed="' + key + '"]');
                node.textContent = number(percent) + (percent == null ? '' : '%');
                node.closest('.ue1c-calculator-row').title = percent == null
                    ? 'Нет сопоставимых цен для расчёта скидки.'
                    : '(1 − ' + number(target) + ' / ' + number(base) + ') × 100 = ' + number(percent) + '%.' +
                        (pay ? ' База — цена покупателя без Пэй.' : ' База — цена продавца.');
            });
        }
        function showResult(state) {
            var r = state.result,
                period =
                    product.ym_economics.scheme === scheme
                        ? product.ym_economics.period || {}
                        : data.period || {},
                coverage = period.coverage || {};
            showLogistics(state);
            showDiscounts(state);
            specs.forEach(function (spec) {
                var key = spec[0];
                var input = container.querySelector('[data-ym-field="' + key + '"]');
                if (!input) return;
                var value = inputValue(key, key === 'commission_rub' ? r.commission_rub :
                    key === 'logistics_total' ? r.logistics.total :
                    key === 'logistics_returns' ? r.logistics.returns :
                    key === 'repeat_delivery' ? r.logistics.repeat_delivery : state.values[key]);
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
            container.querySelector('[data-ym-field="advertising_per_buyout"]').value =
                inputValue('advertising_per_buyout', state.values.advertising_per_buyout);
            container.querySelector('.ym-drr-row').classList.toggle('is-incomplete',
                !manual && (!weekly.complete || weekly.drr == null));
            container.querySelector('.ym-ad-row').classList.toggle('is-incomplete',
                !manual && (!weekly.complete || state.values.advertising_per_buyout == null));
            updateHints(state);
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
            if (breakEven) pending();
            else previewInBrowser();
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
                    if (breakEven) {
                        changed = result.economics.break_even_scenario;
                        priceSource = 'seller_price';
                    }
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
