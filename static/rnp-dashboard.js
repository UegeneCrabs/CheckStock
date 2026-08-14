(function () {
    'use strict';

    var root = document.querySelector('[data-rnp-dashboard]');
    if (!root) return;
    var readOnly = document.body.dataset.accessLevel === 'read';

    var controls = {
        month: root.querySelector('[name="month"]'),
        search: root.querySelector('[name="search"]'),
        store: root.querySelector('[name="store"]'),
        marketplaces: Array.from(root.querySelectorAll('[data-rnp-marketplace]')),
        refresh: root.querySelector('[data-rnp-refresh]'),
        forecast: root.querySelector('[data-rnp-forecast-toggle]'),
        density: root.querySelector('[data-rnp-density-toggle]'),
        metrics: root.querySelector('[data-rnp-metrics-toggle]'),
        metricsMenu: root.querySelector('[data-rnp-metrics-menu]')
    };
    var els = {
        head: root.querySelector('[data-rnp-head]'),
        body: root.querySelector('[data-rnp-body]'),
        table: root.querySelector('[data-rnp-table]'),
        scroll: root.querySelector('[data-rnp-scroll]'),
        state: root.querySelector('[data-rnp-state]'),
        empty: root.querySelector('[data-rnp-empty]'),
        pagination: root.querySelector('[data-rnp-pagination]'),
        pageLabel: root.querySelector('[data-rnp-page-label]'),
        prev: root.querySelector('[data-rnp-prev]'),
        next: root.querySelector('[data-rnp-next]'),
        sync: root.querySelector('[data-rnp-sync]'),
        syncLabel: root.querySelector('[data-rnp-sync-label]'),
        syncMessage: root.querySelector('[data-rnp-sync-message]'),
        modal: root.querySelector('[data-rnp-modal]'),
        modalForm: root.querySelector('[data-rnp-modal-form]'),
        modalTitle: root.querySelector('[data-rnp-modal-title]'),
        modalKicker: root.querySelector('[data-rnp-modal-kicker]'),
        modalBody: root.querySelector('[data-rnp-modal-body]'),
        modalError: root.querySelector('[data-rnp-modal-error]'),
        modalSubmit: root.querySelector('[data-rnp-modal-submit]')
    };

    var state = {
        marketplace: 'WB',
        offset: 0,
        limit: 25,
        forecast: true,
        compact: false,
        selectedMetrics: null,
        expandedGroups: (function () {
            try { return JSON.parse(localStorage.getItem('checkstock-rnp-groups') || '{}'); }
            catch (error) { return {}; }
        })(),
        data: null,
        request: null,
        modal: null
    };
    var money = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    var integer = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
    var dateLabel = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (char) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char];
        });
    }

    function formatValue(value, format, emptyZero) {
        if (value == null || value === '') return '—';
        if (format === 'date') {
            var parsedDate = new Date(String(value).slice(0, 10) + 'T00:00:00');
            return Number.isNaN(parsedDate.getTime()) ? '—' : dateLabel.format(parsedDate);
        }
        if (format === 'regions') {
            try {
                var regions = typeof value === 'string' ? JSON.parse(value) : value;
                var entries = Object.keys(regions || {}).map(function (key) {
                    return [key, Number(regions[key]) || 0];
                }).filter(function (entry) { return entry[1] !== 0; }).sort(function (a, b) {
                    return b[1] - a[1];
                });
                if (!entries.length) return '—';
                var visible = entries.slice(0, 3).map(function (entry) {
                    return entry[0] + ': ' + integer.format(entry[1]);
                }).join(' · ');
                return visible + (entries.length > 3 ? ' · ещё ' + (entries.length - 3) : '');
            } catch (error) {
                return String(value || '—');
            }
        }
        if (!Number.isFinite(Number(value))) return '—';
        if (emptyZero && Number(value) === 0) return '—';
        if (format === 'money') return money.format(Number(value)) + ' ₽';
        if (format === 'percent') return money.format(Number(value)) + ' %';
        if (format === 'decimal') return money.format(Number(value));
        if (format === 'signed') return (Number(value) > 0 ? '+' : '') + integer.format(Number(value));
        return integer.format(Number(value));
    }

    function marketplaceCode() {
        return state.marketplace === 'YANDEX MARKET' ? 'YM' : state.marketplace;
    }

    function selectedMetrics() {
        var definitions = (state.data && state.data.metrics) || [];
        if (!state.selectedMetrics) {
            state.selectedMetrics = definitions.map(function (item) { return item.id; });
        }
        return definitions.filter(function (item) {
            return state.selectedMetrics.indexOf(item.id) !== -1;
        });
    }

    function setLoading(loading, message) {
        root.classList.toggle('is-loading', loading);
        els.state.hidden = !loading;
        if (loading) {
            els.state.querySelector('strong').textContent = message || 'Собираем РНП';
            controls.refresh.disabled = true;
        } else {
            controls.refresh.disabled = false;
        }
    }

    function showLoadError(message) {
        els.state.hidden = false;
        els.state.innerHTML = '<strong>Не удалось загрузить РНП</strong><span>' + escapeHtml(message) + '</span>';
        els.empty.hidden = true;
        controls.refresh.disabled = false;
    }

    function queryString() {
        return new URLSearchParams({
            month: controls.month.value,
            store: controls.store.value,
            marketplace: state.marketplace,
            search: controls.search.value.trim(),
            limit: String(state.limit),
            offset: String(state.offset)
        }).toString();
    }

    function loadData(options) {
        var opts = options || {};
        if (state.request) state.request.abort();
        state.request = new AbortController();
        setLoading(true, opts.message || 'Собираем РНП');
        els.empty.hidden = true;
        return fetch('/api/rnp?' + queryString(), {
            headers: { 'Accept': 'application/json' },
            signal: state.request.signal
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok || data.ok === false) throw new Error(data.error || 'Ошибка сервера');
                return data;
            });
        }).then(function (data) {
            state.data = data;
            state.request = null;
            render();
            if (opts.resetScroll !== false) els.scroll.scrollLeft = 0;
        }).catch(function (error) {
            if (error.name === 'AbortError') return;
            state.request = null;
            showLoadError(error.message || 'Неизвестная ошибка');
        });
    }

    function syncData() {
        setLoading(true, 'Забираем воронку и рекламу из API');
        return fetch('/api/rnp/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                month: controls.month.value,
                store: controls.store.value,
                marketplace: state.marketplace,
                articles: state.data ? state.data.products.map(function (item) { return item.article; }) : []
            })
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok || data.ok === false) throw new Error(data.error || 'Не удалось обновить API');
                return data;
            });
        }).then(function () {
            return loadData({ resetScroll: false, message: 'Пересчитываем РНП' });
        }).catch(function (error) {
            showLoadError(error.message || 'Не удалось обновить данные площадки');
        });
    }

    function renderHeader() {
        var days = state.data.period.days;
        els.head.innerHTML = '<tr>' +
            '<th class="rnp-sticky-product">Магазин / товар</th>' +
            '<th class="rnp-sticky-metric">Показатель</th>' +
            days.map(function (day) {
                var classes = ['rnp-day-head'];
                if (day.weekend) classes.push('is-weekend');
                if (day.today) classes.push('is-today');
                if (day.future) classes.push('is-future');
                return '<th class="' + classes.join(' ') + '"><strong>' + String(day.day).padStart(2, '0') +
                    '</strong><span>' + escapeHtml(day.weekday) + (day.future ? ' · F' : '') + '</span></th>';
            }).join('') +
            '<th class="rnp-total-head">Факт месяца</th>' +
            '<th class="rnp-total-head rnp-col-forecast">Прогноз месяца</th>' +
            '</tr>';
    }

    function expansionKey(scope, metricId) {
        return [controls.store.value, state.marketplace, scope, metricId].join('|');
    }

    function isExpanded(scope, metric) {
        return Boolean(metric.children && metric.children.length && state.expandedGroups[expansionKey(scope, metric.id)]);
    }

    function ensureDefaultExpansion(metrics) {
        if (!state.data.products.length) return;
        var prefix = [controls.store.value, state.marketplace].join('|') + '|';
        if (Object.keys(state.expandedGroups).some(function (key) { return key.indexOf(prefix) === 0; })) return;
        var scope = 'product:' + state.data.products[0].article;
        metrics.forEach(function (metric) {
            if (metric.children && metric.children.length) {
                state.expandedGroups[expansionKey(scope, metric.id)] = true;
            }
        });
        localStorage.setItem('checkstock-rnp-groups', JSON.stringify(state.expandedGroups));
    }

    function metricLabel(metric, scope, child) {
        var hint = metric.hint ? ' title="' + escapeHtml(metric.hint) + '"' : '';
        if (child) {
            return '<span class="rnp-child-branch" aria-hidden="true"></span><strong' + hint + '>' +
                escapeHtml(metric.label) + '</strong>';
        }
        var expandable = metric.children && metric.children.length;
        var expanded = expandable && isExpanded(scope, metric);
        var toggle = expandable ? '<button class="rnp-group-toggle" type="button" data-rnp-toggle-group data-scope="' +
            escapeHtml(scope) + '" data-metric="' + escapeHtml(metric.id) + '" aria-expanded="' +
            (expanded ? 'true' : 'false') + '" aria-label="' + (expanded ? 'Свернуть' : 'Развернуть') +
            ' группу ' + escapeHtml(metric.group) + '"><span></span></button>' : '';
        return '<span class="rnp-metric-group">' + toggle + escapeHtml(metric.group) + '</span>' +
            '<strong' + hint + '>' + escapeHtml(metric.label) + '</strong>';
    }

    function dailyCells(record, metric) {
        return state.data.period.days.map(function (day) {
            var item = (record.daily && record.daily[day.date]) || {};
            var classes = ['rnp-value-cell'];
            if (day.weekend) classes.push('is-weekend');
            if (day.today) classes.push('is-today');
            if (day.future) classes.push('is-future');
            var value = day.future ? null : item[metric.id];
            var preserveZero = metric.id.indexOf('stock_') === 0 || metric.id === 'reviews_delta';
            var text = formatValue(value, metric.format, !preserveZero);
            return '<td class="' + classes.join(' ') + '">' + escapeHtml(text) + '</td>';
        }).join('');
    }

    function metricRow(record, metric, firstCell, scope, child) {
        return '<tr class="rnp-metric-row' + (child ? ' is-child' : ' is-parent') + '" data-metric="' + escapeHtml(metric.id) + '">' +
            (firstCell || '') +
            '<th class="rnp-sticky-metric" scope="row">' + metricLabel(metric, scope, child) + '</th>' +
            dailyCells(record, metric) +
            '<td class="rnp-month-total">' + escapeHtml(formatValue(record.fact[metric.id], metric.format, false)) + '</td>' +
            '<td class="rnp-month-total rnp-col-forecast">' + escapeHtml(formatValue(record.forecast[metric.id], metric.format, false)) + '</td>' +
            '</tr>';
    }

    function metricRowCount(metrics, scope) {
        return metrics.reduce(function (count, metric) {
            return count + 1 + (isExpanded(scope, metric) ? metric.children.length : 0);
        }, 0);
    }

    function renderMetricRows(record, metrics, scope, firstCell) {
        var usedFirstCell = false;
        return metrics.map(function (metric) {
            var cell = usedFirstCell ? '' : firstCell;
            usedFirstCell = true;
            var html = metricRow(record, metric, cell, scope, false);
            if (isExpanded(scope, metric)) {
                html += metric.children.map(function (child) {
                    return metricRow(record, child, '', scope, true);
                }).join('');
            }
            return html;
        }).join('');
    }

    function renderStoreRows(metrics) {
        var scope = 'store';
        var firstCell = '<th class="rnp-sticky-product rnp-store-cell" rowspan="' + metricRowCount(metrics, scope) + '" scope="rowgroup">' +
            '<span class="rnp-store-mark">Σ</span><div><strong>' + escapeHtml(state.data.store.name) +
            '</strong><small>Итого магазина · ' + escapeHtml(state.data.marketplace_label) + '</small></div></th>';
        return '<tbody class="rnp-store-total">' + renderMetricRows(state.data.totals, metrics, scope, firstCell) + '</tbody>';
    }

    function productImage(product) {
        if (product.image_url) {
            return '<img src="' + escapeHtml(product.image_url) + '" alt="" loading="lazy">';
        }
        return '<span>' + escapeHtml(marketplaceCode()) + '</span>';
    }

    function strategyCard(product) {
        var strategy = product.strategy;
        var editButton = readOnly ? '' : '<button type="button" data-rnp-edit-strategy data-article="' +
            escapeHtml(product.article) + '">' + (strategy ? 'Изменить' : 'Добавить') + '</button>';
        return '<div class="rnp-product-strategy">' +
            '<div><span>СТРАТЕГИЯ</span>' + editButton + '</div>' +
            '<strong>' + escapeHtml(strategy ? strategy.strategy : 'Стратегия не задана') + '</strong>' +
            '<small>' + (strategy ? dateLabel.format(new Date(strategy.date_from + 'T00:00:00')) + ' — ' +
                dateLabel.format(new Date(strategy.date_to + 'T00:00:00')) : 'Добавьте период и цель для товара') + '</small>' +
            '</div>';
    }

    function productCell(product, rowspan) {
        var priceRows = '';
        if (product.current_price != null) {
            priceRows += '<div><span>' + escapeHtml(product.price_source) + '</span><strong>' +
                formatValue(product.current_price, 'money', false) + '</strong></div>';
        }
        if (product.list_price != null && state.marketplace === 'WB') {
            priceRows += '<div><span>Цена до скидки</span><strong>' + formatValue(product.list_price, 'money', false) + '</strong></div>';
        }
        if (product.spp_percent != null && state.marketplace === 'WB') {
            priceRows += '<div><span>СПП</span><strong>' + formatValue(product.spp_percent, 'percent', false) + '</strong></div>';
        }
        priceRows += '<div><span>Остаток сейчас</span><strong>' + formatValue(product.current_stock, 'integer', false) + '</strong></div>';
        return '<th class="rnp-sticky-product rnp-product-cell" rowspan="' + rowspan + '" scope="rowgroup">' +
            '<div class="rnp-product-head"><span class="rnp-product-image">' + productImage(product) + '</span>' +
            '<div><strong title="' + escapeHtml(product.name) + '">' + escapeHtml(product.name) + '</strong>' +
            '<small>' + escapeHtml(marketplaceCode()) + ': ' + escapeHtml(product.article) +
            (product.mp_sku ? ' · SKU: ' + escapeHtml(product.mp_sku) : '') + '</small></div></div>' +
            '<div class="rnp-product-stats">' + priceRows + '</div>' + strategyCard(product) + '</th>';
    }

    function strategyRow(product, rowspan) {
        var strategy = product.strategy;
        var cells = state.data.period.days.map(function (day) {
            var active = strategy && day.date >= strategy.date_from && day.date <= strategy.date_to;
            var classes = ['rnp-strategy-cell'];
            if (day.weekend) classes.push('is-weekend');
            if (day.future) classes.push('is-future');
            return '<td class="' + classes.join(' ') + '">' +
                (active ? '<span>' + escapeHtml(strategy.strategy) + '</span>' : '—') + '</td>';
        }).join('');
        return '<tr class="rnp-strategy-row">' + productCell(product, rowspan) +
            '<th class="rnp-sticky-metric" scope="row"><strong>Стратегия</strong></th>' + cells +
            '<td class="rnp-month-total">' + escapeHtml(strategy ? strategy.strategy : '—') + '</td>' +
            '<td class="rnp-month-total rnp-col-forecast">—</td></tr>';
    }

    function actionRow(product) {
        var cells = state.data.period.days.map(function (day) {
            var entries = product.actions[day.date] || [];
            var note = entries.map(function (item) { return item.note; }).join('\n');
            var disabled = day.future || readOnly ? ' disabled' : '';
            var classes = ['rnp-action-cell'];
            if (entries.length) classes.push('has-note');
            if (day.weekend) classes.push('is-weekend');
            if (day.future) classes.push('is-future');
            return '<td class="' + classes.join(' ') + '"><button type="button" data-rnp-add-action data-article="' +
                escapeHtml(product.article) + '" data-date="' + escapeHtml(day.date) + '" title="' +
                escapeHtml(note || (day.future ? 'Будущий день' : 'Добавить запись')) + '"' + disabled + '>' +
                (entries.length ? '<span>' + escapeHtml(note) + '</span>' : '+') + '</button></td>';
        }).join('');
        return '<tr class="rnp-action-row"><th class="rnp-sticky-metric" scope="row"><strong>Лог действий</strong></th>' +
            cells + '<td class="rnp-month-total">' + Object.keys(product.actions).reduce(function (sum, day) {
                return sum + product.actions[day].length;
            }, 0) + ' записей</td><td class="rnp-month-total rnp-col-forecast">—</td></tr>';
    }

    function renderProduct(product, metrics) {
        var scope = 'product:' + product.article;
        var rowspan = metricRowCount(metrics, scope) + 2;
        return '<tbody class="rnp-product-group" data-article="' + escapeHtml(product.article) + '">' +
            strategyRow(product, rowspan) + actionRow(product) +
            renderMetricRows(product, metrics, scope, '') + '</tbody>';
    }

    function renderBody() {
        var metrics = selectedMetrics();
        if (!metrics.length) {
            els.body.innerHTML = '<tr><td class="rnp-no-metrics" colspan="99">Выберите хотя бы один показатель</td></tr>';
            return;
        }
        ensureDefaultExpansion(metrics);
        els.body.innerHTML = renderStoreRows(metrics) + state.data.products.map(function (product) {
            return renderProduct(product, metrics);
        }).join('');
    }

    function renderKpis() {
        var source = state.forecast ? state.data.totals.forecast : state.data.totals.fact;
        ['orders_amount', 'orders_count', 'sales_amount', 'sales_count', 'gross_profit'].forEach(function (key) {
            var definition = null;
            state.data.metrics.some(function (item) {
                if (item.id === key) { definition = item; return true; }
                definition = (item.children || []).find(function (child) { return child.id === key; }) || null;
                return Boolean(definition);
            });
            var target = root.querySelector('[data-rnp-kpi="' + key + '"]');
            target.textContent = formatValue(source[key], definition ? definition.format : 'money', false);
            var note = root.querySelector('[data-rnp-kpi-note="' + key + '"]');
            if (key !== 'gross_profit') note.textContent = state.forecast ? 'Прогноз месяца' : 'Факт месяца';
        });
    }

    function renderSync() {
        var sync = state.data.sync;
        var metricStates = state.data.metric_sync || [];
        var issue = metricStates.find(function (item) {
            return item.status === 'error' || item.status === 'waiting' ||
                item.status === 'partial' || item.status === 'unavailable';
        });
        var status = sync.status !== 'ready' ? sync.status : (issue ? 'warning' : 'ready');
        els.sync.className = 'rnp-sync is-' + status;
        els.syncLabel.textContent = sync.status !== 'ready' ? sync.label :
            (issue ? issue.label + ': данные доступны частично' : 'Все источники РНП обновлены');
        els.syncMessage.textContent = sync.status !== 'ready' ? (sync.error || 'Ожидаем первую загрузку фактов') :
            (issue ? issue.message : metricStates.map(function (item) { return item.label; }).join(' · '));
        els.sync.hidden = status === 'ready';
    }

    function renderPagination() {
        var page = state.data.pagination;
        els.pagination.hidden = page.total <= page.limit;
        var from = page.total ? page.offset + 1 : 0;
        var to = page.offset + page.shown;
        els.pageLabel.textContent = 'Показано ' + integer.format(from) + '–' + integer.format(to) + ' из ' + integer.format(page.total);
        els.prev.disabled = page.offset === 0;
        els.next.disabled = !page.has_more;
    }

    function renderMetricsMenu() {
        if (!state.selectedMetrics) {
            state.selectedMetrics = state.data.metrics.map(function (item) { return item.id; });
        }
        controls.metricsMenu.innerHTML = '<strong>Показатели таблицы</strong>' + state.data.metrics.map(function (metric) {
            var checked = state.selectedMetrics.indexOf(metric.id) !== -1 ? ' checked' : '';
            return '<label title="' + escapeHtml(metric.hint || '') + '"><input type="checkbox" value="' +
                escapeHtml(metric.id) + '"' + checked + '><span><small>' + escapeHtml(metric.group) + '</small>' +
                escapeHtml(metric.label) + '</span></label>';
        }).join('');
    }

    function render() {
        setLoading(false);
        root.classList.toggle('show-forecast', state.forecast);
        root.classList.toggle('is-compact', state.compact);
        renderHeader();
        renderKpis();
        renderSync();
        renderMetricsMenu();
        renderPagination();
        var hasProducts = state.data.products.length > 0;
        els.table.hidden = !hasProducts;
        els.empty.hidden = hasProducts;
        if (hasProducts) renderBody();
    }

    function closeModal() {
        els.modal.hidden = true;
        document.body.classList.remove('rnp-modal-open');
        state.modal = null;
    }

    function productByArticle(article) {
        return state.data.products.find(function (item) { return item.article === article; });
    }

    function openStrategy(article) {
        if (readOnly) return;
        var product = productByArticle(article);
        if (!product) return;
        var strategy = product.strategy || {};
        var period = state.data.period;
        els.modalKicker.textContent = product.name;
        els.modalTitle.textContent = strategy.strategy ? 'Изменить стратегию' : 'Добавить стратегию';
        els.modalBody.innerHTML =
            '<label><span>Стратегия</span><input name="strategy" list="rnp-strategy-options" maxlength="80" required value="' +
            escapeHtml(strategy.strategy || '') + '" placeholder="Например, В топ"><datalist id="rnp-strategy-options">' +
            '<option value="В топ"><option value="Удерживать позицию"><option value="Рост выкупов">' +
            '<option value="Распродажа"><option value="Тест цены"></datalist></label>' +
            '<div class="rnp-modal-grid"><label><span>Начало</span><input type="date" name="date_from" required value="' +
            escapeHtml(strategy.date_from || period.from) + '"></label>' +
            '<label><span>Окончание</span><input type="date" name="date_to" required value="' +
            escapeHtml(strategy.date_to || period.to) + '"></label></div>';
        state.modal = { type: 'strategy', article: article };
        openModal();
    }

    function openAction(article, actionDate) {
        if (readOnly) return;
        var product = productByArticle(article);
        if (!product) return;
        var existing = (product.actions[actionDate] || []).map(function (item) { return item.note; });
        els.modalKicker.textContent = product.name;
        els.modalTitle.textContent = 'Запись в лог действий';
        els.modalBody.innerHTML =
            '<label><span>Дата</span><input type="date" name="action_date" readonly value="' + escapeHtml(actionDate) + '"></label>' +
            (existing.length ? '<div class="rnp-existing-notes"><span>Уже записано</span>' + existing.map(function (note) {
                return '<p>' + escapeHtml(note) + '</p>';
            }).join('') + '</div>' : '') +
            '<label><span>Что сделали</span><textarea name="note" maxlength="500" rows="5" required placeholder="Изменили цену, ставку, рекламу или карточку товара"></textarea></label>';
        state.modal = { type: 'action', article: article };
        openModal();
    }

    function openModal() {
        els.modalError.hidden = true;
        els.modalError.textContent = '';
        els.modal.hidden = false;
        document.body.classList.add('rnp-modal-open');
        var firstInput = els.modalBody.querySelector('input:not([readonly]), textarea');
        if (firstInput) firstInput.focus();
    }

    function formObject(form) {
        var result = {};
        new FormData(form).forEach(function (value, key) { result[key] = value; });
        return result;
    }

    function submitModal(event) {
        event.preventDefault();
        if (!state.modal) return;
        var payload = formObject(els.modalForm);
        payload.store = controls.store.value;
        payload.marketplace = state.marketplace;
        payload.article = state.modal.article;
        var endpoint = state.modal.type === 'strategy' ? '/api/rnp/strategy' : '/api/rnp/action';
        els.modalSubmit.disabled = true;
        els.modalError.hidden = true;
        fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(payload)
        }).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok || data.ok === false) throw new Error(data.error || 'Не удалось сохранить');
                return data;
            });
        }).then(function () {
            closeModal();
            return loadData({ resetScroll: false, message: 'Обновляем РНП' });
        }).catch(function (error) {
            els.modalError.textContent = error.message || 'Не удалось сохранить';
            els.modalError.hidden = false;
        }).finally(function () {
            els.modalSubmit.disabled = false;
        });
    }

    controls.month.addEventListener('change', function () { state.offset = 0; loadData(); });
    controls.store.addEventListener('change', function () { state.offset = 0; loadData(); });
    var searchTimer = null;
    controls.search.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () { state.offset = 0; loadData(); }, 350);
    });
    controls.marketplaces.forEach(function (button) {
        button.addEventListener('click', function () {
            state.marketplace = button.dataset.rnpMarketplace;
            state.offset = 0;
            controls.marketplaces.forEach(function (item) {
                item.classList.toggle('is-active', item === button);
            });
            loadData();
        });
    });
    controls.refresh.addEventListener('click', syncData);
    controls.forecast.addEventListener('click', function () {
        state.forecast = !state.forecast;
        controls.forecast.classList.toggle('is-active', state.forecast);
        controls.forecast.setAttribute('aria-pressed', state.forecast ? 'true' : 'false');
        root.classList.toggle('show-forecast', state.forecast);
        renderKpis();
    });
    controls.density.addEventListener('click', function () {
        state.compact = !state.compact;
        root.classList.toggle('is-compact', state.compact);
        controls.density.querySelector('strong').textContent = state.compact ? '2' : '1';
    });
    controls.metrics.addEventListener('click', function () {
        var open = controls.metricsMenu.hidden;
        controls.metricsMenu.hidden = !open;
        controls.metrics.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    controls.metricsMenu.addEventListener('change', function () {
        state.selectedMetrics = Array.from(controls.metricsMenu.querySelectorAll('input:checked')).map(function (input) {
            return input.value;
        });
        renderBody();
    });
    els.prev.addEventListener('click', function () {
        state.offset = Math.max(0, state.offset - state.limit);
        loadData();
    });
    els.next.addEventListener('click', function () {
        state.offset += state.limit;
        loadData();
    });
    els.body.addEventListener('click', function (event) {
        var groupButton = event.target.closest('[data-rnp-toggle-group]');
        if (groupButton) {
            var key = expansionKey(groupButton.dataset.scope, groupButton.dataset.metric);
            state.expandedGroups[key] = !state.expandedGroups[key];
            localStorage.setItem('checkstock-rnp-groups', JSON.stringify(state.expandedGroups));
            renderBody();
            return;
        }
        var strategyButton = event.target.closest('[data-rnp-edit-strategy]');
        if (strategyButton) return openStrategy(strategyButton.dataset.article);
        var actionButton = event.target.closest('[data-rnp-add-action]');
        if (actionButton && !actionButton.disabled) return openAction(actionButton.dataset.article, actionButton.dataset.date);
    });
    els.modal.addEventListener('click', function (event) {
        if (event.target.closest('[data-rnp-modal-close]')) closeModal();
    });
    els.modalForm.addEventListener('submit', submitModal);
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !els.modal.hidden) closeModal();
    });
    document.addEventListener('click', function (event) {
        if (!controls.metricsMenu.hidden && !event.target.closest('[data-rnp-metrics-toggle]') &&
            !event.target.closest('[data-rnp-metrics-menu]')) {
            controls.metricsMenu.hidden = true;
            controls.metrics.setAttribute('aria-expanded', 'false');
        }
    });

    root.classList.add('show-forecast');
    loadData();
})();
