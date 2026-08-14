(function () {
    var root = document.getElementById('unit-economics-wb');
    var configNode = document.getElementById('ue-config');
    if (!root || !configNode) return;
    var readOnly = document.body.dataset.accessLevel === 'read';

    var config = JSON.parse(configNode.textContent);
    var money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
    var decimal = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    var percent = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
    var generalStorageKey = 'checkstock.ue.wb-fbs.settings';
    var savedGeneral = readJson(generalStorageKey, {});

    var state = {
        store: savedGeneral.store || 'tris',
        fulfillment: savedGeneral.fulfillment || (config.fulfillments[0] || {}).name,
        controls: Object.assign({}, config.defaults, savedGeneral.controls || {}),
        taxes: savedGeneral.taxes || {},
        products: [],
        overrides: {},
        selected: null,
        expanded: new Set(),
        loaded: false,
        loading: false
    };

    var nodes = {
        tabs: document.getElementById('ue-cabinet-tabs'),
        fulfillment: document.getElementById('ue-fulfillment'),
        ffRatesRows: document.getElementById('ue-ff-rates-rows'),
        ffRatesSave: document.getElementById('ue-ff-rates-save'),
        ffRatesStatus: document.getElementById('ue-ff-rates-status'),
        tax: document.getElementById('ue-tax'),
        refresh: document.getElementById('ue-refresh'),
        refreshStatus: document.getElementById('ue-refresh-status'),
        start: document.getElementById('ue-start-state'),
        results: document.getElementById('ue-results'),
        sourceNote: document.getElementById('ue-source-note'),
        rows: document.getElementById('ue-product-rows'),
        empty: document.getElementById('ue-empty'),
        search: document.getElementById('ue-search'),
        colorFilter: document.getElementById('ue-color-filter'),
        stockOnly: document.getElementById('ue-stock-only'),
        count: document.getElementById('ue-count'),
        averageRoi: document.getElementById('ue-average-roi'),
        negativeCount: document.getElementById('ue-negative-count'),
        incompleteCount: document.getElementById('ue-incomplete-count'),
        selectedArticle: document.getElementById('ue-selected-article'),
        targetRoi: document.getElementById('ue-target-roi'),
        currentPrice: document.getElementById('ue-current-price'),
        targetPrice: document.getElementById('ue-target-price'),
        priceDelta: document.getElementById('ue-price-delta'),
        targetRetail: document.getElementById('ue-target-retail'),
        simulatorHint: document.getElementById('ue-simulator-hint'),
        exportButton: document.getElementById('ue-export')
    };

    function readJson(key, fallback) {
        try {
            var parsed = JSON.parse(window.localStorage.getItem(key));
            return parsed && typeof parsed === 'object' ? parsed : fallback;
        } catch (error) {
            return fallback;
        }
    }

    function writeJson(key, value) {
        try {
            window.localStorage.setItem(key, JSON.stringify(value));
        } catch (error) {

        }
    }

    function productStorageKey() {
        return 'checkstock.ue.wb-fbs.products.' + state.store;
    }

    function saveGeneral() {
        writeJson(generalStorageKey, {
            store: state.store,
            fulfillment: state.fulfillment,
            controls: state.controls,
            taxes: state.taxes
        });
    }

    function storeConfig() {
        return config.stores.find(function (store) { return store.slug === state.store; }) || config.stores[0];
    }

    function taxRate() {
        var value = state.taxes[state.store];
        return Number(value === undefined ? storeConfig().tax : value) || 0;
    }

    function fulfillmentConfig() {
        return config.fulfillments.find(function (item) { return item.name === state.fulfillment; }) || config.fulfillments[0];
    }

    function fulfillmentRatesComplete(ff) {
        return ff && finite(ff.storage) !== null && finite(ff.accept) !== null
            && finite(ff.fulfillment) !== null;
    }

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function finite(value) {
        if (value === null || value === undefined || value === '') return null;
        var number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function hasOwn(object, key) {
        return Object.prototype.hasOwnProperty.call(object, key);
    }

    function valueFor(product, key, fallback) {
        var override = state.overrides[product.article] || {};
        return hasOwn(override, key) ? finite(override[key]) : finite(fallback);
    }

    function productValues(product) {
        return {
            price: valueFor(product, 'price', product.price),
            purchase: valueFor(product, 'purchase', product.purchase_price),
            volume: valueFor(product, 'volume', product.volume_l),
            commission: valueFor(product, 'commission', product.commission_rate),
            delivery: valueFor(product, 'delivery', 0),
            spp: valueFor(product, 'spp', product.spp_percent === null ? state.controls.spp : product.spp_percent)
        };
    }

    function logistics(volume) {
        if (volume === null || volume <= 0) return null;
        if (volume <= 0.2) return 23;
        if (volume <= 0.4) return 26;
        if (volume <= 0.6) return 29;
        if (volume <= 0.8) return 30;
        if (volume <= 1) return 32;
        return 46 + Math.ceil(volume - 1) * 14;
    }

    function calculate(product) {
        var values = productValues(product);
        if (!(values.price > 0) || !(values.purchase > 0) || !(values.volume > 0) || values.commission === null) {
            return null;
        }

        var ff = fulfillmentConfig();
        if (!fulfillmentRatesComplete(ff)) return null;
        var storage = values.volume / 1000 * ff.storage * 30;
        var fulfillment = ff.accept + ff.fulfillment + storage;


        var retail = values.price;
        var preSppPrice = values.spp !== null && values.spp >= 0 && values.spp < 100
            ? values.price / (1 - values.spp / 100)
            : null;
        var commission = values.price * values.commission / 100;
        var acquiring = values.price * state.controls.acquiring / 100;
        var advertising = values.price * state.controls.advertising / 100;
        var netRevenue = retail - acquiring - values.delivery - commission - advertising;
        var stockFee = values.purchase * state.controls.stock_days / 365 * state.controls.stock_rate / 100;
        var logistic = logistics(values.volume);
        var tax = retail * taxRate() / 100;
        var operating = values.price * (
            state.controls.overhead + state.controls.team + state.controls.contribution
        ) / 100;
        var profit = netRevenue - values.purchase - fulfillment - stockFee - tax - logistic - operating;

        return {
            values: values,
            retail: retail,
            preSppPrice: preSppPrice,
            commission: commission,
            acquiring: acquiring,
            advertising: advertising,
            netRevenue: netRevenue,
            storage: storage,
            fulfillment: fulfillment,
            stockFee: stockFee,
            logistics: logistic,
            tax: tax,
            operating: operating,
            profit: profit,
            rbe: profit / values.price * 100,
            roi: profit / values.purchase * 100
        };
    }

    function targetCalculation(product) {
        var values = productValues(product);
        if (!(values.purchase > 0) || !(values.volume > 0) || values.commission === null) return null;

        var ff = fulfillmentConfig();
        if (!fulfillmentRatesComplete(ff)) return null;
        var storage = values.volume / 1000 * ff.storage * 30;
        var fulfillment = ff.accept + ff.fulfillment + storage;
        var logistic = logistics(values.volume);
        var stockFee = values.purchase * state.controls.stock_days / 365 * state.controls.stock_rate / 100;
        var coefficient = 1 - taxRate() / 100
            - state.controls.acquiring / 100
            - state.controls.advertising / 100
            - values.commission / 100
            - state.controls.overhead / 100
            - state.controls.team / 100
            - state.controls.contribution / 100;
        if (coefficient <= 0) return null;

        var requested = (finite(nodes.targetRoi.value) || 0) / 100;
        var fixed = values.purchase + fulfillment + stockFee + logistic + values.delivery;
        var price = (requested * values.purchase + fixed) / coefficient;
        return {
            price: price,
            preSppPrice: values.spp !== null && values.spp >= 0 && values.spp < 100
                ? price / (1 - values.spp / 100)
                : null,
            delta: values.price === null ? null : price - values.price
        };
    }

    function renderTabs() {
        nodes.tabs.innerHTML = config.stores.map(function (store) {
            return '<button class="ue-cabinet-tab' + (store.slug === state.store ? ' active' : '')
                + '" type="button" role="tab" aria-selected="' + (store.slug === state.store ? 'true' : 'false')
                + '" data-store="' + escapeHtml(store.slug) + '">' + escapeHtml(store.name)
                + '<small>' + decimal.format(state.taxes[store.slug] === undefined ? store.tax : state.taxes[store.slug]) + '%</small></button>';
        }).join('');
    }

    function renderFulfillment() {
        nodes.fulfillment.innerHTML = config.fulfillments.map(function (ff) {
            return '<option value="' + escapeHtml(ff.name) + '"' + (ff.name === state.fulfillment ? ' selected' : '') + '>'
                + escapeHtml(ff.name) + '</option>';
        }).join('');
        var ff = fulfillmentConfig();
        document.getElementById('ue-ff-storage').textContent = finite(ff.storage) === null
            ? 'не задано' : decimal.format(ff.storage) + ' ₽/м³/сут';
        document.getElementById('ue-ff-accept').textContent = finite(ff.accept) === null
            ? 'не задано' : money.format(ff.accept) + '/шт';
        document.getElementById('ue-ff-cost').textContent = finite(ff.fulfillment) === null
            ? 'не задано' : money.format(ff.fulfillment) + '/шт';
    }

    function renderFulfillmentRates() {
        nodes.ffRatesRows.innerHTML = config.fulfillments.map(function (ff) {
            var complete = fulfillmentRatesComplete(ff);
            return '<tr class="' + (complete ? '' : 'is-incomplete') + '" data-fulfillment="' + escapeHtml(ff.name) + '">'
                + '<th scope="row"><strong>' + escapeHtml(ff.name) + '</strong>'
                + (complete ? '' : '<small>не заполнено</small>') + '</th>'
                + rateInput(ff.name, 'storage', ff.storage, 'Хранение')
                + rateInput(ff.name, 'accept', ff.accept, 'Приёмка')
                + rateInput(ff.name, 'fulfillment', ff.fulfillment, 'Fulfillment')
                + '</tr>';
        }).join('');
    }

    function rateInput(name, field, value, label) {
        return '<td><input type="number" min="0" max="1000000" step="0.01" inputmode="decimal"'
            + ' data-rate="' + field + '" value="' + escapeHtml(inputValue(finite(value))) + '"'
            + (readOnly ? ' disabled' : '')
            + ' aria-label="' + escapeHtml(label + ', ' + name) + '"></td>';
    }

    async function saveFulfillmentRates() {
        var rates = Array.from(nodes.ffRatesRows.querySelectorAll('tr')).map(function (row) {
            function raw(field) {
                return row.querySelector('[data-rate="' + field + '"]').value.trim();
            }
            return {
                name: row.dataset.fulfillment,
                storage: raw('storage'),
                accept: raw('accept'),
                fulfillment: raw('fulfillment')
            };
        });

        nodes.ffRatesSave.disabled = true;
        nodes.ffRatesSave.classList.add('is-loading');
        nodes.ffRatesStatus.className = '';
        nodes.ffRatesStatus.textContent = 'Сохраняем…';
        try {
            var response = await fetch('/sales/unit-economics/wb-fbs/fulfillment-rates', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify({ rates: rates })
            });
            var responseText = await response.text();
            var payload;
            try {
                payload = JSON.parse(responseText);
            } catch (parseError) {
                payload = { ok: false, error: 'Сервер не вернул результат сохранения' };
            }
            if (!response.ok || !payload.ok) throw new Error(payload.error || 'Не удалось сохранить тарифы');

            config.fulfillments = payload.rates;
            if (!config.fulfillments.some(function (ff) { return ff.name === state.fulfillment; })) {
                state.fulfillment = config.fulfillments[0].name;
            }
            renderFulfillment();
            renderFulfillmentRates();
            nodes.ffRatesStatus.className = 'is-saved';
            nodes.ffRatesStatus.textContent = 'Сохранено в БД';
            invalidateCalculation('Тарифы сохранены · обновите расчет', 'Тарифы сохранены');
        } catch (error) {
            nodes.ffRatesStatus.className = 'is-error';
            nodes.ffRatesStatus.textContent = error.message || 'Ошибка сохранения';
        } finally {
            nodes.ffRatesSave.disabled = false;
            nodes.ffRatesSave.classList.remove('is-loading');
        }
    }

    function renderSettings() {
        nodes.tax.value = taxRate();
        root.querySelectorAll('[data-setting]').forEach(function (input) {
            input.value = state.controls[input.dataset.setting];
        });
    }

    function filteredProducts() {
        var query = nodes.search.value.trim().toLowerCase();
        var color = nodes.colorFilter.value;
        return state.products.filter(function (product) {
            var stock = product.fbs_stock + product.fbo_stock;
            if (nodes.stockOnly.checked && stock <= 0) return false;
            if (query && product.article.toLowerCase().indexOf(query) < 0 && product.name.toLowerCase().indexOf(query) < 0) {
                return false;
            }
            var calc = calculate(product);
            if (color === 'negative' && (!calc || calc.roi >= 0)) return false;
            if (color === 'positive' && (!calc || calc.roi < 0)) return false;
            if (color === 'incomplete' && calc) return false;
            return true;
        });
    }

    function formatMoneyOrDash(value) {
        return value === null || !Number.isFinite(value) ? '—' : money.format(value);
    }

    function formatPercentOrDash(value) {
        return value === null || !Number.isFinite(value) ? '—' : percent.format(value) + '%';
    }

    function inputValue(value) {
        return value === null || !Number.isFinite(value) ? '' : String(Math.round(value * 1000) / 1000);
    }

    function detailInput(article, field, label, value, suffix, placeholder) {
        return '<label class="ue-detail-field"><span>' + escapeHtml(label) + '</span><span class="ue-input-suffix">'
            + '<input type="number" min="0" step="0.01" value="' + escapeHtml(inputValue(value))
            + '" placeholder="' + escapeHtml(placeholder || '') + '" data-article="' + escapeHtml(article)
            + '" data-field="' + escapeHtml(field) + '">' + (suffix ? '<b>' + escapeHtml(suffix) + '</b>' : '')
            + '</span></label>';
    }

    function detailMetric(label, value) {
        return '<div class="ue-detail-metric"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong></div>';
    }

    function renderDetails(product, calc) {
        var values = productValues(product);
        var category = product.category || 'Категория не получена';
        return '<tr class="ue-details-row"><td colspan="8"><div class="ue-row-details">'
            + '<div class="ue-detail-fields"><div class="ue-detail-title"><strong>' + escapeHtml(category)
            + '</strong><button class="ue-detail-reset" type="button" data-action="reset" data-article="' + escapeHtml(product.article)
            + '">Сбросить ручные данные</button></div>'
            + detailInput(product.article, 'price', 'Цена с СПП', values.price, '₽', 'из WB API')
            + detailInput(product.article, 'purchase', 'Закупочная цена', values.purchase, '₽', 'из 1С или файла')
            + detailInput(product.article, 'volume', 'Литраж товара', values.volume, 'л', 'из данных по артикулам')
            + detailInput(product.article, 'commission', 'Комиссия WB', values.commission, '%', 'по категории')
            + detailInput(product.article, 'delivery', 'Доставка с возвратами', values.delivery, '₽', '0')
            + detailInput(product.article, 'spp', 'СПП для строки', values.spp, '%', String(state.controls.spp))
            + '</div><div class="ue-detail-calculation"><div class="ue-detail-title"><strong>Расшифровка расчета</strong><span>Столбцы H:AE</span></div>'
            + detailMetric('Цена до СПП', calc ? formatMoneyOrDash(calc.preSppPrice) : '—')
            + detailMetric('Комиссия WB', calc ? money.format(calc.commission) : '—')
            + detailMetric('Эквайринг', calc ? money.format(calc.acquiring) : '—')
            + detailMetric('Реклама', calc ? money.format(calc.advertising) : '—')
            + detailMetric('Чистая выручка', calc ? money.format(calc.netRevenue) : '—')
            + detailMetric('Логистика', calc ? money.format(calc.logistics) : '—')
            + detailMetric('Накладные + команда + Contib', calc ? money.format(calc.operating) : '—')
            + detailMetric('Налог', calc ? money.format(calc.tax) : '—')
            + detailMetric('Плата за сток', calc ? money.format(calc.stockFee) : '—')
            + '</div></div></td></tr>';
    }

    function renderRows() {
        if (!state.loaded) return;
        var products = filteredProducts();
        var html = '';
        var roiValues = [];
        var negatives = 0;
        var incomplete = 0;

        products.forEach(function (product) {
            var calc = calculate(product);
            var selected = product.article === state.selected;
            var expanded = state.expanded.has(product.article);
            var rowClass = 'ue-product-row';
            if (!calc) {
                rowClass += ' ue-row--incomplete';
                incomplete += 1;
            } else {
                roiValues.push(calc.roi);
                if (calc.roi < 0) {
                    rowClass += ' ue-row--negative';
                    negatives += 1;
                }
            }
            if (selected) rowClass += ' is-selected';

            var sourceLabel = product.price_source === 'finishedPrice'
                ? 'WB API · с СПП'
                : (product.price_source === 'discountedPrice' ? 'WB API · без СПП' : 'ручной ввод');
            var priceSource = product.price_source
                ? '<small class="ue-api-source">' + sourceLabel + '</small>'
                : '<small class="ue-api-source ue-api-source--manual">' + sourceLabel + '</small>';
            html += '<tr class="' + rowClass + '" data-select="' + escapeHtml(product.article) + '">'
                + '<td><button class="ue-article" type="button" data-action="select" data-article="' + escapeHtml(product.article) + '">'
                + escapeHtml(product.article) + '</button><small>сток WB: ' + decimal.format(product.fbs_stock + product.fbo_stock) + '</small></td>'
                + '<td><strong class="ue-name">' + escapeHtml(product.name) + '</strong><small>' + escapeHtml(product.category || 'Категория не получена') + '</small></td>'
                + '<td class="ue-num"><strong>' + formatMoneyOrDash(productValues(product).price) + '</strong>' + priceSource + '</td>'
                + '<td class="ue-num"><strong>' + (calc ? money.format(calc.fulfillment) : '—') + '</strong><small>' + escapeHtml(fulfillmentConfig().name) + '</small></td>'
                + '<td class="ue-num"><strong class="' + (calc && calc.profit < 0 ? 'ue-negative-value' : '') + '">' + (calc ? money.format(calc.profit) : '—') + '</strong></td>'
                + '<td class="ue-num"><strong>' + (calc ? formatPercentOrDash(calc.rbe) : '—') + '</strong></td>'
                + '<td class="ue-num"><span class="ue-roi ' + (calc ? (calc.roi < 0 ? 'ue-roi--negative' : 'ue-roi--positive') : 'ue-roi--empty') + '">'
                + (calc ? formatPercentOrDash(calc.roi) : 'нет данных') + '</span></td>'
                + '<td><button class="ue-row-toggle" type="button" data-action="toggle" data-article="' + escapeHtml(product.article)
                + '" aria-expanded="' + (expanded ? 'true' : 'false') + '" title="Показать детали" aria-label="Показать детали">'
                + '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg></button></td></tr>';
            if (expanded) html += renderDetails(product, calc);
        });

        nodes.rows.innerHTML = html;
        nodes.empty.hidden = products.length > 0;
        nodes.count.textContent = decimal.format(products.length);
        nodes.negativeCount.textContent = decimal.format(negatives);
        nodes.incompleteCount.textContent = decimal.format(incomplete);
        nodes.averageRoi.textContent = roiValues.length
            ? formatPercentOrDash(roiValues.reduce(function (sum, value) { return sum + value; }, 0) / roiValues.length)
            : '—';
        renderSimulator();
    }

    function selectedProduct() {
        return state.products.find(function (product) { return product.article === state.selected; }) || null;
    }

    function renderSimulator() {
        var product = selectedProduct();
        if (!product) {
            nodes.selectedArticle.textContent = '—';
            nodes.currentPrice.textContent = '—';
            nodes.targetPrice.textContent = '—';
            nodes.priceDelta.textContent = '—';
            nodes.targetRetail.textContent = '—';
            nodes.simulatorHint.textContent = 'Выберите строку и заполните закупочную цену с литражом.';
            return;
        }

        var values = productValues(product);
        var target = targetCalculation(product);
        nodes.selectedArticle.textContent = product.article;
        nodes.currentPrice.textContent = formatMoneyOrDash(values.price);
        nodes.targetPrice.textContent = target ? money.format(target.price) : '—';
        nodes.priceDelta.textContent = target ? formatMoneyOrDash(target.delta) : '—';
        nodes.targetRetail.textContent = target ? formatMoneyOrDash(target.preSppPrice) : '—';
        nodes.simulatorHint.textContent = target
            ? 'Цена рассчитана по выбранному кабинету, налогу и условиям фулфилмента.'
            : 'Для симуляции заполните закупочную цену, литраж и комиссию WB.';
    }

    function renderSourceNote(warnings, cached) {
        if (warnings && warnings.length) {
            nodes.sourceNote.className = 'ue-source-note ue-source-note--warning';
            nodes.sourceNote.innerHTML = '<strong>Часть данных требует ручного ввода.</strong><span>'
                + escapeHtml(warnings.join(' · ')) + '</span>';
            return;
        }
        nodes.sourceNote.className = 'ue-source-note';
        nodes.sourceNote.innerHTML = '<strong>Данные синхронизированы.</strong><span>Себестоимость получена из таблицы 1С, литраж и комиссия — из WB. Цена с СПП основана на последнем заказе.'
            + (cached ? ' Повторный запрос взят из минутного кеша.' : '') + '</span>';
    }

    function setLoading(loading) {
        state.loading = loading;
        nodes.refresh.disabled = loading;
        nodes.refresh.classList.toggle('is-loading', loading);
        nodes.refresh.querySelector('span').textContent = loading ? 'Получаем данные' : 'Обновить расчет';
        if (loading) nodes.refreshStatus.textContent = 'WB API и каталог РАКЕТА';
    }

    function invalidateCalculation(status, title) {
        state.loaded = false;
        state.products = [];
        state.selected = null;
        state.expanded.clear();
        nodes.results.hidden = true;
        nodes.start.hidden = false;
        nodes.start.querySelector('h3').textContent = title || 'Условия расчета изменены';
        nodes.start.querySelector('p').textContent = 'Обновите расчет, чтобы применить новые значения.';
        nodes.refreshStatus.textContent = status || 'Нужно обновить расчет';
    }

    function focusMissingFulfillmentRate() {
        var settings = root.querySelector('.ue-ff-settings');
        if (settings) settings.open = true;
        var row = Array.from(nodes.ffRatesRows.querySelectorAll('tr')).find(function (item) {
            return item.dataset.fulfillment === state.fulfillment;
        });
        if (!row) return;
        var missing = Array.from(row.querySelectorAll('[data-rate]')).find(function (input) {
            return input.value.trim() === '';
        });
        if (missing) missing.focus();
    }

    async function refresh() {
        if (state.loading) return;
        if (nodes.ffRatesStatus.classList.contains('is-dirty')) {
            nodes.refreshStatus.textContent = 'Сначала сохраните тарифы фулфилментов';
            return;
        }
        if (!fulfillmentRatesComplete(fulfillmentConfig())) {
            nodes.refreshStatus.textContent = 'Заполните тарифы выбранного фулфилмента';
            focusMissingFulfillmentRate();
            return;
        }
        setLoading(true);
        try {
            var response = await fetch('/sales/unit-economics/wb-fbs/calculate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify({ store: state.store })
            });
            var payload = await response.json();
            if (!response.ok || !payload.ok) throw new Error(payload.error || 'Не удалось получить данные');

            state.products = payload.rows || [];
            state.overrides = readJson(productStorageKey(), {});
            state.loaded = true;
            state.expanded.clear();
            var firstVisible = state.products.find(function (product) {
                return !nodes.stockOnly.checked || product.fbs_stock + product.fbo_stock > 0;
            });
            state.selected = firstVisible ? firstVisible.article : null;
            nodes.start.hidden = true;
            nodes.results.hidden = false;
            renderSourceNote(payload.warnings || [], payload.cached);
            var updated = new Date(payload.updated_at);
            nodes.refreshStatus.textContent = 'Обновлено ' + updated.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
            renderRows();
        } catch (error) {
            nodes.refreshStatus.textContent = error.message || 'Ошибка обновления';
            nodes.start.hidden = false;
            nodes.start.querySelector('h3').textContent = 'Расчет не обновлен';
            nodes.start.querySelector('p').textContent = error.message || 'Не удалось получить данные WB.';
        } finally {
            setLoading(false);
        }
    }

    function selectStore(slug) {
        if (slug === state.store) return;
        state.store = slug;
        state.loaded = false;
        state.products = [];
        state.overrides = {};
        state.selected = null;
        state.expanded.clear();
        nodes.results.hidden = true;
        nodes.start.hidden = false;
        nodes.start.querySelector('h3').textContent = 'Обновите расчет для ' + storeConfig().name;
        nodes.start.querySelector('p').textContent = 'Данные предыдущего кабинета скрыты, чтобы расчеты не смешивались.';
        nodes.refreshStatus.textContent = 'Нужно обновить расчет';
        renderTabs();
        renderSettings();
        saveGeneral();
    }

    function updateOverride(article, field, rawValue, shouldRender) {
        var entry = state.overrides[article] || {};
        if (rawValue === '') {
            delete entry[field];
        } else {
            entry[field] = Number(rawValue);
        }
        if (Object.keys(entry).length) state.overrides[article] = entry;
        else delete state.overrides[article];
        writeJson(productStorageKey(), state.overrides);
        if (shouldRender) renderRows();
    }

    function csvCell(value) {
        var string = String(value === null || value === undefined ? '' : value);
        return '"' + string.replace(/"/g, '""') + '"';
    }

    function exportCsv() {
        var headers = ['Категория', 'Артикул', 'Название', 'Цена с СПП', 'Комиссия WB', 'Эквайринг', 'Реклама', 'Чистая выручка', 'Закупочная цена', 'Литраж', 'Фулфилмент', 'Логистика', 'Накладные', 'Команда', 'Contib', 'Налог', 'Плата за сток', 'Чистая прибыль', 'RBE/TO%', 'ROI'];
        var lines = [headers.map(csvCell).join(';')];
        filteredProducts().forEach(function (product) {
            var calc = calculate(product);
            var values = productValues(product);
            lines.push([
                product.category, product.article, product.name, values.price, values.commission,
                calc && calc.acquiring, calc && calc.advertising, calc && calc.netRevenue,
                values.purchase, values.volume, calc && calc.fulfillment, calc && calc.logistics,
                state.controls.overhead, state.controls.team, state.controls.contribution,
                calc && calc.tax, calc && calc.stockFee, calc && calc.profit, calc && calc.rbe, calc && calc.roi
            ].map(csvCell).join(';'));
        });
        var blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = url;
        link.download = 'unit-economics-wb-' + state.store + '.csv';
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
    }

    nodes.tabs.addEventListener('click', function (event) {
        var button = event.target.closest('[data-store]');
        if (button) selectStore(button.dataset.store);
    });
    nodes.fulfillment.addEventListener('change', function () {
        state.fulfillment = nodes.fulfillment.value;
        renderFulfillment();
        saveGeneral();
        invalidateCalculation('Фулфилмент изменен · обновите расчет');
    });
    nodes.ffRatesSave.addEventListener('click', saveFulfillmentRates);
    nodes.ffRatesRows.addEventListener('input', function () {
        nodes.ffRatesStatus.className = 'is-dirty';
        nodes.ffRatesStatus.textContent = 'Есть несохранённые изменения';
    });
    nodes.tax.addEventListener('change', function () {
        state.taxes[state.store] = finite(nodes.tax.value) || 0;
        saveGeneral();
        renderTabs();
        invalidateCalculation('Налог изменен · обновите расчет');
    });
    root.querySelectorAll('[data-setting]').forEach(function (input) {
        input.addEventListener('change', function () {
            state.controls[input.dataset.setting] = finite(input.value) || 0;
            saveGeneral();
            invalidateCalculation('Параметры изменены · обновите расчет');
        });
    });
    nodes.refresh.addEventListener('click', refresh);
    nodes.search.addEventListener('input', renderRows);
    nodes.colorFilter.addEventListener('change', renderRows);
    nodes.stockOnly.addEventListener('change', renderRows);
    nodes.targetRoi.addEventListener('input', renderSimulator);
    nodes.exportButton.addEventListener('click', exportCsv);
    nodes.rows.addEventListener('click', function (event) {
        if (event.target.closest('input')) return;
        var reset = event.target.closest('[data-action="reset"]');
        if (reset) {
            delete state.overrides[reset.dataset.article];
            writeJson(productStorageKey(), state.overrides);
            renderRows();
            return;
        }
        var toggle = event.target.closest('[data-action="toggle"]');
        if (toggle) {
            var article = toggle.dataset.article;
            if (state.expanded.has(article)) state.expanded.delete(article);
            else state.expanded.add(article);
            state.selected = article;
            renderRows();
            return;
        }
        var target = event.target.closest('[data-select]');
        if (target) {
            state.selected = target.dataset.select || target.dataset.article;
            renderRows();
        }
    });
    nodes.rows.addEventListener('input', function (event) {
        var input = event.target.closest('[data-field]');
        if (input) updateOverride(input.dataset.article, input.dataset.field, input.value.trim(), false);
    });
    nodes.rows.addEventListener('change', function (event) {
        var input = event.target.closest('[data-field]');
        if (input) updateOverride(input.dataset.article, input.dataset.field, input.value.trim(), true);
    });

    if (!config.stores.some(function (store) { return store.slug === state.store; })) {
        state.store = config.stores[0].slug;
    }
    if (!config.fulfillments.some(function (ff) { return ff.name === state.fulfillment; })) {
        state.fulfillment = config.fulfillments[0].name;
    }
    renderTabs();
    renderFulfillment();
    renderFulfillmentRates();
    renderSettings();
})();
