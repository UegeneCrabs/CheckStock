(function () {
    'use strict';

    var root = document.getElementById('unit-economics-1c-report');
    var configNode = document.getElementById('ue1cr-config');
    if (!root || !configNode) return;

    var config = JSON.parse(configNode.textContent || '{}');
    var nodes = {
        form: document.getElementById('ue1cr-filters'),
        from: document.getElementById('ue1cr-date-from'),
        to: document.getElementById('ue1cr-date-to'),
        submit: document.getElementById('ue1cr-submit'),
        export: document.getElementById('ue1cr-export'),
        table: root.querySelector('.ue1cr-table'),
        rows: document.getElementById('ue1cr-rows'),
        empty: document.getElementById('ue1cr-empty'),
        error: document.getElementById('ue1cr-error'),
        scopeNote: document.getElementById('ue1cr-scope-note'),
        marginCoverageNote: document.getElementById('ue1cr-margin-coverage-note'),
        showImages: document.getElementById('ue1cr-show-images'),
        articleSearch: document.getElementById('ue1cr-article-search'),
        storeOptions: document.getElementById('ue1cr-store-options'),
        subjectOptions: document.getElementById('ue1cr-subject-options'),
        managerOptions: document.getElementById('ue1cr-manager-options'),
        articleOptions: document.getElementById('ue1cr-article-options'),
        storeSummary: document.getElementById('ue1cr-store-summary'),
        subjectSummary: document.getElementById('ue1cr-subject-summary'),
        managerSummary: document.getElementById('ue1cr-manager-summary'),
        articleSummary: document.getElementById('ue1cr-article-summary')
    };
    var state = {
        store: new Set(),
        subject: new Set(),
        manager: new Set(),
        article: new Set(),
        rows: [],
        articleOptions: [],
        showImages: false,
        totals: null
    };
    var optionNodes = {
        store: nodes.storeOptions,
        subject: nodes.subjectOptions,
        manager: nodes.managerOptions,
        article: nodes.articleOptions
    };
    var summaryNodes = {
        store: nodes.storeSummary,
        subject: nodes.subjectSummary,
        manager: nodes.managerSummary,
        article: nodes.articleSummary
    };
    var summaryLabels = {
        store: ['Все магазины', 'магазина', 'магазинов'],
        subject: ['Все предметы', 'предмета', 'предметов'],
        manager: ['Все менеджеры', 'менеджера', 'менеджеров'],
        article: ['Все товары', 'товара', 'товаров']
    };
    var number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    var integer = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
    var money = new Intl.NumberFormat('ru-RU', {
        style: 'currency', currency: 'RUB', maximumFractionDigits: 0
    });

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function value(item, suffix) {
        return item === null || item === undefined ? '—' : number.format(item) + (suffix || '');
    }
    function turnover(item) {
        return item === null || item === undefined ? '—' : integer.format(Math.round(numeric(item)));
    }
    function rub(item) { return item === null || item === undefined ? '—' : money.format(item); }
    function copyIdentifier(kind, item, label) {
        if (item === null || item === undefined || item === '') return '—';
        return '<button class="copy-identifier" type="button" data-copy-kind="' + escapeHtml(kind)
            + '" data-copy-value="' + escapeHtml(item)
            + '" data-copy-tooltip="Нажмите, чтобы скопировать" aria-label="Скопировать '
            + escapeHtml(kind.toLocaleLowerCase('ru-RU')) + ' ' + escapeHtml(item) + '">'
            + escapeHtml(label) + '</button>';
    }
    function tone(item) {
        if (item > 0) return ' metric--positive';
        if (item < 0) return ' metric--negative';
        return '';
    }
    function marginCoverageTitle(row) {
        if (row.margin_complete !== false) return '';
        var days = Array.isArray(row.margin_missing_days) ? row.margin_missing_days.join(', ') : '';
        return ' title="Нет исторического снимка маржи'
            + (days ? ' за: ' + escapeHtml(days) : ' за часть периода') + '"';
    }
    function summary(kind) {
        var count = state[kind].size;
        var labels = summaryLabels[kind];
        if (!summaryNodes[kind]) return;
        summaryNodes[kind].textContent = count ? count + ' ' + (count < 5 ? labels[1] : labels[2]) : labels[0];
    }
    function optionHtml(kind, item) {
        var rawValue = kind === 'store' ? item.slug : kind === 'article' ? item.article : item;
        var checked = state[kind].has(String(rawValue));
        var label = kind === 'store' ? item.name : String(item);
        if (kind === 'article') {
            label = '<span><strong>' + escapeHtml(item.name) + '</strong><small>Арт. '
                + escapeHtml(item.article) + ' · ' + escapeHtml(item.store_name) + '</small></span>';
        } else label = '<span>' + escapeHtml(label) + '</span>';
        return '<label><input type="checkbox" data-filter-option="' + kind + '" value="'
            + escapeHtml(rawValue) + '"' + (checked ? ' checked' : '') + '>' + label + '</label>';
    }
    function renderOptions(kind, items) {
        if (!optionNodes[kind]) return;
        optionNodes[kind].innerHTML = items.length
            ? items.map(function (item) { return optionHtml(kind, item); }).join('')
            : '<div class="ue1cr-no-options">Нет вариантов</div>';
        summary(kind);
    }
    function renderArticleOptions() {
        var query = nodes.articleSearch.value.trim().toLocaleLowerCase('ru-RU');
        var filtered = state.articleOptions.filter(function (item) {
            return !query || [item.article, item.name, item.store_name].join(' ')
                .toLocaleLowerCase('ru-RU').indexOf(query) !== -1;
        }).slice(0, 200);
        renderOptions('article', filtered);
    }
    function productMedia(row) {
        if (!state.showImages || !row.image_url) return '';
        return '<img src="' + escapeHtml(row.image_url) + '" alt="" loading="lazy">';
    }
    function rowHtml(row, index) {
        return '<tr data-row-index="' + index + '"><td><div class="ue1cr-product-cell">'
            + productMedia(row) + '<div><strong>'
            + escapeHtml(row.name) + '</strong><span>'
            + copyIdentifier('Артикул', row.article, 'Арт. ' + row.article)
            + '</span></div></div></td><td>' + escapeHtml(row.store_name)
            + '</td><td>' + escapeHtml(row.subject) + '</td><td>' + escapeHtml(row.manager || '—')
            + '</td><td class="num">' + value(row.orders_count) + '</td><td class="num">'
            + value(row.cancel_count) + '</td><td class="num">' + value(row.net_orders_count)
            + '</td><td class="num">' + turnover(row.orders_amount) + '</td><td class="num">'
            + turnover(row.cancel_amount) + '</td><td class="num">' + turnover(row.net_orders_amount)
            + '</td><td class="num">' + value(row.buyout_percent, '%') + '</td><td class="num">'
            + value(row.stock) + '</td><td class="num">' + value(row.impressions) + '</td><td class="num">'
            + value(row.clicks) + '</td><td class="num">' + value(row.ctr, '%')
            + '</td><td class="num">' + rub(row.cpc) + '</td><td class="num">'
            + rub(row.advertising_spend) + '</td><td class="num">' + value(row.margin_orders_count)
            + '</td><td class="num' + tone(row.margin) + '"' + marginCoverageTitle(row) + '>' + rub(row.margin)
            + '</td><td class="num"' + marginCoverageTitle(row) + '>' + rub(row.purchase_value) + '</td><td class="num'
            + tone(row.roi) + '"' + marginCoverageTitle(row) + '>' + value(row.roi, '%') + '</td></tr>';
    }
    function setHeaderTotal(key, text, toneValue) {
        var node = root.querySelector('[data-report-total="' + key + '"]');
        if (!node) return;
        node.textContent = text;
        node.classList.remove('metric--positive', 'metric--negative');
        if (toneValue > 0) node.classList.add('metric--positive');
        if (toneValue < 0) node.classList.add('metric--negative');
    }
    function renderHeaderTotals(total, rowCount) {
        var countNode = root.querySelector('[data-report-total-count]');
        if (countNode) countNode.textContent = 'Итого: ' + value(rowCount) + ' поз.';
        setHeaderTotal('orders_count', value(total.orders_count));
        setHeaderTotal('cancel_count', value(total.cancel_count));
        setHeaderTotal('net_orders_count', value(total.net_orders_count));
        setHeaderTotal('orders_amount', rub(total.orders_amount));
        setHeaderTotal('cancel_amount', rub(total.cancel_amount));
        setHeaderTotal('net_orders_amount', rub(total.net_orders_amount));
        setHeaderTotal('buyout_percent', value(total.buyout_percent, '%'));
        setHeaderTotal('stock', value(total.stock));
        setHeaderTotal('impressions', value(total.impressions));
        setHeaderTotal('clicks', value(total.clicks));
        setHeaderTotal('ctr', value(total.ctr, '%'));
        setHeaderTotal('cpc', rub(total.cpc));
        setHeaderTotal('advertising_spend', rub(total.advertising_spend));
        setHeaderTotal('margin_orders_count', value(total.margin_orders_count));
        setHeaderTotal('margin', rub(total.margin), total.margin);
        setHeaderTotal('purchase_value', rub(total.purchase_value));
        setHeaderTotal('roi', value(total.roi, '%'), total.roi);
    }
    function numeric(item) {
        var parsed = Number(item);
        return Number.isFinite(parsed) ? parsed : 0;
    }
    function sortRowsByTurnover(rows) {
        return rows.slice().sort(function (a, b) {
            var turnoverDifference = numeric(b.orders_amount) - numeric(a.orders_amount);
            if (turnoverDifference) return turnoverDifference;
            return String(a.name || '').localeCompare(String(b.name || ''), 'ru');
        });
    }
    function aggregateRows(rows) {
        var total = rows.reduce(function (result, row) {
            result.orders_count += numeric(row.orders_count);
            result.cancel_count += numeric(row.cancel_count);
            result.net_orders_count += numeric(row.net_orders_count);
            result.orders_amount += numeric(row.orders_amount);
            result.cancel_amount += numeric(row.cancel_amount);
            result.net_orders_amount += numeric(row.net_orders_amount);
            result.buyout_orders_count += numeric(row.buyout_orders_count);
            result.buyout_weighted += numeric(row.buyout_percent) * numeric(row.buyout_orders_count);
            result.stock += numeric(row.stock);
            result.impressions += numeric(row.impressions);
            result.clicks += numeric(row.clicks);
            result.advertising_spend += numeric(row.advertising_spend);
            result.margin += numeric(row.margin);
            result.margin_orders_count += numeric(row.margin_orders_count);
            result.purchase_value += numeric(row.purchase_value);
            if (row.margin_complete === false) result.margin_complete = false;
            return result;
        }, {
            orders_count: 0, cancel_count: 0, net_orders_count: 0,
            orders_amount: 0, cancel_amount: 0, net_orders_amount: 0,
            buyout_orders_count: 0, buyout_weighted: 0, stock: 0,
            impressions: 0, clicks: 0, advertising_spend: 0,
            margin_orders_count: 0, margin: 0, purchase_value: 0, margin_complete: true
        });
        total.orders_amount = Math.round(total.orders_amount * 100) / 100;
        total.cancel_amount = Math.round(total.cancel_amount * 100) / 100;
        total.net_orders_amount = Math.round(total.net_orders_amount * 100) / 100;
        total.advertising_spend = Math.round(total.advertising_spend * 100) / 100;
        total.margin = Math.round(total.margin * 100) / 100;
        total.purchase_value = Math.round(total.purchase_value * 100) / 100;
        total.buyout_percent = total.buyout_orders_count
            ? Math.round(total.buyout_weighted / total.buyout_orders_count * 100) / 100 : null;
        total.ctr = total.impressions
            ? Math.round(total.clicks / total.impressions * 10000) / 100 : 0;
        total.cpc = total.clicks
            ? Math.round(total.advertising_spend / total.clicks * 100) / 100 : 0;
        if (total.margin_complete) {
            total.roi = total.purchase_value
                ? Math.round(total.margin / total.purchase_value * 10000) / 100 : 0;
        } else {
            total.margin = null;
            total.purchase_value = null;
            total.roi = null;
        }
        return total;
    }
    function visibleReportRows() {
        return Array.prototype.map.call(nodes.rows.querySelectorAll('tr[data-row-index]'), function (row) {
            if (row.style.display === 'none') return null;
            return state.rows[Number(row.dataset.rowIndex)] || null;
        }).filter(Boolean);
    }
    function updateVisibleTotal() {
        var rows = visibleReportRows();
        renderHeaderTotals(aggregateRows(rows), rows.length);
    }
    function renderRows() {
        nodes.rows.innerHTML = state.rows.map(rowHtml).join('');
        renderHeaderTotals(state.rows.length ? state.totals : aggregateRows([]), state.rows.length);
        nodes.empty.hidden = state.rows.length > 0;
        if (window.CheckStockTableFilter && typeof window.CheckStockTableFilter.refresh === 'function') {
            window.CheckStockTableFilter.refresh(nodes.table);
        } else updateVisibleTotal();
    }
    function appendFilters(query, kind, parameter) {
        state[kind].forEach(function (item) { query.append(parameter, item); });
    }
    function reportQuery() {
        var query = new URLSearchParams({ date_from: nodes.from.value, date_to: nodes.to.value });
        appendFilters(query, 'store', 'store');
        appendFilters(query, 'subject', 'subject');
        appendFilters(query, 'manager', 'manager');
        appendFilters(query, 'article', 'article');
        return query;
    }
    function updateExportLink() {
        if (!nodes.export) return;
        nodes.export.href = '/sales/unit-economics-1c/reports/unit-profit.xlsx?' + reportQuery().toString();
    }
    async function load() {
        nodes.submit.disabled = true;
        nodes.submit.textContent = 'Считаем…';
        nodes.error.hidden = true;
        nodes.scopeNote.hidden = true;
        nodes.marginCoverageNote.hidden = true;
        var query = reportQuery();
        updateExportLink();
        try {
            var response = await window.fetch('/api/unit-economics-1c/reports/unit-profit?' + query.toString(), {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'Не удалось сформировать отчёт');
            state.rows = sortRowsByTurnover(result.rows || []);
            state.totals = result.totals;
            state.articleOptions = result.filters.articles || [];
            renderOptions('subject', result.filters.subjects || []);
            renderOptions('manager', result.filters.managers || []);
            renderArticleOptions();
            renderRows();
            if (result.totals && result.totals.margin_complete === false) {
                var missingDays = result.totals.margin_missing_days || [];
                nodes.marginCoverageNote.textContent = 'Маржа не рассчитана полностью: нет дневных снимков'
                    + (missingDays.length ? ' за ' + missingDays.join(', ') : ' за часть периода') + '.';
                nodes.marginCoverageNote.hidden = false;
            }
            if (result.manager_scope && result.manager_scope.restricted && !result.manager_scope.matched) {
                nodes.scopeNote.textContent = 'В данных 1С не найден менеджер, соответствующий вашему пользователю.';
                nodes.scopeNote.hidden = false;
            }
        } catch (error) {
            nodes.error.textContent = error.message || 'Не удалось сформировать отчёт';
            nodes.error.hidden = false;
        } finally {
            nodes.submit.disabled = false;
            nodes.submit.textContent = 'Показать';
        }
    }

    root.addEventListener('change', function (event) {
        var kind = event.target.dataset.filterOption;
        if (!kind) {
            updateExportLink();
            return;
        }
        if (event.target.checked) state[kind].add(event.target.value);
        else state[kind].delete(event.target.value);
        if (kind === 'store') {
            ['subject', 'manager', 'article'].forEach(function (child) {
                state[child].clear();
                summary(child);
            });
            nodes.articleSearch.value = '';
        }
        summary(kind);
        updateExportLink();
    });
    nodes.showImages.addEventListener('change', function () {
        state.showImages = nodes.showImages.checked;
        renderRows();
    });
    nodes.articleSearch.addEventListener('input', renderArticleOptions);
    nodes.articleSearch.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') event.preventDefault();
    });
    nodes.table.addEventListener('tablefilterchange', updateVisibleTotal);
    nodes.form.addEventListener('submit', function (event) { event.preventDefault(); load(); });
    nodes.from.value = config.defaultDateFrom;
    nodes.to.value = config.defaultDateTo;
    renderOptions('store', config.stores || []);
    renderOptions('subject', []);
    renderOptions('manager', []);
    renderArticleOptions();
    updateExportLink();
    load();
})();
