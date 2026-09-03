(function () {
    'use strict';

    var root = document.getElementById('unit-economics-1c');
    var configNode = document.getElementById('ue1c-config');
    if (!root || !configNode) return;

    var config = JSON.parse(configNode.textContent || '{}');
    var products = Array.isArray(config.products) ? config.products : [];
    var productsEndpoint = String(config.productsEndpoint || '/sales/unit-economics-1c?data=1');
    var commissionsEndpoint = String(
        config.commissionsEndpoint || '/sales/unit-economics-1c?data=1&commissions=1'
    );
    var stores = Array.isArray(config.stores) ? config.stores : [];
    var subjectCommissions = Array.isArray(config.subjectCommissions) ? config.subjectCommissions : [];
    var commissionsPromise = null;
    var AUTO_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
    var AUTO_REFRESH_STALE_MS = 60 * 1000;
    var lastProductsLoadedAt = 0;
    var canEdit = config.canEdit === true;
    var productsById = {};
    products.forEach(function (product) {
        product._detailLoaded = Boolean(product.details && Array.isArray(product.history));
        productsById[product.id] = product;
    });

    var integer = new Intl.NumberFormat('ru-RU');
    var decimal = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    var money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
    var preciseMoney = new Intl.NumberFormat('ru-RU', {
        style: 'currency', currency: 'RUB', minimumFractionDigits: 2, maximumFractionDigits: 2
    });
    var userKey = String(config.userKey || 'anonymous');
    var commentsKey = 'checkstock.unit-economics-1c.comments.' + userKey;
    var columnsKey = 'checkstock.unit-economics-1c.columns.' + userKey;
    var chartKey = 'checkstock.unit-economics-1c.chart.' + userKey;
    var priceJobsKey = 'checkstock.unit-economics-1c.price-jobs.' + userKey;
    var comments = readJson(commentsKey, {});
    var pendingPriceJobs = readJson(priceJobsKey, []);
    if (!Array.isArray(pendingPriceJobs)) pendingPriceJobs = [];
    var configuredPeriodDays = [7, 14, 30].indexOf(Number(config.periodDays)) !== -1
        ? Number(config.periodDays) : 7;
    var maxPeriodDays = Math.max(1, Number(config.maxPeriodDays) || 366);
    var toastTimer = 0;
    var rowHighlightTimer = 0;
    var columnSaveTimer = 0;
    var draggedColumnKey = null;
    var sendingPrice = false;
    var editedPriceKind = 'retail';
    var pendingPriceChange = null;
    var state = {
        query: '', store: 'all', status: 'all', page: 1, pageSize: 20,
        periodMode: 'preset', periodDays: configuredPeriodDays,
        periodFrom: String(config.periodFrom || ''), periodTo: String(config.periodTo || ''),
        lastCompleteDay: String(config.lastCompleteDay || config.periodTo || ''),
        selected: null, sortColumn: 4, sortDirection: -1, tableFilters: {},
        productsLoading: products.length === 0, productsRefreshing: false
    };
    var nodes = {
        table: id('ue1c-table'), rows: id('ue1c-product-rows'), empty: id('ue1c-empty'), search: id('ue1c-search'),
        store: id('ue1c-store-filter'), periodDays: id('ue1c-period-days'),
        periodFrom: id('ue1c-period-from'), periodTo: id('ue1c-period-to'),
        periodApply: id('ue1c-period-apply'),
        tableWrap: id('ue1c-table-wrap'), pageSize: id('ue1c-page-size'),
        pagePrev: id('ue1c-page-prev'), pageNext: id('ue1c-page-next'), pageNumbers: id('ue1c-page-numbers'),
        summary: id('ue1c-pagination-summary'), overlay: id('ue1c-overlay'),
        productsLoading: id('ue1c-products-loading'), productsError: id('ue1c-products-error'),
        productsErrorText: id('ue1c-products-error-text'), productsRetry: id('ue1c-products-retry'),
        colgroup: id('ue1c-colgroup'), tableHead: id('ue1c-table-head'),
        columnsToggle: id('ue1c-columns-toggle'), columnPanel: id('ue1c-column-panel'),
        columnList: id('ue1c-column-list'),
        detail: id('ue1c-detail'), detailClose: id('ue1c-detail-close'), drawerThumb: id('ue1c-drawer-thumb'),
        detailLoading: id('ue1c-detail-loading'),
        drawerTitle: id('ue1c-drawer-title'), drawerMeta: id('ue1c-drawer-meta'), priceInput: id('ue1c-price-input'),
        sppPriceInput: id('ue1c-spp-price-input'), walletPriceInput: id('ue1c-wallet-price-input'),
        calculatorReset: id('ue1c-calculator-reset'), calculatorMode: id('ue1c-calculator-mode'),
        calculatorFields: id('ue1c-calculator-inputs'),
        subjectSelect: id('ue1c-subject-select'),
        subjectOptions: id('ue1c-subject-options'),
        calculatorInputs: root.querySelectorAll('[data-calculator-input]'),
        breakEven: id('ue1c-break-even'), savePrice: id('ue1c-save-price'),
        priceMetrics: id('ue1c-price-metrics'), parameters: id('ue1c-parameter-groups'),
        secondaryTaxLabel: id('ue1c-secondary-tax-label'),
        chart: id('ue1c-chart'), chartWrap: id('ue1c-chart-wrap'), chartTooltip: id('ue1c-chart-tooltip'),
        chartSeriesTooltip: id('ue1c-chart-series-tooltip'),
        chartDailySales: id('ue1c-chart-daily-sales'),
        gluedSection: id('ue1c-glued-section'), gluedProducts: id('ue1c-glued-products'),
        confirmModal: id('ue1c-price-confirm-modal'), confirmClose: id('ue1c-price-confirm-close'),
        confirmCancel: id('ue1c-price-confirm-cancel'), confirmSend: id('ue1c-price-confirm-send'),
        confirmProduct: id('ue1c-price-confirm-product'), confirmTarget: id('ue1c-price-confirm-target'),
        confirmGrid: id('ue1c-price-confirm-grid'), confirmWarning: id('ue1c-price-confirm-warning'),
        toast: id('ue1c-toast')
    };

    var columnGroups = [
        { key: 'product', label: 'Товар', fixed: true, columns: [{ index: 0, label: 'Товар', width: 260 }] },
        { key: 'newness', label: 'Новинка', columns: [{ index: 23, label: 'Новинка', width: 90 }] },
        { key: 'comments', label: 'Комментарии', columns: [{ index: 1, label: 'Комментарии', width: 260 }] },
        { key: 'current', label: 'Текущая экономика', columns: [
            {
                index: 2,
                label: 'Маржа на шт., ₽',
                help: 'Потенциальная чистая прибыль с одной выкупленной единицы по текущим ценам, комиссиям, налогам и расходам за сегодняшний день.',
                number: true,
                width: 112
            },
            { index: 3, label: 'ROI, %', number: true, width: 85 },
            { index: 24, label: 'СПП, %', number: true, width: 82 }
        ] },
        { key: 'actual', label: 'Экономика за 7 дней', columns: [
            { index: 4, label: 'ТО, ₽', number: true, width: 105 },
            { index: 5, label: 'Маржа, ₽', number: true, width: 105 },
            { index: 6, label: 'ROI, %', number: true, width: 85 }
        ] },
        { key: 'advertising', label: 'Реклама за 7 дней', columns: [
            { index: 7, label: 'ДРР с выкупом, %', number: true, width: 115 },
            { index: 8, label: 'Затраты, ₽', number: true, width: 100 },
            { index: 9, label: 'CTR, %', number: true, width: 78 },
            { index: 10, label: 'CPC, ₽', number: true, width: 78 }
        ] },
        { key: 'tag', label: 'Тег', columns: [
            { index: 11, label: 'Цель неделя', number: true, width: 90 },
            { index: 12, label: 'Цель день', number: true, width: 80 },
            { index: 13, label: 'Статус стока', width: 100 },
            { index: 14, label: 'Сток закончится', width: 105 },
            { index: 15, label: 'Код товара', width: 85 },
            { index: 16, label: 'Факт прош. недели', number: true, width: 105 },
            { index: 17, label: 'План прош. недели', number: true, width: 105 }
        ] },
        { key: 'stock', label: 'Остатки', columns: [
            { index: 18, label: 'Всего', number: true, width: 75 },
            { index: 19, label: 'FBS', number: true, width: 65 },
            { index: 20, label: 'FBO', number: true, width: 65 },
            { index: 21, label: 'ФФ', number: true, width: 65 },
            { index: 22, label: 'Хватит, дней', number: true, width: 90 }
        ] }
    ];
    var defaultColumnOrder = columnGroups.map(function (group) { return group.key; });
    var savedColumns = config.columnPreferences && Array.isArray(config.columnPreferences.order)
        ? config.columnPreferences : readJson(columnsKey, {});
    var columnPreferences = {
        order: Array.isArray(savedColumns.order) ? savedColumns.order.filter(function (key) {
            return defaultColumnOrder.indexOf(key) !== -1;
        }) : defaultColumnOrder.slice(),
        hidden: Array.isArray(savedColumns.hidden) ? savedColumns.hidden.slice() : []
    };
    columnPreferences.order = ['product'].concat(columnPreferences.order.filter(function (key) {
        return key !== 'product';
    }));
    if (Array.isArray(savedColumns.order) && columnPreferences.order.indexOf('newness') === -1) {
        columnPreferences.order.splice(Math.max(columnPreferences.order.indexOf('product') + 1, 0), 0, 'newness');
    }
    defaultColumnOrder.forEach(function (key) {
        if (columnPreferences.order.indexOf(key) === -1) columnPreferences.order.push(key);
    });
    var savedChart = readJson(chartKey, {});
    var chartSeries = ['orders', 'stock', 'margin', 'ads', 'drr'];
    var chartPreferences = {
        series: Array.isArray(savedChart.series) ? savedChart.series.filter(function (key) {
            return chartSeries.indexOf(key) !== -1;
        }) : chartSeries.slice(),
        compare: savedChart.compare === true
    };
    if (!chartPreferences.series.length) chartPreferences.series = ['orders', 'stock'];

    function id(value) { return document.getElementById(value); }
    function readJson(key, fallback) {
        try {
            var parsed = JSON.parse(window.localStorage.getItem(key));
            return parsed && typeof parsed === 'object' ? parsed : fallback;
        } catch (error) { return fallback; }
    }
    function writeJson(key, value) {
        try { window.localStorage.setItem(key, JSON.stringify(value)); return true; }
        catch (error) { return false; }
    }
    function validIsoDay(value) {
        var text = String(value || '');
        if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
        var parts = text.split('-').map(Number);
        var parsed = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
        return parsed.toISOString().slice(0, 10) === text;
    }
    function periodDayCount(dateFrom, dateTo) {
        if (!validIsoDay(dateFrom) || !validIsoDay(dateTo)) return 0;
        var fromParts = dateFrom.split('-').map(Number);
        var toParts = dateTo.split('-').map(Number);
        return Math.floor(
            (Date.UTC(toParts[0], toParts[1] - 1, toParts[2])
                - Date.UTC(fromParts[0], fromParts[1] - 1, fromParts[2])) / 86400000
        ) + 1;
    }
    function shiftIsoDay(value, offset) {
        if (!validIsoDay(value)) return '';
        var parts = value.split('-').map(Number);
        var parsed = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2] + offset));
        return parsed.toISOString().slice(0, 10);
    }
    function shortPeriodDate(value) {
        var parts = String(value || '').split('-');
        return parts.length === 3 ? parts[2] + '.' + parts[1] : String(value || '');
    }
    function syncPeriodControls() {
        nodes.periodDays.value = state.periodMode === 'custom' ? 'custom' : String(state.periodDays);
        nodes.periodFrom.value = state.periodFrom;
        nodes.periodTo.value = state.periodTo;
        nodes.periodFrom.max = state.lastCompleteDay;
        nodes.periodTo.max = state.lastCompleteDay;
    }
    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function finite(value, fallback) {
        if (value === null || value === undefined || value === '') return fallback;
        var parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }
    function negativeValueClass(value) {
        var parsed = finite(value, null);
        return parsed !== null && parsed < 0 ? ' ue1c-roi-negative' : '';
    }
    function pluralProducts(value) {
        var mod10 = value % 10;
        var mod100 = value % 100;
        if (mod10 === 1 && mod100 !== 11) return 'товар';
        if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'товара';
        return 'товаров';
    }
    function commentText(productId) {
        var saved = comments[productId];
        if (typeof saved === 'string') return saved;
        if (!saved || typeof saved !== 'object') return '';
        return [saved.first, saved.second].filter(Boolean).join('\n');
    }
    function setCommentText(productId, value) {
        var normalized = String(value || '').replace(/\r/g, '').trim();
        if (!normalized) { delete comments[productId]; return; }
        var lines = normalized.split('\n');
        comments[productId] = { first: String(lines.shift() || '').trim(), second: lines.join('\n').trim() };
    }
    function calculateSppPercent(product) {
        var price = product.price || {};
        var withoutSpp = finite(price.current, null);
        var withSpp = finite(price.with_spp, null);
        if (withoutSpp === null || withoutSpp <= 0 || withSpp === null) return null;
        return (withoutSpp - withSpp) / withoutSpp * 100;
    }
    function nullText(value) {
        return value === null || value === undefined || value === '' ? '—' : String(value);
    }
    function coverageDate(value) {
        var parts = String(value || '').split('-');
        return parts.length === 3 ? parts[2] + '.' + parts[1] + '.' + parts[0] : String(value || '');
    }
    function coverageTitle(label, coverage) {
        if (!coverage || !Array.isArray(coverage.dates) || !coverage.dates.length) {
            return label + ': данных для расчёта нет';
        }
        return label + ': данные за ' + coverage.dates.map(coverageDate).join(', ')
            + ' (' + integer.format(finite(coverage.days, coverage.dates.length)) + ' из '
            + integer.format(finite(coverage.expected_days, state.periodDays)) + ' дней)';
    }
    function isPartialCoverage(coverage) {
        return Boolean(coverage && finite(coverage.days, 0) > 0 && coverage.complete !== true);
    }
    function coverageCellClass(coverage) {
        return isPartialCoverage(coverage) ? ' ue1c-partial-cell' : '';
    }
    function coverageValue(label, value, formatter, coverage, suffix) {
        if (value === null || value === undefined || value === '') return '—';
        var partial = isPartialCoverage(coverage);
        return '<span class="ue1c-coverage-value' + (partial ? ' is-partial' : '') + '" title="'
            + escapeHtml(coverageTitle(label, coverage)) + '">' + formatter.format(value)
            + (suffix || '') + (partial ? '<sup>*</sup>' : '') + '</span>';
    }
    function moneyOrNull(value) {
        var parsed = finite(value, null);
        return parsed === null ? '—' : money.format(parsed);
    }
    function tagData(product) {
        if (product.tag_data) return product.tag_data;
        var match = String(product.tag || '').match(
            /([^/]+)\/([^/]+)\/([^/]+)\/([^|]+)\|([^/]+)\/\s*Ф:([^/]+)\/\s*П:(.+)/
        );
        return match ? {
            goal_week: match[1].trim(), goal_day: match[2].trim(), status: match[3].trim(),
            ends: match[4].trim(), code: match[5].trim(), fact: match[6].trim(), plan: match[7].trim()
        } : { goal_week: null, goal_day: null, status: null, ends: null, code: null, fact: null, plan: null };
    }
    function mediaHtml(product, className) {
        var initial = (product.name || product.article || '?').slice(0, 1).toUpperCase();
        var image = product.image_url && /^https?:\/\//.test(product.image_url)
            ? '<img src="' + escapeHtml(product.image_url) + '" alt="" loading="lazy" decoding="async">' : '';
        return '<span class="' + className + '" aria-hidden="true"><span>'
            + escapeHtml(initial) + '</span>' + image + '</span>';
    }
    function copyValue(label, value, kind) {
        if (value === null || value === undefined || value === '') {
            return '<span>' + escapeHtml(label) + ' —</span>';
        }
        var copyKind = kind || (/баркод/i.test(label) ? 'Баркод' : 'Артикул');
        return '<button class="ue1c-copy-value" type="button" data-copy-value="' + escapeHtml(value)
            + '" data-copy-kind="' + escapeHtml(copyKind)
            + '" data-copy-tooltip="Нажмите, чтобы скопировать" aria-label="Скопировать '
            + escapeHtml(copyKind.toLocaleLowerCase('ru-RU')) + ' ' + escapeHtml(value) + '">'
            + (label ? escapeHtml(label) + ' ' : '') + '<strong>' + escapeHtml(value) + '</strong></button>';
    }
    function groupByKey(key) {
        return columnGroups.find(function (group) { return group.key === key; });
    }
    function visibleColumnGroups() {
        return columnPreferences.order.map(groupByKey).filter(function (group) {
            return group && (group.fixed || columnPreferences.hidden.indexOf(group.key) === -1);
        });
    }
    function pluralDays(value) {
        var mod10 = value % 10;
        var mod100 = value % 100;
        if (mod10 === 1 && mod100 !== 11) return 'день';
        if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'дня';
        return 'дней';
    }
    function columnGroupLabel(group) {
        var customRange = state.periodMode === 'custom' && state.periodFrom && state.periodTo
            ? shortPeriodDate(state.periodFrom) + '–' + shortPeriodDate(state.periodTo) : '';
        if (group.key === 'actual' && customRange) return 'Экономика ' + customRange;
        if (group.key === 'advertising' && customRange) return 'Реклама ' + customRange;
        if (group.key === 'actual') return 'Экономика за ' + state.periodDays + ' ' + pluralDays(state.periodDays);
        if (group.key === 'advertising') return 'Реклама за ' + state.periodDays + ' ' + pluralDays(state.periodDays);
        return group.label;
    }
    function columnLabel(column) {
        var label = escapeHtml(column.label);
        if (!column.help) return label;
        return label + '<span class="ue1c-header-help" title="' + escapeHtml(column.help)
            + '" aria-label="' + escapeHtml(column.help) + '">?</span>';
    }
    function renderTableHeader() {
        var groups = visibleColumnGroups();
        nodes.colgroup.innerHTML = groups.map(function (group) {
            return group.columns.map(function (column) {
                return '<col data-column-group="' + group.key + '" style="width:' + column.width + 'px">';
            }).join('');
        }).join('');
        var top = '';
        var sub = '';
        groups.forEach(function (group) {
            if (group.columns.length === 1) {
                var only = group.columns[0];
                top += '<th rowspan="2" data-column-group="' + group.key + '" data-filter-column="'
                    + only.index + '">' + columnLabel(only) + '</th>';
                return;
            }
            top += '<th colspan="' + group.columns.length + '" data-column-group="' + group.key
                + '" class="ue1c-header-group ue1c-group-start">'
                + escapeHtml(columnGroupLabel(group)) + '</th>';
            sub += group.columns.map(function (column, index) {
                return '<th data-column-group="' + group.key + '" class="' + (column.number ? 'ue1c-num ' : '')
                    + (index === 0 ? 'ue1c-group-start' : '') + '" data-filter-column="'
                    + column.index + '"' + (column.number ? ' data-filter-type="number"' : '') + '>'
                    + columnLabel(column) + '</th>';
            }).join('');
        });
        nodes.tableHead.innerHTML = '<tr class="ue1c-group-row">' + top
            + '</tr><tr class="ue1c-subhead-row">' + sub + '</tr>';
    }
    function saveColumnPreferences() {
        columnPreferences.order = ['product'].concat(columnPreferences.order.filter(function (key) {
            return key !== 'product';
        }));
        writeJson(columnsKey, columnPreferences);
        window.clearTimeout(columnSaveTimer);
        columnSaveTimer = window.setTimeout(function () {
            window.fetch('/api/unit-economics-1c/preferences/columns', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify(columnPreferences)
            }).catch(function () { return null; });
        }, 250);
        renderTableHeader();
        renderColumnSettings();
        renderPage();
        if (window.CheckStockTableFilter && typeof window.CheckStockTableFilter.refresh === 'function') {
            window.CheckStockTableFilter.refresh(nodes.table);
        }
    }
    function renderColumnSettings() {
        nodes.columnList.innerHTML = columnPreferences.order.map(function (key, index) {
            var group = groupByKey(key);
            if (!group) return '';
            var checked = group.fixed || columnPreferences.hidden.indexOf(key) === -1;
            return '<div class="ue1c-column-option" data-column-key="' + key + '" draggable="'
                + (group.fixed ? 'false' : 'true') + '"'
                + ' aria-grabbed="false"><span class="ue1c-column-grip" title="Перетащить">⋮⋮</span>'
                + '<label><input type="checkbox"'
                + (checked ? ' checked' : '') + (group.fixed ? ' disabled' : '') + '> <span>'
                + escapeHtml(group.label) + '</span></label><div><button type="button" data-column-move="up"'
                + (group.fixed || index <= 1 ? ' disabled' : '') + ' aria-label="Выше">↑</button><button type="button"'
                + ' data-column-move="down"' + (index === columnPreferences.order.length - 1 ? ' disabled' : '')
                + (group.fixed ? ' disabled' : '')
                + ' aria-label="Ниже">↓</button></div></div>';
        }).join('');
    }
    function productState(product) {
        var current = product.current_economics || {};
        if (finite(current.roi, 0) < 0) return 'negative';
        var stockState = product.stock && product.stock.state || {};
        if (stockState.is_low) return 'low';
        if (stockState.is_risk) return 'risk';
        return 'ok';
    }
    function productSearchValue(product) {
        var tag = tagData(product);
        return [product.name, product.article, product.barcode, product.store_name, commentText(product.id),
            tag.goal_week, tag.goal_day, tag.status, tag.ends, tag.code, tag.fact, tag.plan,
            product.rating, product.reviews_count, product.advertising.drr, product.advertising.spend,
            product.is_new ? 'новинка' : '', product.sales_days,
            product.advertising.ctr, product.advertising.cpc,
            product.current_economics && product.current_economics.margin,
            product.current_economics && product.current_economics.roi,
            calculateSppPercent(product),
            product.economics_7d && product.economics_7d.turnover,
            product.economics_7d && product.economics_7d.margin,
            product.economics_7d && product.economics_7d.roi, product.stock.total, product.stock.fbs,
            product.stock.fbo, product.stock.fulfillment, product.stock.days].join(' ').toLocaleLowerCase('ru-RU');
    }
    function columnValue(product, columnIndex) {
        var tag = tagData(product);
        var values = [
            [product.name, product.article, product.barcode, product.store_name].join(' · '),
            commentText(product.id),
            product.current_economics && product.current_economics.margin,
            product.current_economics && product.current_economics.roi,
            product.economics_7d && product.economics_7d.turnover,
            product.economics_7d && product.economics_7d.margin,
            product.economics_7d && product.economics_7d.roi,
            product.advertising.drr, product.advertising.spend,
            product.advertising.ctr, product.advertising.cpc,
            tag.goal_week, tag.goal_day, tag.status, tag.ends, tag.code, tag.fact, tag.plan,
            product.stock.total, product.stock.fbs, product.stock.fbo,
            product.stock.fulfillment, product.stock.days,
            product.is_new ? 'Новинка' : 'Нет',
            calculateSppPercent(product)
        ];
        var value = values[Number(columnIndex)];
        return String(value === null || value === undefined ? '—' : value);
    }
    function renderStores() {
        stores.forEach(function (store) {
            var option = document.createElement('option');
            option.value = store.slug;
            option.textContent = store.name;
            nodes.store.appendChild(option);
        });
    }
    function renderProduct(product) {
        var tag = tagData(product);
        var rowClasses = [];
        if (state.selected === product.id) rowClasses.push('is-selected');
        var status = tag.status ? String(tag.status).toLowerCase() : 'unknown';
        var drr = finite(product.advertising.drr, null);
        var drrClass = drr !== null && drr >= 18 ? ' is-high' : (drr !== null && drr >= 12 ? ' is-medium' : '');
        var advertisingTitle = 'Период ' + nullText(product.advertising.period_from)
            + ' — ' + nullText(product.advertising.period_to) + ' · ТО воронки '
            + nullable(product.advertising.orders_amount, preciseMoney);
        var economics = product.economics_7d || {};
        var turnoverCoverage = economics.turnover_coverage || null;
        var marginCoverage = economics.margin_coverage || null;
        var roiCoverage = economics.roi_coverage || marginCoverage;
        var stock = product.stock || {};
        var stockTitle = 'Заказы воронки за ' + integer.format(finite(stock.period_days, 21)) + ' дн.: '
            + integer.format(finite(stock.orders_21d, 0)) + ' · среднесуточно: '
            + decimal.format(finite(stock.average_daily_orders, 0));
        var current = product.current_economics || {};
        var currentTitle = 'Сегодня, ' + nullText(current.period_to) + ' · заказы '
            + integer.format(finite(current.orders, 0)) + ' · выкуп '
            + (finite(current.buyout_percent, null) === null
                ? '—' : decimal.format(finite(current.buyout_percent, 0)) + '%')
            + (product.advertising.buyout_default_applied ? ' · выкуп по умолчанию' : '')
            + ' · реклама ' + nullable(current.advertising_spend, preciseMoney);
        var cells = {};
        cells.product = '<td><div class="ue1c-product">' + mediaHtml(product, 'ue1c-product-thumb')
            + '<div><button class="ue1c-product-name" type="button" data-product-open="' + escapeHtml(product.id)
            + '" title="Открыть карточку товара">' + escapeHtml(product.name)
            + '</button><div class="ue1c-product-meta">' + copyValue('Арт.', product.article)
            + copyValue('Баркод', product.barcode) + '<span>' + escapeHtml(product.store_name) + '</span>'
            + '<span title="Рейтинг товара">★ ' + escapeHtml(nullText(product.rating)) + '</span>'
            + '<span title="Количество отзывов">' + escapeHtml(nullText(product.reviews_count)) + ' отзывов</span>'
            + '</div></div></div></td>';
        cells.comments = '<td class="ue1c-col-comments"><textarea class="ue1c-comment-input" data-comment-id="'
            + escapeHtml(product.id) + '" maxlength="480" placeholder="Добавить комментарий…"'
            + ' aria-label="Комментарий к товару ' + escapeHtml(product.name) + '">'
            + escapeHtml(commentText(product.id)) + '</textarea></td>';
        cells.newness = '<td class="ue1c-newness"><span class="ue1c-new-badge'
            + (product.is_new ? ' is-new' : '') + '">' + (product.is_new ? 'Новинка' : 'Обычный')
            + '</span><small>' + (product.sales_days === null || product.sales_days === undefined
                ? 'нет данных' : integer.format(product.sales_days) + ' дн.') + '</small></td>';
        var currentSpp = calculateSppPercent(product);
        cells.current = '<td class="ue1c-num ue1c-group-start"><strong title="'
            + escapeHtml(currentTitle) + '">'
            + nullable(current.margin, money) + '</strong></td><td class="ue1c-num'
            + negativeValueClass(current.roi) + '"><strong>'
            + '<span title="' + escapeHtml(currentTitle) + '">'
            + nullable(current.roi, decimal, '%') + '</span></strong></td>'
            + '<td class="ue1c-num"><strong>' + nullable(currentSpp, decimal, '%') + '</strong></td>';
        cells.actual = '<td class="ue1c-num ue1c-group-start' + coverageCellClass(turnoverCoverage)
            + '"><strong>' + coverageValue('ТО после отмен', economics.turnover, money, turnoverCoverage)
            + '</strong></td><td class="ue1c-num' + coverageCellClass(marginCoverage) + '"><strong>'
            + coverageValue('Маржа', economics.margin, money, marginCoverage)
            + '</strong></td><td class="ue1c-num' + coverageCellClass(roiCoverage)
            + negativeValueClass(economics.roi) + '"><strong>'
            + coverageValue('ROI', economics.roi, decimal, roiCoverage, '%') + '</strong></td>';
        cells.advertising = '<td class="ue1c-num ue1c-group-start"><span class="ue1c-drr' + drrClass + '" title="'
            + escapeHtml(advertisingTitle) + '">' + (drr === null ? '—' : decimal.format(drr) + '%')
            + '</span></td><td class="ue1c-num"><strong title="' + escapeHtml(advertisingTitle) + '">'
            + nullable(product.advertising.spend, money) + '</strong></td><td class="ue1c-num">'
            + nullable(product.advertising.ctr, decimal, '%') + '</td><td class="ue1c-num">'
            + nullable(product.advertising.cpc, preciseMoney) + '</td>';
        cells.tag = '<td class="ue1c-col-tag ue1c-group-start ue1c-num"><strong>' + escapeHtml(nullText(tag.goal_week)) + '</strong></td>'
            + '<td class="ue1c-col-tag ue1c-num">' + escapeHtml(nullText(tag.goal_day)) + '</td>'
            + '<td class="ue1c-col-tag"><span class="ue1c-tag-status is-' + escapeHtml(status) + '">'
            + escapeHtml(nullText(tag.status)) + '</span></td><td class="ue1c-col-tag">' + escapeHtml(nullText(tag.ends)) + '</td>'
            + '<td class="ue1c-col-tag"><span class="ue1c-code-pill">' + escapeHtml(nullText(tag.code)) + '</span></td>'
            + '<td class="ue1c-col-tag ue1c-num">' + escapeHtml(nullText(tag.fact)) + '</td>'
            + '<td class="ue1c-col-tag ue1c-num">' + escapeHtml(nullText(tag.plan)) + '</td>';
        cells.stock = '<td class="ue1c-num ue1c-group-start"><strong>' + nullable(product.stock.total, integer) + '</strong></td>'
            + '<td class="ue1c-num"><span class="ue1c-stock-channel is-fbs">' + nullable(product.stock.fbs, integer)
            + '</span></td><td class="ue1c-num"><span class="ue1c-stock-channel is-fbo">'
            + nullable(product.stock.fbo, integer) + '</span></td><td class="ue1c-num"><span class="ue1c-stock-channel">'
            + nullable(product.stock.fulfillment, integer) + '</span></td><td class="ue1c-num"><strong>'
            + '<span title="' + escapeHtml(stockTitle) + '">' + nullable(product.stock.days, integer)
            + '</span></strong></td>';
        return '<tr data-product-id="' + escapeHtml(product.id) + '"'
            + (rowClasses.length ? ' class="' + rowClasses.join(' ') + '"' : '') + '>'
            + visibleColumnGroups().map(function (group) { return cells[group.key] || ''; }).join('') + '</tr>';
    }
    function externallyFilteredProducts() {
        var query = state.query.trim().toLocaleLowerCase('ru-RU');
        return products.filter(function (product) {
            return (state.store === 'all' || product.store_slug === state.store)
                && (state.status === 'all' || (state.status === 'risk'
                    ? product.stock && product.stock.state && product.stock.state.is_risk
                    : state.status === 'low' ? product.stock && product.stock.state && product.stock.state.is_low
                        : state.status === 'new' ? product.is_new === true
                        : productState(product) === state.status))
                && (!query || productSearchValue(product).indexOf(query) !== -1);
        });
    }
    function filteredProducts() {
        var activeColumns = Object.keys(state.tableFilters);
        var result = externallyFilteredProducts().filter(function (product) {
            return activeColumns.every(function (columnIndex) {
                return state.tableFilters[columnIndex].has(columnValue(product, columnIndex));
            });
        });
        result.sort(function (left, right) {
            var leftValue = columnValue(left, state.sortColumn);
            var rightValue = columnValue(right, state.sortColumn);
            var leftNumber = finite(leftValue, null);
            var rightNumber = finite(rightValue, null);
            if (leftNumber !== null && rightNumber !== null) return (leftNumber - rightNumber) * state.sortDirection;
            return String(leftValue || '').localeCompare(String(rightValue || ''), 'ru') * state.sortDirection;
        });
        return result;
    }
    function tableFilterValues(columnIndex) {
        return externallyFilteredProducts().map(function (product) {
            return columnValue(product, columnIndex);
        });
    }
    function applyTableFilters(filters) {
        state.tableFilters = filters || {};
        resetPageAndRender();
    }
    function applyTableSort(columnIndex, direction) {
        state.sortColumn = Number(columnIndex);
        state.sortDirection = direction === 'desc' ? -1 : 1;
        resetPageAndRender();
    }
    function paginationItems(totalPages) {
        if (totalPages <= 7) return Array.from({ length: totalPages }, function (_, index) { return index + 1; });
        var items = [1];
        var start = Math.max(2, state.page - 1);
        var end = Math.min(totalPages - 1, state.page + 1);
        if (state.page <= 4) end = 5;
        if (state.page >= totalPages - 3) start = totalPages - 4;
        if (start > 2) items.push('gap');
        for (var page = start; page <= end; page += 1) items.push(page);
        if (end < totalPages - 1) items.push('gap');
        items.push(totalPages);
        return items;
    }
    function renderPagination(total) {
        var totalPages = Math.max(1, Math.ceil(total / state.pageSize));
        state.page = Math.min(Math.max(1, state.page), totalPages);
        var first = total ? (state.page - 1) * state.pageSize + 1 : 0;
        var last = total ? Math.min(state.page * state.pageSize, total) : 0;
        nodes.summary.textContent = total ? 'Показано ' + integer.format(first) + '–' + integer.format(last)
            + ' из ' + integer.format(total) + ' ' + pluralProducts(total) : 'Показано 0 товаров';
        nodes.pagePrev.disabled = !total || state.page <= 1;
        nodes.pageNext.disabled = !total || state.page >= totalPages;
        nodes.pageNumbers.innerHTML = total ? paginationItems(totalPages).map(function (item) {
            if (typeof item !== 'number') return '<span class="ue1c-page-gap" aria-hidden="true">…</span>';
            return '<button class="ue1c-page-button' + (item === state.page ? ' is-current' : '')
                + '" type="button" data-page="' + item + '"' + (item === state.page ? ' aria-current="page"' : '')
                + '>' + item + '</button>';
        }).join('') : '';
    }
    function renderPage() {
        if (state.productsLoading) {
            nodes.rows.innerHTML = '';
            nodes.empty.hidden = true;
            renderPagination(0);
            return;
        }
        var filtered = filteredProducts();
        state.page = Math.min(Math.max(1, state.page), Math.max(1, Math.ceil(filtered.length / state.pageSize)));
        var offset = (state.page - 1) * state.pageSize;
        nodes.rows.innerHTML = filtered.slice(offset, offset + state.pageSize).map(renderProduct).join('');
        Array.prototype.forEach.call(nodes.rows.querySelectorAll('.ue1c-product-thumb img'), function (image) {
            image.addEventListener('error', function () { image.hidden = true; }, { once: true });
        });
        nodes.empty.hidden = filtered.length > 0 || !nodes.productsError.hidden;
        renderPagination(filtered.length);
        nodes.tableWrap.scrollTop = 0;
    }
    function replaceProducts(items, preserveDetails) {
        var previousProducts = productsById;
        products = (Array.isArray(items) ? items : []).map(function (product) {
            var previous = preserveDetails ? previousProducts[product.id] : null;
            if (!previous || !previous._detailLoaded) return product;
            var mergedPrice = Object.assign({}, previous.price || {}, product.price || {});
            Object.assign(previous, product);
            previous.price = mergedPrice;
            return previous;
        });
        productsById = {};
        products.forEach(function (product) {
            product._detailLoaded = product._detailLoaded
                || Boolean(product.details && Array.isArray(product.history));
            productsById[product.id] = product;
        });
    }
    function setProductsLoading(loading) {
        state.productsLoading = loading;
        nodes.productsLoading.hidden = !loading;
        nodes.search.disabled = loading;
        nodes.store.disabled = loading;
        nodes.periodDays.disabled = loading;
        nodes.periodFrom.disabled = loading;
        nodes.periodTo.disabled = loading;
        nodes.periodApply.disabled = loading;
        nodes.pageSize.disabled = loading;
        Array.prototype.forEach.call(root.querySelectorAll('[data-state-filter]'), function (button) {
            button.disabled = loading;
        });
    }
    function productsRequestUrl(parameters) {
        var separator = productsEndpoint.indexOf('?') === -1 ? '?' : '&';
        var query = new URLSearchParams(parameters || {});
        if (state.periodMode === 'custom') {
            query.set('date_from', state.periodFrom);
            query.set('date_to', state.periodTo);
        } else {
            query.set('period_days', String(state.periodDays));
        }
        return productsEndpoint + separator + query.toString();
    }
    async function loadProducts(options) {
        options = options || {};
        var silent = options.silent === true;
        if (state.productsRefreshing) return false;
        state.productsRefreshing = true;
        var scrollTop = nodes.tableWrap.scrollTop;
        var scrollLeft = nodes.tableWrap.scrollLeft;
        if (!silent) {
            setProductsLoading(true);
            nodes.productsError.hidden = true;
            renderPage();
        }
        try {
            var response = await window.fetch(productsRequestUrl(), {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'Не удалось загрузить данные WB');
            replaceProducts(result.products, silent);
            state.periodDays = Math.max(1, Number(result.period_days) || state.periodDays);
            state.periodFrom = String(result.period_from || state.periodFrom);
            state.periodTo = String(result.period_to || state.periodTo);
            state.lastCompleteDay = String(result.last_complete_day || state.lastCompleteDay);
            state.periodMode = result.period_mode === 'custom' ? 'custom' : 'preset';
            syncPeriodControls();
            renderTableHeader();
            if (window.CheckStockTableFilter && typeof window.CheckStockTableFilter.refresh === 'function') {
                window.CheckStockTableFilter.refresh(nodes.table);
            }
            if (!silent) {
                state.page = 1;
                state.tableFilters = {};
            }
            lastProductsLoadedAt = Date.now();
            nodes.productsError.hidden = true;
            renderPage();
            if (silent) {
                nodes.tableWrap.scrollTop = scrollTop;
                nodes.tableWrap.scrollLeft = scrollLeft;
            }
            if (options.refreshDetail) await refreshSelectedDetail();
            return true;
        } catch (error) {
            if (!silent) {
                replaceProducts([]);
                nodes.productsErrorText.textContent = error.message || 'Повторите попытку чуть позже.';
                nodes.productsError.hidden = false;
            }
            return false;
        } finally {
            state.productsRefreshing = false;
            if (!silent) {
                setProductsLoading(false);
                renderPage();
            }
        }
    }
    function resetPageAndRender() { state.page = 1; renderPage(); }
    function showToast(message, kind) {
        window.clearTimeout(toastTimer);
        nodes.toast.textContent = message;
        nodes.toast.classList.toggle('is-error', kind === 'error');
        nodes.toast.classList.add('is-visible');
        toastTimer = window.setTimeout(function () { nodes.toast.classList.remove('is-visible'); }, 5000);
    }
    async function copyText(button) {
        var value = button.dataset.copyValue || '';
        var copied = false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(value);
                copied = true;
            }
        } catch (error) { copied = false; }
        if (!copied) {
            var helper = document.createElement('textarea');
            helper.value = value;
            helper.style.position = 'fixed';
            helper.style.opacity = '0';
            document.body.appendChild(helper);
            helper.select();
            try { copied = document.execCommand('copy'); } catch (error) { copied = false; }
            helper.remove();
        }
        button.dataset.copyTooltip = copied ? 'Скопировано' : 'Не удалось скопировать';
        window.setTimeout(function () {
            button.dataset.copyTooltip = button.dataset.copyTooltipDefault || 'Нажмите, чтобы скопировать';
        }, 1300);
    }

    function databaseCalculatorValues(product) {
        var details = product.details || {};
        var saved = product.product_settings || {};
        var retail = finite(product.price && product.price.current, null);
        var client = finite(product.price && product.price.with_spp, retail);
        var wallet = finite(product.price && product.price.with_wallet, null);
        var commissionPercent = finite(details.commission_percent, null);
        var acquiringPercent = finite(details.acquiring, null);
        var teamPercent = finite(details.team_commission_percent, null);
        var storageRate = finite(saved.storage_wb_rub, null);
        var storageDays = finite(details.storage_days, null);
        var vatPercent = finite(details.vat_percent, null);
        var taxSystem = productTaxSystem(product);
        var walletPercent = walletDiscountPercent(product);
        if (walletPercent === null && client !== null && client > 0 && wallet !== null) {
            walletPercent = Math.max(0, (client - wallet) / client * 100);
        }
        var activeTaxValue = taxSystem === 'osno'
            ? finite(details.osno_value, null) : finite(details.usn_value, null);
        return {
            retail: retail,
            client: client,
            wallet: wallet,
            spp: retail !== null && retail > 0 && client !== null
                ? (retail - client) / retail * 100 : null,
            walletPercent: walletPercent,
            commission: commissionPercent,
            commissionRub: finite(details.commission_value,
                retail === null || commissionPercent === null ? null : retail * commissionPercent / 100),
            drr: finite(product.advertising && product.advertising.drr, null),
            advertisingRub: finite(product.advertising && product.advertising.spend_per_order, 0),
            logistics: finite(details.delivery_with_returns, finite(details.logistics, null)),
            storage: storageRate,
            storageTotal: finite(details.storage_sum,
                storageRate === null || storageDays === null ? null : storageRate * storageDays),
            acquiringPercent: acquiringPercent,
            acquiringRub: retail === null || acquiringPercent === null
                ? null : retail * acquiringPercent / 100,
            purchase: finite(details.purchase_cost, null),
            team: teamPercent,
            teamRub: retail === null || teamPercent === null ? null : retail * teamPercent / 100,
            fulfillment: finite(details.fulfillment_cost, null),
            vat: vatPercent,
            vatRub: finite(details.vat_value,
                client === null || vatPercent === null ? null : client * vatPercent / (100 + vatPercent)),
            usn: finite(details.usn_percent, null),
            osno: finite(details.osno_percent, null),
            secondaryTaxRub: activeTaxValue
        };
    }
    function sppPriceFactor(product) {
        var withoutSpp = finite(product.price && product.price.current, null);
        var withSpp = finite(product.price && product.price.with_spp, withoutSpp);
        return withoutSpp !== null && withoutSpp > 0 && withSpp !== null
            ? Math.max(0, withSpp) / withoutSpp
            : 1;
    }
    function walletPriceFactor(product) {
        var withSpp = finite(product.price && product.price.with_spp, null);
        var withWallet = finite(product.price && product.price.with_wallet, null);
        return withSpp !== null && withSpp > 0 && withWallet !== null && withWallet > 0
            ? withWallet / withSpp
            : null;
    }
    function walletDiscountPercent(product) {
        var withSpp = finite(product.price && product.price.with_spp, null);
        var withWallet = finite(product.price && product.price.with_wallet, null);
        if (withSpp === null || withSpp <= 0 || withWallet === null || withWallet <= 0) return null;
        for (var percent = 0; percent < 100; percent += 1) {
            if (withSpp - Math.ceil(withSpp * percent / 100) === Math.round(withWallet)) return percent;
        }
        return null;
    }
    function walletPriceFromClient(product, client) {
        var percent = walletDiscountPercent(product);
        var factor = walletPriceFactor(product);
        if (percent !== null) return Math.round(client) - Math.ceil(Math.round(client) * percent / 100);
        return factor === null ? null : roundedRubles(client * factor);
    }
    function clientPriceFromWallet(product, wallet) {
        var percent = walletDiscountPercent(product);
        var factor = walletPriceFactor(product);
        if (percent === null) {
            return factor === null ? null : roundedRubles(wallet / Math.max(factor, 0.0001));
        }
        var estimate = Math.round(wallet / Math.max(1 - percent / 100, 0.0001));
        var matches = [];
        for (var candidate = Math.max(1, estimate - 8); candidate <= estimate + 8; candidate += 1) {
            if (candidate - Math.ceil(candidate * percent / 100) === Math.round(wallet)) matches.push(candidate);
        }
        return matches.length ? matches.reduce(function (best, candidate) {
            return Math.abs(candidate - estimate) < Math.abs(best - estimate) ? candidate : best;
        }, matches[0]) : estimate;
    }
    function roundedRubles(value) { return Math.round(Math.max(0, value)); }
    function precisePrice(value) { return Math.round(Math.max(0, value) * 100) / 100; }
    function syncLinkedPriceInputs(product, source) {
        var retail = finite(nodes.priceInput.value, null);
        var client = finite(nodes.sppPriceInput.value, null);
        var wallet = finite(nodes.walletPriceInput.value, null);
        var sppFactor = Math.max(sppPriceFactor(product), 0.0001);
        var walletFactor = walletPriceFactor(product);
        if (source === 'retail' && retail !== null) {
            client = roundedRubles(retail * sppFactor);
            wallet = walletPriceFromClient(product, client);
        } else if (source === 'client' && client !== null) {
            retail = precisePrice(client / sppFactor);
            wallet = walletPriceFromClient(product, client);
        } else if (source === 'wallet' && wallet !== null && walletFactor !== null) {
            client = clientPriceFromWallet(product, wallet);
            retail = precisePrice(client / sppFactor);
        }
        if (source !== 'retail') nodes.priceInput.value = retail === null ? '' : String(retail);
        if (source !== 'client') nodes.sppPriceInput.value = client === null ? '' : String(client);
        if (source !== 'wallet') nodes.walletPriceInput.value = wallet === null ? '' : String(wallet);
    }
    async function ensureSubjectCommissions() {
        if (subjectCommissions.length) return subjectCommissions;
        if (!commissionsPromise) {
            commissionsPromise = window.fetch(commissionsEndpoint, {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            }).then(function (response) {
                return response.json().then(function (result) {
                    if (!response.ok || !result.ok) {
                        throw new Error(result.error || 'Не удалось загрузить комиссии WB');
                    }
                    subjectCommissions = Array.isArray(result.items) ? result.items : [];
                    return subjectCommissions;
                });
            }).catch(function () {
                commissionsPromise = null;
                return [];
            });
        }
        return commissionsPromise;
    }
    function fillCalculator(product) {
        var values = databaseCalculatorValues(product);
        Array.prototype.forEach.call(nodes.calculatorInputs, function (input) {
            var value = values[input.dataset.calculatorInput];
            input.value = value === null ? '' : String(Math.round(Number(value) * 100) / 100);
        });
        var currentSubject = String(product.details && product.details.subject || '');
        var options = subjectCommissions.slice();
        if (currentSubject && !options.some(function (item) { return item.category === currentSubject; })) {
            options.unshift({ category: currentSubject, commission_percent:
                finite(product.details && product.details.subject_commission_percent, 0) });
        }
        nodes.subjectOptions.innerHTML = options.map(function (item) {
            return '<option value="' + escapeHtml(item.category) + '"></option>';
        }).join('');
        nodes.subjectSelect.value = currentSubject;
    }
    function productTaxSystem(product) {
        return product.store_slug === 'gogol'
            && product.details && product.details.tax_system === 'osno' ? 'osno' : 'usn';
    }
    function syncTaxCalculatorLabel(product) {
        nodes.secondaryTaxLabel.textContent = productTaxSystem(product) === 'osno'
            ? 'Налог ОСНО, руб' : 'Налог УСН, руб';
    }
    function calculatorValues() {
        var values = {};
        Array.prototype.forEach.call(nodes.calculatorInputs, function (input) {
            values[input.dataset.calculatorInput] = finite(input.value, null);
        });
        return values;
    }
    function calculatorInput(key) {
        return root.querySelector('[data-calculator-input="' + key + '"]');
    }
    function setCalculatorValue(key, value) {
        var input = calculatorInput(key);
        if (!input) return;
        input.value = value === null || value === undefined || !Number.isFinite(Number(value))
            ? '' : String(Math.round(Number(value) * 100) / 100);
    }
    function walletPriceWithPercent(clientPrice, percent) {
        if (clientPrice === null || percent === null) return null;
        var roundedClient = Math.round(Math.max(0, clientPrice));
        return roundedClient - Math.ceil(roundedClient * Math.max(0, percent) / 100);
    }
    function clientPriceWithWalletPercent(walletPrice, percent) {
        if (walletPrice === null || percent === null) return null;
        var estimate = Math.round(walletPrice / Math.max(0.0001, 1 - percent / 100));
        var matches = [];
        for (var candidate = Math.max(1, estimate - 10); candidate <= estimate + 10; candidate += 1) {
            if (walletPriceWithPercent(candidate, percent) === Math.round(walletPrice)) matches.push(candidate);
        }
        return matches.length ? matches.reduce(function (best, candidate) {
            return Math.abs(candidate - estimate) < Math.abs(best - estimate) ? candidate : best;
        }, matches[0]) : estimate;
    }
    function syncPriceDependentAmounts(product) {
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        var client = finite(values.client, null);
        var commissionPercent = finite(values.commission, null);
        var acquiringPercent = finite(values.acquiringPercent, null);
        var teamPercent = finite(values.team, null);
        setCalculatorValue('commissionRub', retail === null || commissionPercent === null
            ? null : retail * commissionPercent / 100);
        setCalculatorValue('acquiringRub', retail === null || acquiringPercent === null
            ? null : retail * acquiringPercent / 100);
        setCalculatorValue('teamRub', retail === null || teamPercent === null
            ? null : retail * teamPercent / 100);

        var vatPercent = finite(values.vat, null);
        var vat = client === null || vatPercent === null
            ? null : client * vatPercent / (100 + vatPercent);
        setCalculatorValue('vatRub', vat);
        var taxSystem = productTaxSystem(product);
        var activeTaxPercent = finite(taxSystem === 'osno' ? values.osno : values.usn, null);
        var secondaryTax = client === null || vat === null || activeTaxPercent === null
            ? null : taxSystem === 'osno'
                ? client * activeTaxPercent / 100
                : (client - vat) * activeTaxPercent / 100;
        setCalculatorValue('secondaryTaxRub', secondaryTax);
    }
    function syncDetailedPriceInputs(product, source) {
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        var client = finite(values.client, null);
        var wallet = finite(values.wallet, null);
        var spp = finite(values.spp, null);
        var walletPercent = finite(values.walletPercent, null);
        if ((source === 'retail' || source === 'spp') && retail !== null && spp !== null) {
            client = roundedRubles(retail * Math.max(0, 1 - spp / 100));
            wallet = walletPriceWithPercent(client, walletPercent);
            setCalculatorValue('client', client);
            setCalculatorValue('wallet', wallet);
        } else if (source === 'client' && client !== null && spp !== null) {
            retail = precisePrice(client / Math.max(0.0001, 1 - spp / 100));
            wallet = walletPriceWithPercent(client, walletPercent);
            setCalculatorValue('retail', retail);
            setCalculatorValue('wallet', wallet);
        } else if (source === 'walletPercent' && client !== null && walletPercent !== null) {
            setCalculatorValue('wallet', walletPriceWithPercent(client, walletPercent));
        } else if (source === 'wallet' && wallet !== null && walletPercent !== null && spp !== null) {
            client = clientPriceWithWalletPercent(wallet, walletPercent);
            retail = precisePrice(client / Math.max(0.0001, 1 - spp / 100));
            setCalculatorValue('client', client);
            setCalculatorValue('retail', retail);
        }
        if (source === 'retail' || source === 'spp' || source === 'client' || source === 'wallet') {
            syncPriceDependentAmounts(product);
        }
    }
    function syncDetailedCalculatorInputs(product, source) {
        if (['retail', 'spp', 'client', 'walletPercent', 'wallet'].indexOf(source) !== -1) {
            syncDetailedPriceInputs(product, source);
        }
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        function syncPair(percentKey, rubKey, changedKey, base) {
            var percent = finite(values[percentKey], null);
            var rubles = finite(values[rubKey], null);
            if (changedKey === percentKey) {
                setCalculatorValue(rubKey, base === null || percent === null ? null : base * percent / 100);
            } else if (changedKey === rubKey) {
                setCalculatorValue(percentKey, base === null || base <= 0 || rubles === null
                    ? null : rubles / base * 100);
            }
        }
        syncPair('commission', 'commissionRub', source, retail);
        syncPair('acquiringPercent', 'acquiringRub', source, retail);
        syncPair('team', 'teamRub', source, retail);

        var advertisingRub = finite(values.advertisingRub, null);
        var drr = finite(values.drr, null);
        var periodOrdersAmount = finite(product.advertising && product.advertising.orders_amount, null);
        var periodOrders = finite(product.advertising && product.advertising.orders, null);
        var averageOrderAmount = periodOrdersAmount === null || periodOrders === null || periodOrders <= 0
            ? null : periodOrdersAmount / periodOrders;
        var advertisingBase = averageOrderAmount !== null && averageOrderAmount > 0
            ? averageOrderAmount : retail;
        if (source === 'drr') {
            setCalculatorValue('advertisingRub', advertisingBase === null || drr === null
                ? null : advertisingBase * drr / 100);
        } else if (source === 'advertisingRub') {
            setCalculatorValue('drr', advertisingBase === null || advertisingBase <= 0 || advertisingRub === null
                ? null : advertisingRub / advertisingBase * 100);
        }
    }
    function syncCompactCalculatorInputs(product) {
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        var client = finite(values.client, null);
        setCalculatorValue('spp', retail !== null && retail > 0 && client !== null
            ? (retail - client) / retail * 100 : null);
        syncPriceDependentAmounts(product);
    }
    function calculatePrice(product, simulation) {
        var details = product.details || {};
        var values = simulation || databaseCalculatorValues(product);
        var currentValue = finite(values.retail, null);
        var current = currentValue === null ? null : Math.max(0, currentValue);
        var clientValue = finite(values.client, current);
        var clientPrice = clientValue === null ? null : Math.max(0, clientValue);
        var walletValue = finite(values.wallet, clientPrice);
        var walletPrice = walletValue === null ? null : Math.max(0, walletValue);
        var commissionPercent = finite(values.commission, null);
        var teamCommissionPercent = finite(values.team, null);
        var vatPercent = finite(values.vat, null);
        var usnPercent = finite(values.usn, null);
        var osnoPercent = finite(values.osno, null);
        var taxSystem = productTaxSystem(product);
        var acquiringPercent = finite(values.acquiringPercent, finite(details.acquiring, null));
        var advertisingRub = finite(values.advertisingRub, null);
        var acquiring = finite(values.acquiringRub, current === null || acquiringPercent === null
            ? null : current * acquiringPercent / 100);
        var commission = finite(values.commissionRub, current === null || commissionPercent === null
            ? null : current * commissionPercent / 100);
        var advertising = advertisingRub;
        var teamCommission = finite(values.teamRub, current === null || teamCommissionPercent === null
            ? null : current * teamCommissionPercent / 100);
        var calculatedVat = clientPrice === null || vatPercent === null
            ? null : clientPrice * vatPercent / (100 + vatPercent);
        var vat = finite(values.vatRub, calculatedVat);
        var activeTaxPercent = taxSystem === 'osno' ? osnoPercent : usnPercent;
        var calculatedSecondaryTax = clientPrice === null || vat === null || activeTaxPercent === null
            ? null
            : taxSystem === 'osno'
                ? clientPrice * activeTaxPercent / 100
                : (clientPrice - vat) * activeTaxPercent / 100;
        var secondaryTax = finite(values.secondaryTaxRub, calculatedSecondaryTax);
        var tax = vat === null || secondaryTax === null ? null : vat + secondaryTax;
        var purchase = finite(values.purchase, finite(details.purchase_cost, null));
        var fulfillment = finite(values.fulfillment, null);
        var logistics = finite(values.logistics, null);
        var storageRate = finite(values.storage, null);
        var turnoverDays = finite(details.storage_days, null);
        var storage = finite(values.storageTotal,
            storageRate === null || turnoverDays === null ? null : storageRate * turnoverDays);
        var revenueComplete = [current, acquiring, logistics, storage, commission, advertising].every(function (value) {
            return value !== null;
        });
        var netRevenue = revenueComplete
            ? current - acquiring - logistics - storage - commission - advertising
            : null;
        var profitComplete = [netRevenue, purchase, fulfillment, teamCommission, tax].every(function (value) {
            return value !== null;
        });
        var margin = profitComplete
            ? netRevenue - purchase - fulfillment - teamCommission - tax
            : null;
        return {
            current: current, sppPrice: clientPrice, walletPrice: walletPrice, netRevenue: netRevenue,
            purchase: purchase, fulfillment: fulfillment, acquiring: acquiring,
            commission: commission, teamCommission: teamCommission, logistics: logistics,
            storage: storage, vat: vat, secondaryTax: secondaryTax,
            secondaryTaxLabel: taxSystem === 'osno' ? 'ОСНО' : 'УСН',
            tax: tax, advertising: advertising, margin: margin,
            roi: margin !== null && purchase !== null && purchase > 0 ? margin / purchase * 100 : null
        };
    }
    function metric(label, value, className) {
        return '<div><span>' + escapeHtml(label) + '</span><strong class="' + (className || '') + '">'
            + escapeHtml(value) + '</strong></div>';
    }
    function renderPriceCalculation(product) {
        var values = calculatorValues();
        var metrics = calculatePrice(product, values);
        nodes.secondaryTaxLabel.textContent = 'Налог ' + metrics.secondaryTaxLabel + ', руб';
        var marginClass = metrics.margin === null ? '' : metrics.margin < 0 ? 'is-negative' : 'is-positive';
        nodes.priceMetrics.innerHTML = metric('Чистая прибыль', nullable(metrics.margin, preciseMoney), marginClass)
            + metric('ROI', nullable(metrics.roi, decimal, '%'), metrics.roi === null ? '' : metrics.roi < 0 ? 'is-negative' : 'is-positive');
    }
    function parameter(label, value) {
        return '<div class="ue1c-parameter"><span>' + escapeHtml(label) + '</span><strong title="'
            + escapeHtml(value) + '">' + escapeHtml(value) + '</strong></div>';
    }
    function identifierParameter(label, value) {
        return '<div class="ue1c-parameter"><span>' + escapeHtml(label) + '</span>'
            + copyValue('', value, label) + '</div>';
    }
    function nullable(value, formatter, suffix) {
        if (value === null || value === undefined || value === '') return '—';
        return formatter.format(value) + (suffix || '');
    }
    function editableParameter(label, key, value, suffix, maximum) {
        return '<label class="ue1c-parameter ue1c-parameter--editable"><span>' + escapeHtml(label)
            + '</span><span class="ue1c-parameter-input"><input type="number" min="0" step="0.01"'
            + (maximum ? ' max="' + maximum + '"' : '') + ' data-product-setting="' + escapeHtml(key)
            + '" value="' + escapeHtml(finite(value, 0)) + '"' + (canEdit ? '' : ' disabled') + '>'
            + (suffix ? '<b>' + escapeHtml(suffix) + '</b>' : '') + '</span></label>';
    }
    function renderParameters(product) {
        var item = product.details || {};
        var saved = product.product_settings || {};
        var groups = [
            ['Товар', [
                parameter('Предмет', item.subject === null ? '—' : item.subject),
                identifierParameter('Артикул', product.article),
                identifierParameter('Баркод', product.barcode),
                parameter('Магазин', product.store_name),
                parameter('Закупочная цена', nullable(item.purchase_cost, preciseMoney)),
                parameter('Схема комиссии', item.commission_scheme === null ? '—' : item.commission_scheme)
            ]],
            ['Комиссии и налоги', [
                parameter('Комиссия СУ', nullable(item.subject_commission_percent, decimal, '%')),
                parameter('Доп. тарифы WB', nullable(item.wb_extra_tariff_percent, decimal, '%')),
                parameter('Процент WB', nullable(item.commission_percent, decimal, '%')),
                parameter('Налоговая система', item.tax_system === 'osno' ? 'ОСНО' : 'УСН'),
                parameter('НДС', nullable(item.vat_percent, decimal, '%')),
                parameter(item.tax_system === 'osno' ? 'ОСНО' : 'УСН', nullable(
                    item.tax_system === 'osno' ? item.osno_percent : item.usn_percent, decimal, '%')),
                parameter('Эквайринг', nullable(item.acquiring, decimal, '%')),
                parameter('Комиссия команды', nullable(item.team_commission_percent, decimal, '%'))
            ]],
            ['Продажи и реклама', [
                parameter(product.advertising.buyout_default_applied ? 'Выкуп · значение кабинета' : 'Выкуп WB · период кабинета', nullable(product.advertising.buyout_percent, decimal, '%')),
                parameter('СПП', nullable(calculateSppPercent(product), decimal, '%')),
                parameter('ДРР с выкупом', nullable(product.advertising.drr, decimal, '%')),
                parameter('Реклама факт', nullable(item.actual_advertising, preciseMoney))
            ]],
            ['Логистика', [
                parameter('Фулфилмент', nullable(item.fulfillment_cost, preciseMoney)),
                editableParameter('Доставка WB', 'delivery_wb_rub', saved.delivery_wb_rub, '₽'),
                editableParameter('За 1 возврат', 'return_cost_rub', saved.return_cost_rub, '₽'),
                editableParameter('Объём 1 товара', 'volume_l', saved.volume_l, 'л'),
                parameter('Стоимость платной приёмки', nullable(item.paid_acceptance_cost, preciseMoney)),
                parameter('Доставка с учётом возвратов', nullable(item.delivery_with_returns, preciseMoney))
            ]],
            ['Хранение', [
                editableParameter('Хранение WB', 'storage_wb_rub', saved.storage_wb_rub, '₽/шт.'),
                parameter('Дней', nullable(item.storage_days, integer)),
                parameter('Сумма', nullable(item.storage_sum, preciseMoney))
            ]]
        ];
        nodes.parameters.innerHTML = groups.map(function (group) {
            return '<section class="ue1c-parameter-group"><h4>' + escapeHtml(group[0])
                + '</h4><div class="ue1c-parameter-grid">' + group[1].join('') + '</div></section>';
        }).join('') + '<div class="ue1c-parameter-save"><span data-product-settings-state>'
            + (saved.updated_at ? 'Параметры сохранены' : 'Значения по умолчанию')
            + '</span><button type="button" data-save-product-settings' + (canEdit ? '' : ' disabled')
            + '>Сохранить параметры</button></div>';
    }
    function renderGluedProducts(product) {
        var items = Array.isArray(product.glued_products) ? product.glued_products : [];
        nodes.gluedSection.hidden = !items.length;
        nodes.gluedProducts.innerHTML = items.map(function (item) {
            var targetId = product.store_slug + ':' + item.article;
            return '<div><button class="ue1c-glued-name" type="button" data-glued-product-open="'
                + escapeHtml(targetId) + '" title="Открыть карточку товара">' + escapeHtml(item.name)
                + '</button><button class="ue1c-copy-value ue1c-glued-article" type="button" data-copy-value="'
                + escapeHtml(item.article) + '" data-copy-kind="Артикул" data-copy-tooltip="нажмите чтобы скопировать"'
                + ' data-copy-tooltip-default="нажмите чтобы скопировать" aria-label="Скопировать артикул '
                + escapeHtml(item.article) + '">Арт. <strong>' + escapeHtml(item.article) + '</strong></button></div>';
        }).join('');
    }
    function chartPath(points) {
        return points.map(function (point, index) {
            return (index ? 'L' : 'M') + point[0].toFixed(1) + ' ' + point[1].toFixed(1);
        }).join(' ');
    }
    function renderChartDailySales(history) {
        if (!nodes.chartDailySales) return;
        if (!history.length) {
            nodes.chartDailySales.innerHTML = '';
            nodes.chartDailySales.hidden = true;
            return;
        }
        nodes.chartDailySales.style.setProperty('--ue1c-chart-days', history.length);
        nodes.chartDailySales.innerHTML = '<span class="ue1c-chart-daily-sales-label">Продажи<small>шт.</small></span>'
            + history.map(function (item) {
                var orders = Math.max(0, Math.round(finite(item.orders_count, 0)));
                return '<span class="ue1c-chart-daily-sales-value" title="' + escapeHtml(item.label)
                    + ': ' + escapeHtml(integer.format(orders)) + ' шт.">'
                    + escapeHtml(integer.format(orders)) + '</span>';
            }).join('');
        nodes.chartDailySales.setAttribute(
            'aria-label',
            'Продажи по дням в штуках: ' + history.map(function (item) {
                return item.label + ' — ' + Math.max(0, Math.round(finite(item.orders_count, 0)));
            }).join(', ')
        );
        nodes.chartDailySales.hidden = false;
    }
    function renderChart(product) {
        var allHistory = Array.isArray(product.history) ? product.history : [];
        if (!allHistory.length) {
            nodes.chart.innerHTML = '';
            renderChartDailySales([]);
            return;
        }
        var history = allHistory.slice(-14);
        renderChartDailySales(history);
        var previousHistory = allHistory.length >= 21 ? allHistory.slice(-21, -7) : [];
        var compare = chartPreferences.compare && previousHistory.length === history.length;
        var compareInput = nodes.chartWrap.querySelector('[data-chart-compare]');
        if (compareInput) compareInput.disabled = previousHistory.length !== history.length;
        var enabled = function (key) { return chartPreferences.series.indexOf(key) !== -1; };
        var left = 42;
        var right = 398;
        var top = 16;
        var bottom = 174;
        var width = right - left;
        var height = bottom - top;
        var orderScale = 10;
        var visibleHistory = history;
        var comparedOrderHistory = compare ? history.concat(previousHistory) : history;
        var moneyValues = [];
        visibleHistory.forEach(function (item) {
            var marginValue = enabled('margin') ? finite(item.margin_rub, null) : null;
            var advertisingValue = enabled('ads') ? finite(item.advertising_rub, null) : null;
            if (marginValue !== null) moneyValues.push(marginValue);
            if (advertisingValue !== null) moneyValues.push(advertisingValue);
        });
        var minimum = Math.min.apply(null, [0].concat(moneyValues));
        var maximum = Math.max.apply(null, [1].concat(moneyValues));
        var usesMoneyAxis = enabled('margin') || enabled('ads');
        if (usesMoneyAxis && enabled('orders') && minimum < 0) {
            var moneyBoundary = Math.max(Math.abs(minimum), Math.abs(maximum), 1);
            minimum = -moneyBoundary;
            maximum = moneyBoundary;
        }
        var range = maximum - minimum || 1;
        var stockValues = [];
        var scaledOrderValues = enabled('orders') ? comparedOrderHistory.map(function (item) {
            return finite(item.orders_count, 0) * orderScale;
        }) : [];
        visibleHistory.forEach(function (item) {
            if (!enabled('stock')) return;
            var stockValue = finite(item.stock_units, null);
            if (stockValue !== null) stockValues.push(stockValue);
        });
        var stockMaximum = Math.max.apply(null, [1].concat(stockValues));
        var orderMaximum = Math.max.apply(null, [1].concat(scaledOrderValues));
        var drrValues = visibleHistory.map(function (item) { return finite(item.drr_percent, null); })
            .filter(function (value) { return value !== null; });
        var drrObservedMaximum = Math.max.apply(null, [0].concat(drrValues));
        var drrMaximum = drrObservedMaximum <= 10 ? 10
            : drrObservedMaximum <= 25 ? 25
                : drrObservedMaximum <= 50 ? 50
                    : drrObservedMaximum <= 100 ? 100 : Math.ceil(drrObservedMaximum / 50) * 50;
        function x(index) { return left + width * (index + 0.5) / history.length; }
        function moneyY(value) { return bottom - (value - minimum) / range * height; }
        function stockY(value) { return bottom - value / stockMaximum * height; }
        var orderBaselineY = usesMoneyAxis ? moneyY(0) : bottom;
        function orderY(value) {
            return orderBaselineY - value / orderMaximum * Math.max(orderBaselineY - top, 1);
        }
        function drrY(value) { return bottom - value / drrMaximum * height; }
        function complete(items, key) {
            return items.length && items.every(function (item) { return finite(item[key], null) !== null; });
        }
        function points(items, key, yFunction) {
            return items.map(function (item, index) { return [x(index), yFunction(finite(item[key], 0))]; });
        }
        var grid = [0, 1, 2, 3].map(function (index) {
            var y = top + height * index / 3;
            var value = usesMoneyAxis ? maximum - range * index / 3
                : stockValues.length ? stockMaximum * (1 - index / 3)
                    : orderMaximum * (1 - index / 3) / orderScale;
            return '<line class="ue1c-chart-grid" x1="' + left + '" y1="' + y + '" x2="' + right
                + '" y2="' + y + '"></line><text class="ue1c-chart-y" x="2" y="' + (y + 3)
                + '">' + escapeHtml(integer.format(Math.round(value))) + '</text>';
        }).join('');
        var drrAxis = enabled('drr') ? [0, 0.5, 1].map(function (ratio) {
            var y = bottom - height * ratio;
            return '<text class="ue1c-chart-y ue1c-chart-y--drr" x="438" y="' + (y + 3)
                + '" text-anchor="end">' + escapeHtml(decimal.format(drrMaximum * ratio)) + '%</text>';
        }).join('') : '';
        var chartSlotWidth = width / history.length;
        var barGap = Math.max(2, Math.min(4, chartSlotWidth * 0.12));
        var barWidth = compare
            ? Math.max(5, (chartSlotWidth - barGap * 2) / 2)
            : Math.max(7, Math.min(22, chartSlotWidth * 0.64));
        function orderBars(items, previous) {
            if (!enabled('orders') || !items.length) return '';
            return items.map(function (item, index) {
                var actualOrders = Math.max(0, Math.round(finite(item.orders_count, 0)));
                var scaledOrders = actualOrders * orderScale;
                var y = orderY(Math.min(scaledOrders, orderMaximum));
                var offset = compare ? (previous ? barGap / 2 : -(barWidth + barGap / 2)) : -barWidth / 2;
                var barHeight = orderBaselineY - y;
                return '<rect class="ue1c-chart-bar' + (previous ? ' is-previous' : '') + '" x="'
                    + (x(index) + offset) + '" y="' + y + '" width="' + barWidth
                    + '" height="' + barHeight + '" rx="2"></rect>';
            }).join('');
        }
        function line(items, key, yFunction, className, seriesKey) {
            var availablePoints = [];
            items.forEach(function (item, index) {
                var itemValue = finite(item[key], null);
                if (itemValue !== null) availablePoints.push([x(index), yFunction(itemValue)]);
            });
            if (!availablePoints.length) return { visual: '', hit: '' };
            if (availablePoints.length === 1) {
                return {
                    visual: '<circle class="ue1c-chart-point ' + className + '" cx="'
                        + availablePoints[0][0] + '" cy="' + availablePoints[0][1] + '" r="3"></circle>',
                    hit: '<circle class="ue1c-chart-line-hit" data-chart-line-series="' + seriesKey
                        + '" cx="' + availablePoints[0][0] + '" cy="' + availablePoints[0][1]
                        + '" r="8"></circle>'
                };
            }
            var path = chartPath(availablePoints);
            return {
                visual: '<path class="ue1c-chart-line ' + className + '" d="' + path + '"></path>',
                hit: '<path class="ue1c-chart-line-hit" data-chart-line-series="' + seriesKey
                    + '" d="' + path + '"></path>'
            };
        }
        var renderedLines = {
            stock: enabled('stock') ? line(history, 'stock_units', stockY, 'is-stock', 'stock') : { visual: '', hit: '' },
            margin: enabled('margin') ? line(history, 'margin_rub', moneyY, 'is-margin', 'margin') : { visual: '', hit: '' },
            ads: enabled('ads') ? line(history, 'advertising_rub', moneyY, 'is-ads', 'ads') : { visual: '', hit: '' },
            drr: enabled('drr') ? line(history, 'drr_percent', drrY, 'is-drr', 'drr') : { visual: '', hit: '' }
        };
        var labels = history.map(function (item, index) {
            return '<text class="ue1c-chart-x" x="' + x(index) + '" y="204" text-anchor="middle">'
                + escapeHtml(item.label) + '</text>';
        }).join('');
        var hits = history.map(function (item, index) {
            var hitLeft = index === 0 ? left : (x(index - 1) + x(index)) / 2;
            var hitRight = index === history.length - 1 ? right : (x(index) + x(index + 1)) / 2;
            return '<rect class="ue1c-chart-hit" data-chart-index="' + index + '" x="' + hitLeft
                + '" y="' + top + '" width="' + (hitRight - hitLeft) + '" height="190" tabindex="0"></rect>';
        }).join('');
        var zeroAxis = usesMoneyAxis ? '<line class="ue1c-chart-zero" x1="'
            + left + '" y1="' + moneyY(0) + '" x2="' + right + '" y2="' + moneyY(0)
            + '"></line><text class="ue1c-chart-y ue1c-chart-zero-label" x="2" y="'
            + (moneyY(0) + 3) + '">0</text>' : '';
        nodes.chart.innerHTML = grid + drrAxis + zeroAxis
            + orderBars(compare ? previousHistory : [], true) + orderBars(history, false)
            + renderedLines.stock.visual + renderedLines.margin.visual
            + renderedLines.ads.visual + renderedLines.drr.visual
            + '<line class="ue1c-chart-cursor" data-chart-cursor x1="' + left + '" y1="' + top
            + '" x2="' + left + '" y2="' + bottom + '"></line>'
            + '<circle class="ue1c-chart-hover-point" data-chart-series-marker r="4"></circle>'
            + labels + hits + renderedLines.stock.hit + renderedLines.margin.hit
            + renderedLines.ads.hit + renderedLines.drr.hit;
        Array.prototype.forEach.call(nodes.chart.querySelectorAll('[data-chart-index]'), function (hit) {
            function show() {
                showChartTooltip(Number(hit.dataset.chartIndex), x, history, compare ? previousHistory : []);
            }
            hit.addEventListener('mouseenter', show);
            hit.addEventListener('focus', show);
            hit.addEventListener('blur', hideChartTooltip);
        });
        var seriesDefinitions = {
            stock: { key: 'stock_units', label: 'Остатки', formatter: integer, suffix: ' шт.', y: stockY },
            margin: { key: 'margin_rub', label: 'Прибыль', formatter: preciseMoney, suffix: '', y: moneyY },
            ads: { key: 'advertising_rub', label: 'Реклама', formatter: preciseMoney, suffix: '', y: moneyY },
            drr: { key: 'drr_percent', label: 'ДРР с выкупом', formatter: decimal, suffix: '%', y: drrY }
        };
        Array.prototype.forEach.call(nodes.chart.querySelectorAll('[data-chart-line-series]'), function (lineHit) {
            var seriesKey = lineHit.dataset.chartLineSeries;
            var definition = seriesDefinitions[seriesKey];
            if (!definition) return;
            lineHit.addEventListener('mousemove', function (event) {
                var bounds = nodes.chart.getBoundingClientRect();
                var pointerX = (event.clientX - bounds.left) * 440 / Math.max(bounds.width, 1);
                var index = Math.max(0, Math.min(
                    history.length - 1,
                    Math.round((pointerX - left) * history.length / width - 0.5)
                ));
                if (finite(history[index][definition.key], null) === null) {
                    var availableIndexes = history.map(function (item, itemIndex) {
                        return finite(item[definition.key], null) === null ? null : itemIndex;
                    }).filter(function (itemIndex) { return itemIndex !== null; });
                    if (!availableIndexes.length) return;
                    index = availableIndexes.reduce(function (nearest, candidate) {
                        return Math.abs(candidate - index) < Math.abs(nearest - index) ? candidate : nearest;
                    }, availableIndexes[0]);
                }
                showChartTooltip(index, x, history, compare ? previousHistory : []);
                showChartSeriesTooltip(
                    seriesKey,
                    definition,
                    history[index],
                    x(index),
                    definition.y(finite(history[index][definition.key], 0))
                );
            });
            lineHit.addEventListener('mouseleave', hideChartSeriesTooltip);
        });
    }
    function showChartSeriesTooltip(seriesKey, definition, item, pointX, pointY) {
        if (!nodes.chartSeriesTooltip || !item) return;
        var value = finite(item[definition.key], null);
        if (value === null) return;
        var chartBounds = nodes.chart.getBoundingClientRect();
        var wrapBounds = nodes.chartWrap.getBoundingClientRect();
        var leftPosition = chartBounds.left - wrapBounds.left + pointX / 440 * chartBounds.width;
        var topPosition = chartBounds.top - wrapBounds.top + pointY / 220 * chartBounds.height;
        nodes.chartSeriesTooltip.innerHTML = '<strong>' + escapeHtml(item.label) + '</strong><span>'
            + escapeHtml(definition.label) + '</span><b>'
            + escapeHtml(definition.formatter.format(value) + definition.suffix) + '</b>';
        nodes.chartSeriesTooltip.style.left = Math.max(58, Math.min(wrapBounds.width - 58, leftPosition)) + 'px';
        nodes.chartSeriesTooltip.style.top = topPosition + 'px';
        nodes.chartSeriesTooltip.className = 'ue1c-chart-series-tooltip is-' + seriesKey;
        nodes.chartSeriesTooltip.classList.toggle('is-below-point', pointY < 44);
        nodes.chartSeriesTooltip.hidden = false;
        var marker = nodes.chart.querySelector('[data-chart-series-marker]');
        if (marker) {
            marker.setAttribute('cx', pointX);
            marker.setAttribute('cy', pointY);
            marker.setAttribute('class', 'ue1c-chart-hover-point is-visible is-' + seriesKey);
        }
    }
    function hideChartSeriesTooltip() {
        if (nodes.chartSeriesTooltip) nodes.chartSeriesTooltip.hidden = true;
        var marker = nodes.chart.querySelector('[data-chart-series-marker]');
        if (marker) marker.setAttribute('class', 'ue1c-chart-hover-point');
    }
    function showChartTooltip(index, xFunction, history, previousHistory) {
        var item = history[index];
        if (!item) return;
        var previous = previousHistory[index];
        var x = xFunction(index);
        var cursor = nodes.chart.querySelector('[data-chart-cursor]');
        if (cursor) {
            cursor.setAttribute('x1', x);
            cursor.setAttribute('x2', x);
            cursor.classList.add('is-visible');
        }
        nodes.chartTooltip.innerHTML = '<strong>' + escapeHtml(item.label) + '</strong><table><tbody>'
            + '<tr><th scope="row">Заказы воронки</th><td>' + nullable(item.orders_count, decimal, ' шт.') + '</td></tr>'
            + '<tr><th scope="row">Реклама</th><td>' + nullable(item.advertising_rub, preciseMoney) + '</td></tr>'
            + '<tr><th scope="row">ДРР с выкупом</th><td>' + nullable(item.drr_percent, decimal, '%') + '</td></tr>'
            + '<tr><th scope="row">Чистая прибыль</th><td>' + nullable(item.margin_rub, preciseMoney) + '</td></tr>'
            + '<tr><th scope="row">Остатки</th><td>' + nullable(item.stock_units, integer, ' шт.') + '</td></tr>'
            + (previous ? '<tr class="ue1c-chart-tooltip-previous"><th scope="row">Неделей ранее</th><td>'
                + nullable(previous.orders_count, decimal, ' шт.') + '</td></tr>' : '')
            + '</tbody></table>';
        nodes.chartTooltip.classList.add('is-below');
        nodes.chartTooltip.hidden = false;
        nodes.chartTooltip.style.left = '';
        nodes.chartTooltip.style.top = '';
        nodes.chartTooltip.style.transform = 'none';
    }
    function hideChartTooltip() {
        nodes.chartTooltip.hidden = true;
        hideChartSeriesTooltip();
        var cursor = nodes.chart.querySelector('[data-chart-cursor]');
        if (cursor) cursor.classList.remove('is-visible');
    }

    function setDetailTab(tabName) {
        Array.prototype.forEach.call(root.querySelectorAll('[data-detail-tab]'), function (button) {
            var active = button.dataset.detailTab === tabName;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
        });
        id('ue1c-panel-economics').hidden = tabName !== 'economics';
        id('ue1c-panel-params').hidden = tabName !== 'params';
    }
    function updateSaveState() {
        nodes.savePrice.setAttribute('aria-busy', sendingPrice ? 'true' : 'false');
    }
    function renderDrawerMedia(product) {
        var initial = (product.name || product.article || '?').slice(0, 1).toUpperCase();
        nodes.drawerThumb.innerHTML = '<span>' + escapeHtml(initial) + '</span>'
            + (product.image_url && /^https?:\/\//.test(product.image_url)
                ? '<img src="' + escapeHtml(product.image_url) + '" alt="">' : '');
        var image = nodes.drawerThumb.querySelector('img');
        if (image) image.addEventListener('error', function () { image.hidden = true; }, { once: true });
    }
    function renderDetailProduct(product) {
        root.classList.remove('is-detail-loading');
        nodes.detailLoading.hidden = true;
        state.selected = product.id;
        renderDrawerMedia(product);
        nodes.drawerTitle.textContent = product.name;
        nodes.drawerMeta.innerHTML = escapeHtml(product.store_name) + ' · '
            + copyValue('Арт.', product.article, 'Артикул') + ' · ★ ' + escapeHtml(nullText(product.rating))
            + ' · ' + escapeHtml(nullText(product.reviews_count)) + ' отзывов';
        syncTaxCalculatorLabel(product);
        fillCalculator(product);
        updateSaveState(product);
        renderPriceCalculation(product);
        renderParameters(product);
        renderGluedProducts(product);
        renderChart(product);
        setDetailTab('economics');
        Array.prototype.forEach.call(nodes.rows.querySelectorAll('tr'), function (row) {
            row.classList.toggle('is-selected', row.dataset.productId === product.id);
        });
    }
    async function fetchProductDetail(product) {
        var query = new URLSearchParams({ store: product.store_slug, article: product.article });
        var response = await window.fetch(productsRequestUrl(query), {
            headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
        });
        var result = await response.json();
        if (!response.ok || !result.ok || !result.product) {
            throw new Error(result.error || 'Не удалось загрузить карточку товара');
        }
        Object.assign(product, result.product);
        product._detailLoaded = true;
        if (product._pendingPricePlan) {
            applyPricePlan(product, product._pendingPricePlan);
            delete product._pendingPricePlan;
        }
        productsById[product.id] = product;
        return product;
    }
    async function refreshSelectedDetail() {
        var product = productsById[state.selected];
        if (!product || !nodes.detail.classList.contains('is-open')) return;
        var active = document.activeElement;
        if (active && nodes.detail.contains(active)
            && /^(INPUT|SELECT|TEXTAREA)$/.test(active.tagName)) return;
        try {
            await fetchProductDetail(product);
            if (state.selected === product.id) renderDetailProduct(product);
        } catch (error) {
            /* Keep the last valid detail while a background refresh is unavailable. */
        }
    }
    async function openDetail(product) {
        state.selected = product.id;
        editedPriceKind = 'retail';
        root.classList.add('has-detail');
        nodes.detail.classList.add('is-open');
        nodes.detail.setAttribute('aria-hidden', 'false');
        nodes.overlay.classList.add('is-open');
        renderDrawerMedia(product);
        nodes.drawerTitle.textContent = product.name;
        nodes.drawerMeta.innerHTML = escapeHtml(product.store_name) + ' · '
            + copyValue('Арт.', product.article, 'Артикул');
        root.classList.add('is-detail-loading');
        nodes.detailLoading.hidden = false;
        var commissionsLoad = ensureSubjectCommissions();
        try {
            if (!product._detailLoaded) {
                await fetchProductDetail(product);
            }
            await commissionsLoad;
            if (state.selected === product.id) renderDetailProduct(product);
        } catch (error) {
            if (state.selected !== product.id) return;
            root.classList.remove('is-detail-loading');
            nodes.detailLoading.hidden = true;
            closeDetail();
            showToast(error.message || 'Не удалось загрузить карточку товара', 'error');
        }
    }
    function clearFiltersForProduct() {
        state.query = '';
        nodes.search.value = '';
        state.store = 'all';
        nodes.store.value = 'all';
        state.status = 'all';
        state.tableFilters = {};
        nodes.table._tfFilters = {};
        Array.prototype.forEach.call(root.querySelectorAll('[data-state-filter]'), function (button) {
            button.classList.toggle('is-active', button.dataset.stateFilter === 'all');
        });
        Array.prototype.forEach.call(nodes.table.querySelectorAll('.tf-btn--active'), function (button) {
            button.classList.remove('tf-btn--active');
        });
    }
    function locateProductInTable(product) {
        var filtered = filteredProducts();
        var productIndex = filtered.findIndex(function (item) { return item.id === product.id; });
        if (productIndex === -1) {
            clearFiltersForProduct();
            filtered = filteredProducts();
            productIndex = filtered.findIndex(function (item) { return item.id === product.id; });
        }
        if (productIndex === -1) {
            showToast('Не удалось найти связанный товар в таблице', 'error');
            return;
        }
        state.page = Math.floor(productIndex / state.pageSize) + 1;
        renderPage();
        openDetail(product);
        var targetRow = Array.prototype.find.call(nodes.rows.querySelectorAll('tr'), function (row) {
            return row.dataset.productId === product.id;
        });
        if (!targetRow) return;
        window.clearTimeout(rowHighlightTimer);
        targetRow.classList.add('is-located');
        window.requestAnimationFrame(function () {
            targetRow.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
        });
        rowHighlightTimer = window.setTimeout(function () {
            targetRow.classList.remove('is-located');
        }, 5000);
    }
    function closeDetail() {
        state.selected = null;
        root.classList.remove('has-detail', 'is-detail-loading');
        nodes.detailLoading.hidden = true;
        nodes.detail.classList.remove('is-open');
        nodes.detail.setAttribute('aria-hidden', 'true');
        nodes.overlay.classList.remove('is-open');
        hideChartTooltip();
        Array.prototype.forEach.call(nodes.rows.querySelectorAll('tr.is-selected'), function (row) {
            row.classList.remove('is-selected');
        });
    }
    function breakEvenPrice(product, values) {
        var item = product.details || {};
        var commissionPercent = finite(values.commission, null);
        var teamCommissionPercent = finite(values.team, null);
        var vatPercent = finite(values.vat, null);
        var usnPercent = finite(values.usn, null);
        var osnoPercent = finite(values.osno, null);
        var taxSystem = productTaxSystem(product);
        var activeTaxPercent = taxSystem === 'osno' ? osnoPercent : usnPercent;
        var acquiringPercent = finite(values.acquiringPercent, finite(item.acquiring, null));
        var advertisingRub = finite(values.advertisingRub, null);
        var purchase = finite(values.purchase, finite(item.purchase_cost, null));
        var fulfillment = finite(values.fulfillment, null);
        var logistics = finite(values.logistics, null);
        var storageRate = finite(values.storage, null);
        var turnoverDays = finite(item.storage_days, null);
        var storage = finite(values.storageTotal,
            storageRate === null || turnoverDays === null ? null : storageRate * turnoverDays);
        if ([commissionPercent, teamCommissionPercent, vatPercent, activeTaxPercent,
            acquiringPercent, advertisingRub,
            purchase, fulfillment, logistics, storage].some(function (value) {
            return value === null;
        })) return null;
        var retailValue = finite(values.retail, null);
        var clientValue = finite(values.client, retailValue);
        var walletValue = finite(values.wallet, clientValue);
        var clientPriceFactor = retailValue !== null && retailValue > 0 && clientValue !== null
            ? Math.max(clientValue, 0) / retailValue : sppPriceFactor(product);
        var walletPriceFactor = retailValue !== null && retailValue > 0 && walletValue !== null
            ? Math.max(walletValue, 0) / retailValue : clientPriceFactor;
        var vatFactor = vatPercent / (100 + vatPercent);
        var secondaryTaxFactor = taxSystem === 'osno'
            ? osnoPercent / 100
            : (1 - vatFactor) * usnPercent / 100;
        var variableFactor = 1 - commissionPercent / 100
            - acquiringPercent / 100
            - teamCommissionPercent / 100
            - clientPriceFactor * (vatFactor + secondaryTaxFactor);
        var fixed = purchase + fulfillment + logistics + storage + advertisingRub;
        return {
            retail: Math.ceil(fixed / Math.max(0.01, variableFactor) / 10) * 10,
            clientPriceFactor: clientPriceFactor,
            walletPriceFactor: walletPriceFactor
        };
    }
    function priceKindLabel(kind) {
        return { retail: 'Цена без СПП', spp: 'Цена с СПП', wallet: 'Цена с WB Кошельком' }[kind] || 'Цена';
    }
    function calculatorTargetKind() {
        return editedPriceKind === 'client' ? 'spp' : editedPriceKind;
    }
    function calculatorTargetValue() {
        var input = editedPriceKind === 'client' ? nodes.sppPriceInput
            : editedPriceKind === 'wallet' ? nodes.walletPriceInput : nodes.priceInput;
        var value = finite(input.value, null);
        if (value === null) return null;
        return editedPriceKind === 'retail' ? precisePrice(value) : Math.round(value);
    }
    function confirmationRow(label, previousValue, nextValue, suffix) {
        function formatted(value) {
            var parsed = finite(value, null);
            return parsed === null ? '—' : decimal.format(parsed) + (suffix || '');
        }
        return '<span>' + escapeHtml(label) + '</span><span class="is-old">'
            + escapeHtml(formatted(previousValue)) + '</span><span class="is-new">→ '
            + escapeHtml(formatted(nextValue)) + '</span>';
    }
    function openPriceConfirmation(product, payload, plan) {
        pendingPriceChange = { productId: product.id, payload: payload, plan: plan };
        nodes.confirmProduct.innerHTML = escapeHtml(product.store_name) + ' · '
            + copyValue('Арт.', product.article, 'Артикул') + ' · ' + escapeHtml(product.name);
        nodes.confirmTarget.textContent = priceKindLabel(plan.target_kind) + ': '
            + decimal.format(plan.target_price) + ' ₽';
        nodes.confirmGrid.innerHTML = confirmationRow(
            'Базовая цена WB', plan.previous_base_price, plan.base_price, ' ₽'
        ) + confirmationRow(
            'Скидка продавца', plan.previous_discount, plan.discount, '%'
        ) + confirmationRow(
            'Цена без СПП', plan.previous_retail_price, plan.display_retail_price, ' ₽'
        ) + confirmationRow(
            'Цена с СПП', plan.previous_spp_price, plan.predicted_spp_price, ' ₽'
        ) + confirmationRow(
            'С WB Кошельком', plan.previous_wallet_price, plan.predicted_wallet_price, ' ₽'
        );
        nodes.confirmWarning.hidden = !plan.quarantine_risk;
        nodes.confirmWarning.textContent = plan.quarantine_risk
            ? 'Новая цена более чем в 3 раза ниже текущей. WB может поместить её в карантин.' : '';
        nodes.confirmModal.classList.add('is-open');
        nodes.confirmModal.setAttribute('aria-hidden', 'false');
        nodes.confirmSend.focus();
    }
    function closePriceConfirmation() {
        if (sendingPrice) return;
        pendingPriceChange = null;
        nodes.confirmModal.classList.remove('is-open');
        nodes.confirmModal.setAttribute('aria-hidden', 'true');
    }
    async function saveSelectedPrice() {
        var product = productsById[state.selected];
        if (!product || !canEdit) {
            if (!canEdit) showToast('Нет права изменять цены');
            return;
        }
        var targetValue = calculatorTargetValue();
        if (targetValue === null || targetValue <= 0) {
            showToast('Укажите целевую цену больше нуля');
            return;
        }
        var payload = {
            data: [{
                store_slug: product.store_slug,
                article: product.article,
                target_kind: calculatorTargetKind(),
                target_price: targetValue
            }]
        };
        nodes.savePrice.disabled = true;
        nodes.savePrice.textContent = 'Проверяем…';
        try {
            var response = await window.fetch('/api/unit-economics-1c/prices/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify(payload)
            });
            var result = await response.json();
            var plan = Array.isArray(result.accepted) ? result.accepted[0] : null;
            if (!response.ok || !plan) {
                var previewError = Array.isArray(result.errors) && result.errors.length
                    ? result.errors[0].error : result.error;
                throw new Error(previewError || 'Не удалось рассчитать параметры WB');
            }
            openPriceConfirmation(product, payload, plan);
        } catch (error) {
            showToast(error.message || 'Не удалось рассчитать параметры WB');
        } finally {
            nodes.savePrice.disabled = false;
            nodes.savePrice.textContent = 'Сохранить цену';
        }
    }
    async function sendConfirmedPrice() {
        if (!pendingPriceChange || sendingPrice) return;
        var pending = pendingPriceChange;
        var product = productsById[pending.productId];
        sendingPrice = true;
        nodes.confirmSend.disabled = true;
        nodes.confirmCancel.disabled = true;
        nodes.confirmClose.disabled = true;
        nodes.confirmSend.textContent = 'Ставим в очередь…';
        if (product) updateSaveState(product);
        try {
            var response = await window.fetch('/api/unit-economics-1c/prices', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify(pending.payload)
            });
            var result = await response.json();
            if (!response.ok || !result.job_id) throw new Error(result.error || 'Не удалось запустить отправку');
            pendingPriceChange = null;
            nodes.confirmModal.classList.remove('is-open');
            nodes.confirmModal.setAttribute('aria-hidden', 'true');
            if (pendingPriceJobs.indexOf(result.job_id) === -1) pendingPriceJobs.push(result.job_id);
            writeJson(priceJobsKey, pendingPriceJobs);
            showToast('Цена отправляется в фоне — можно продолжать работу');
            pollPriceJob(result.job_id);
        } catch (error) {
            showToast(error.message || 'Не удалось передать цену в WB', 'error');
        } finally {
            sendingPrice = false;
            nodes.confirmSend.disabled = false;
            nodes.confirmCancel.disabled = false;
            nodes.confirmClose.disabled = false;
            nodes.confirmSend.textContent = 'Подтвердить и отправить';
            if (product) updateSaveState(product);
        }
    }
    function finishPriceJob(jobId) {
        pendingPriceJobs = pendingPriceJobs.filter(function (item) { return item !== jobId; });
        writeJson(priceJobsKey, pendingPriceJobs);
    }
    function applyPricePlan(product, plan) {
        if (!product.price) {
            product._pendingPricePlan = plan;
            return;
        }
        product.price.current = finite(plan.display_retail_price, product.price.current);
        product.price.with_spp = finite(plan.predicted_spp_price, product.price.with_spp);
        product.price.with_wallet = finite(plan.predicted_wallet_price, product.price.with_wallet);
    }
    function applyPriceJobResult(result) {
        var accepted = result && Array.isArray(result.accepted) ? result.accepted : [];
        accepted.forEach(function (plan) {
            var product = productsById[plan.product_id];
            if (!product) return;
            applyPricePlan(product, plan);
        });
        renderPage();
        if (state.selected && productsById[state.selected] && productsById[state.selected]._detailLoaded) {
            fillCalculator(productsById[state.selected]);
            renderPriceCalculation(productsById[state.selected]);
        }
    }
    async function pollPriceJob(jobId) {
        try {
            var response = await window.fetch('/api/unit-economics-1c/prices/jobs/' + encodeURIComponent(jobId), {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var job = await response.json();
            if (response.status === 404) { finishPriceJob(jobId); return; }
            if (!response.ok) throw new Error(job.error || 'Не удалось проверить отправку цены');
            if (job.status === 'queued' || job.status === 'running') {
                window.setTimeout(function () { pollPriceJob(jobId); }, 2500);
                return;
            }
            finishPriceJob(jobId);
            if (job.status === 'success') {
                applyPriceJobResult(job.result);
                showToast('Цена успешно отправлена в WB');
            } else {
                showToast(job.error || 'WB не принял изменение цены', 'error');
            }
        } catch (error) {
            window.setTimeout(function () { pollPriceJob(jobId); }, 5000);
        }
    }
    async function saveSelectedProductSettings() {
        var product = productsById[state.selected];
        if (!product || !canEdit) return;
        var payload = { article: product.article };
        var valid = true;
        Array.prototype.forEach.call(nodes.parameters.querySelectorAll('[data-product-setting]'), function (input) {
            var value = Number(input.value);
            if (!Number.isFinite(value) || value < 0) {
                valid = false;
                return;
            }
            payload[input.dataset.productSetting] = value;
        });
        if (!valid) { showToast('Проверьте значения параметров'); return; }

        var button = nodes.parameters.querySelector('[data-save-product-settings]');
        var stateNode = nodes.parameters.querySelector('[data-product-settings-state]');
        button.disabled = true;
        button.textContent = 'Сохраняем…';
        try {
            var response = await window.fetch(
                '/api/unit-economics-1c/product-settings/' + encodeURIComponent(product.store_slug),
                {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json', 'Accept': 'application/json',
                        'X-Requested-With': 'fetch'
                    },
                    body: JSON.stringify(payload)
                }
            );
            var result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'Не удалось сохранить параметры');
            var saved = result.settings;
            product.product_settings = {
                store_slug: saved.store_slug, marketplace: saved.marketplace, article: saved.article,
                delivery_wb_rub: saved.delivery_wb_rub,
                return_cost_rub: saved.return_cost_rub, volume_l: saved.volume_l,
                storage_wb_rub: saved.storage_wb_rub, updated_at: saved.updated_at,
                updated_by_user_id: saved.updated_by_user_id, updated_by_name: saved.updated_by_name
            };
            product.details.delivery_wb_rub = saved.delivery_wb_rub;
            product.details.buyout_percent = saved.buyout_percent;
            product.details.return_cost_rub = saved.return_cost_rub;
            product.details.volume_l = saved.volume_l;
            product.details.storage_wb_rub = saved.storage_wb_rub;
            product.details.paid_acceptance_cost = saved.paid_acceptance_cost;
            product.details.delivery_with_returns = saved.delivery_with_returns;
            product.details.logistics = saved.delivery_with_returns;
            renderParameters(product);
            fillCalculator(product);
            renderPriceCalculation(product);
            showToast('Параметры товара сохранены');
        } catch (error) {
            button.disabled = false;
            button.textContent = 'Сохранить параметры';
            if (stateNode) stateNode.textContent = 'Не удалось сохранить';
            showToast(error.message || 'Не удалось сохранить параметры');
        }
    }
    nodes.search.addEventListener('input', function () {
        state.query = nodes.search.value;
        resetPageAndRender();
    });
    nodes.store.addEventListener('change', function () {
        state.store = nodes.store.value;
        resetPageAndRender();
    });
    nodes.columnsToggle.addEventListener('click', function () {
        var opening = nodes.columnPanel.hidden;
        nodes.columnPanel.hidden = !opening;
        nodes.columnsToggle.setAttribute('aria-expanded', String(opening));
    });
    nodes.columnList.addEventListener('change', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option || event.target.type !== 'checkbox') return;
        var key = option.dataset.columnKey;
        columnPreferences.hidden = columnPreferences.hidden.filter(function (item) { return item !== key; });
        if (!event.target.checked) columnPreferences.hidden.push(key);
        saveColumnPreferences();
    });
    nodes.columnList.addEventListener('click', function (event) {
        var button = event.target.closest('[data-column-move]');
        var option = event.target.closest('[data-column-key]');
        if (!button || !option) return;
        var index = columnPreferences.order.indexOf(option.dataset.columnKey);
        var next = button.dataset.columnMove === 'up' ? index - 1 : index + 1;
        if (index < 0 || next < 0 || next >= columnPreferences.order.length) return;
        var moved = columnPreferences.order.splice(index, 1)[0];
        columnPreferences.order.splice(next, 0, moved);
        saveColumnPreferences();
    });
    nodes.columnList.addEventListener('dragstart', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option) return;
        draggedColumnKey = option.dataset.columnKey;
        option.classList.add('is-dragging');
        option.setAttribute('aria-grabbed', 'true');
        if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = 'move';
            event.dataTransfer.setData('text/plain', draggedColumnKey);
        }
    });
    nodes.columnList.addEventListener('dragover', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option || !draggedColumnKey || option.dataset.columnKey === draggedColumnKey) return;
        event.preventDefault();
        option.classList.add('is-drag-target');
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    });
    nodes.columnList.addEventListener('dragleave', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (option) option.classList.remove('is-drag-target');
    });
    nodes.columnList.addEventListener('drop', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option || !draggedColumnKey) return;
        event.preventDefault();
        var from = columnPreferences.order.indexOf(draggedColumnKey);
        var to = columnPreferences.order.indexOf(option.dataset.columnKey);
        if (from >= 0 && to >= 0 && from !== to) {
            var moved = columnPreferences.order.splice(from, 1)[0];
            columnPreferences.order.splice(to, 0, moved);
            saveColumnPreferences();
        }
    });
    nodes.columnList.addEventListener('dragend', function () {
        draggedColumnKey = null;
        Array.prototype.forEach.call(nodes.columnList.querySelectorAll('.is-dragging, .is-drag-target'), function (item) {
            item.classList.remove('is-dragging', 'is-drag-target');
            item.setAttribute('aria-grabbed', 'false');
        });
    });
    Array.prototype.forEach.call(root.querySelectorAll('[data-state-filter]'), function (button) {
        button.addEventListener('click', function () {
            state.status = button.dataset.stateFilter;
            Array.prototype.forEach.call(root.querySelectorAll('[data-state-filter]'), function (item) {
                item.classList.toggle('is-active', item === button);
            });
            resetPageAndRender();
        });
    });
    nodes.pageSize.addEventListener('change', function () {
        var value = Number(nodes.pageSize.value);
        state.pageSize = [20, 50, 100].indexOf(value) === -1 ? 20 : value;
        resetPageAndRender();
    });
    function reloadForPeriod() {
        state.page = 1;
        state.tableFilters = {};
        renderTableHeader();
        if (window.CheckStockTableFilter && typeof window.CheckStockTableFilter.refresh === 'function') {
            window.CheckStockTableFilter.refresh(nodes.table);
        }
        loadProducts();
    }
    function markCustomPeriod() {
        nodes.periodDays.value = 'custom';
    }
    function applyCustomPeriod() {
        var dateFrom = String(nodes.periodFrom.value || '');
        var dateTo = String(nodes.periodTo.value || '');
        var days = periodDayCount(dateFrom, dateTo);
        if (!dateFrom || !dateTo) {
            showToast('Укажите начало и конец периода', 'error');
            return;
        }
        if (!days || dateFrom > dateTo) {
            showToast('Дата начала должна быть не позже даты окончания', 'error');
            return;
        }
        if (dateTo > state.lastCompleteDay) {
            showToast('Можно выбрать только завершённые дни — не позднее вчера', 'error');
            return;
        }
        if (days > maxPeriodDays) {
            showToast('Период не может превышать ' + maxPeriodDays + ' дней', 'error');
            return;
        }
        state.periodMode = 'custom';
        state.periodDays = days;
        state.periodFrom = dateFrom;
        state.periodTo = dateTo;
        syncPeriodControls();
        reloadForPeriod();
    }
    nodes.periodDays.addEventListener('change', function () {
        if (nodes.periodDays.value === 'custom') {
            markCustomPeriod();
            return;
        }
        var value = Number(nodes.periodDays.value);
        state.periodMode = 'preset';
        state.periodDays = [7, 14, 30].indexOf(value) !== -1 ? value : 7;
        state.periodTo = state.lastCompleteDay;
        state.periodFrom = shiftIsoDay(state.periodTo, 1 - state.periodDays);
        syncPeriodControls();
        reloadForPeriod();
    });
    nodes.periodFrom.addEventListener('input', markCustomPeriod);
    nodes.periodTo.addEventListener('input', markCustomPeriod);
    nodes.periodApply.addEventListener('click', applyCustomPeriod);
    [nodes.periodFrom, nodes.periodTo].forEach(function (input) {
        input.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            applyCustomPeriod();
        });
    });
    nodes.pagePrev.addEventListener('click', function () {
        if (state.page <= 1) return;
        state.page -= 1;
        renderPage();
    });
    nodes.pageNext.addEventListener('click', function () { state.page += 1; renderPage(); });
    nodes.pageNumbers.addEventListener('click', function (event) {
        var button = event.target.closest('[data-page]');
        if (!button) return;
        state.page = Number(button.dataset.page) || 1;
        renderPage();
    });
    nodes.productsRetry.addEventListener('click', loadProducts);
    nodes.rows.addEventListener('click', function (event) {
        var copy = event.target.closest('[data-copy-value]');
        if (copy) { copyText(copy); return; }
        var opener = event.target.closest('[data-product-open]');
        if (opener && productsById[opener.dataset.productOpen]) openDetail(productsById[opener.dataset.productOpen]);
    });
    nodes.gluedProducts.addEventListener('click', function (event) {
        var copy = event.target.closest('[data-copy-value]');
        if (copy) { copyText(copy); return; }
        var opener = event.target.closest('[data-glued-product-open]');
        if (!opener) return;
        var product = productsById[opener.dataset.gluedProductOpen];
        if (!product) {
            showToast('Не удалось найти связанный товар в таблице', 'error');
            return;
        }
        locateProductInTable(product);
    });
    nodes.rows.addEventListener('keydown', function (event) {
        var field = event.target.closest('[data-comment-id]');
        if (!field || event.key !== 'Enter' || event.shiftKey) return;
        event.preventDefault();
        field.blur();
    });
    nodes.rows.addEventListener('focusout', function (event) {
        var field = event.target.closest('[data-comment-id]');
        if (!field) return;
        setCommentText(field.dataset.commentId, field.value);
        writeJson(commentsKey, comments);
        field.value = commentText(field.dataset.commentId);
        showToast('Комментарий сохранён');
        if (state.query || state.tableFilters[1] || state.sortColumn === 1) renderPage();
    });
    nodes.detailClose.addEventListener('click', closeDetail);
    nodes.overlay.addEventListener('click', closeDetail);
    Array.prototype.forEach.call(root.querySelectorAll('[data-detail-tab]'), function (button) {
        button.addEventListener('click', function () { setDetailTab(button.dataset.detailTab); });
    });
    Array.prototype.forEach.call(nodes.calculatorInputs, function (input) {
        input.addEventListener('input', function () {
            var product = productsById[state.selected];
            if (!product) return;
            var source = input.dataset.calculatorInput;
            if (input === nodes.priceInput || input === nodes.sppPriceInput || input === nodes.walletPriceInput) {
                editedPriceKind = source;
                if (nodes.calculatorMode.checked) syncDetailedCalculatorInputs(product, source);
                else {
                    syncLinkedPriceInputs(product, editedPriceKind);
                    syncCompactCalculatorInputs(product);
                }
                updateSaveState(product);
            } else if (nodes.calculatorMode.checked) {
                syncDetailedCalculatorInputs(product, source);
                if (source === 'spp') editedPriceKind = 'client';
                else if (source === 'walletPercent') editedPriceKind = 'wallet';
                if (source === 'spp' || source === 'walletPercent') updateSaveState(product);
            } else if (source === 'drr') syncDetailedCalculatorInputs(product, source);
            renderPriceCalculation(product);
        });
    });
    nodes.subjectSelect.addEventListener('change', function () {
        var product = productsById[state.selected];
        var selectedSubject = String(nodes.subjectSelect.value || '').trim().toLocaleLowerCase('ru-RU');
        var subject = subjectCommissions.find(function (item) {
            return String(item.category || '').trim().toLocaleLowerCase('ru-RU') === selectedSubject;
        });
        if (!subject && product && String(product.details && product.details.subject || '')
            .trim().toLocaleLowerCase('ru-RU') === selectedSubject) {
            subject = {
                category: product.details.subject,
                commission_percent: product.details.subject_commission_percent
            };
        }
        if (!product || !subject) return;
        nodes.subjectSelect.value = subject.category;
        var subjectCommission = finite(subject.commission_percent, 0);
        var extra = finite(product.details && product.details.wb_extra_tariff_percent, 0);
        setCalculatorValue('commission', subjectCommission + extra);
        syncDetailedCalculatorInputs(product, 'commission');
        renderPriceCalculation(product);
    });
    [nodes.priceInput, nodes.sppPriceInput, nodes.walletPriceInput].forEach(function (input) {
        input.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            saveSelectedPrice();
        });
    });
    nodes.calculatorMode.addEventListener('change', function () {
        nodes.calculatorFields.classList.toggle('is-expanded', nodes.calculatorMode.checked);
    });
    nodes.breakEven.addEventListener('click', function () {
        var product = productsById[state.selected];
        if (!product) return;
        var result = breakEvenPrice(product, calculatorValues());
        if (result === null) {
            showToast('Недостаточно реальных данных для расчёта цены без убытка');
            return;
        }
        nodes.priceInput.value = String(result.retail);
        editedPriceKind = 'retail';
        if (nodes.calculatorMode.checked) syncDetailedCalculatorInputs(product, editedPriceKind);
        else {
            syncLinkedPriceInputs(product, editedPriceKind);
            syncCompactCalculatorInputs(product);
        }
        updateSaveState(product);
        renderPriceCalculation(product);
    });
    nodes.calculatorReset.addEventListener('click', function () {
        var product = productsById[state.selected];
        if (!product) return;
        editedPriceKind = 'retail';
        fillCalculator(product);
        updateSaveState(product);
        renderPriceCalculation(product);
        showToast('Параметры возвращены к значениям из базы');
    });
    nodes.savePrice.addEventListener('click', saveSelectedPrice);
    nodes.parameters.addEventListener('click', function (event) {
        if (event.target.closest('[data-save-product-settings]')) saveSelectedProductSettings();
    });
    nodes.chartWrap.addEventListener('change', function (event) {
        var series = event.target.dataset.chartSeries;
        if (series) {
            chartPreferences.series = chartPreferences.series.filter(function (key) { return key !== series; });
            if (event.target.checked) chartPreferences.series.push(series);
        } else if (event.target.hasAttribute('data-chart-compare')) {
            chartPreferences.compare = event.target.checked;
        } else return;
        writeJson(chartKey, chartPreferences);
        var product = productsById[state.selected];
        if (product) renderChart(product);
    });
    nodes.chartWrap.addEventListener('mouseleave', hideChartTooltip);
    nodes.confirmClose.addEventListener('click', closePriceConfirmation);
    nodes.confirmCancel.addEventListener('click', closePriceConfirmation);
    nodes.confirmSend.addEventListener('click', sendConfirmedPrice);
    nodes.confirmModal.addEventListener('click', function (event) {
        if (event.target === nodes.confirmModal) closePriceConfirmation();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        if (nodes.confirmModal.classList.contains('is-open')) closePriceConfirmation();
        else if (state.selected) closeDetail();
    });
    function refreshProductsIfStale(force) {
        if (document.hidden || state.productsRefreshing) return;
        if (!force && Date.now() - lastProductsLoadedAt < AUTO_REFRESH_STALE_MS) return;
        loadProducts({ silent: true, refreshDetail: true });
    }
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) refreshProductsIfStale(false);
    });
    window.addEventListener('focus', function () { refreshProductsIfStale(false); });
    window.setInterval(function () { refreshProductsIfStale(true); }, AUTO_REFRESH_INTERVAL_MS);

    renderStores();
    syncPeriodControls();
    Array.prototype.forEach.call(root.querySelectorAll('[data-chart-series]'), function (input) {
        input.checked = chartPreferences.series.indexOf(input.dataset.chartSeries) !== -1;
    });
    var compareControl = root.querySelector('[data-chart-compare]');
    if (compareControl) compareControl.checked = chartPreferences.compare;
    renderTableHeader();
    renderColumnSettings();
    nodes.table._tfAdapter = {
        values: tableFilterValues,
        filter: applyTableFilters,
        sort: applyTableSort
    };
    renderPage();
    loadProducts().then(function () {
        pendingPriceJobs.slice().forEach(pollPriceJob);
    });
})();
