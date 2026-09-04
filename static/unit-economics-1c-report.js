(function () {
    'use strict';

    var root = document.getElementById('unit-economics-1c-report');
    var configNode = document.getElementById('ue1cr-config');
    if (!root || !configNode) return;

    var config = JSON.parse(configNode.textContent || '{}');
    var AUTO_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
    var AUTO_REFRESH_STALE_MS = 60 * 1000;
    var filterRequestId = 0;
    var filtersEndpoint = String(
        config.filtersEndpoint || '/api/unit-economics-1c/reports/unit-profit/filters'
    );
    var nodes = {
        form: document.getElementById('ue1cr-filters'),
        from: document.getElementById('ue1cr-date-from'),
        to: document.getElementById('ue1cr-date-to'),
        submit: document.getElementById('ue1cr-submit'),
        export: document.getElementById('ue1cr-export'),
        table: root.querySelector('.ue1cr-table'),
        tableHead: document.getElementById('ue1cr-table-head'),
        tableTitle: document.getElementById('ue1cr-table-title'),
        groupHead: root.querySelector('.ue1cr-groups'),
        metricHead: root.querySelector('.ue1cr-table thead tr:nth-child(2)'),
        rows: document.getElementById('ue1cr-rows'),
        empty: document.getElementById('ue1cr-empty'),
        error: document.getElementById('ue1cr-error'),
        scopeNote: document.getElementById('ue1cr-scope-note'),
        marginCoverageNote: document.getElementById('ue1cr-margin-coverage-note'),
        showImages: document.getElementById('ue1cr-show-images'),
        storeOptions: document.getElementById('ue1cr-store-options'),
        subjectOptions: document.getElementById('ue1cr-subject-options'),
        managerOptions: document.getElementById('ue1cr-manager-options'),
        articleOptions: document.getElementById('ue1cr-article-options'),
        storeSummary: document.getElementById('ue1cr-store-summary'),
        subjectSummary: document.getElementById('ue1cr-subject-summary'),
        managerSummary: document.getElementById('ue1cr-manager-summary'),
        articleSummary: document.getElementById('ue1cr-article-summary'),
        viewButtons: root.querySelectorAll('[data-report-view]'),
        columnsToggle: document.getElementById('ue1cr-columns-toggle'),
        columnPanel: document.getElementById('ue1cr-column-panel'),
        columnList: document.getElementById('ue1cr-column-list'),
        dailyToggle: document.getElementById('ue1cr-daily-toggle'),
        pagination: document.getElementById('ue1cr-pagination'),
        pageSize: document.getElementById('ue1cr-page-size'),
        pageSummary: document.getElementById('ue1cr-page-summary'),
        pageLabel: document.getElementById('ue1cr-page-label'),
        pagePrev: document.getElementById('ue1cr-page-prev'),
        pageNext: document.getElementById('ue1cr-page-next')
    };
    var preferenceKey = 'unit-profit-report-view-v1';
    var preferences = {};
    try { preferences = JSON.parse(window.localStorage.getItem(preferenceKey) || '{}'); }
    catch (error) { preferences = {}; }
    var state = {
        store: new Set(),
        subject: new Set(),
        manager: new Set(),
        article: new Set(),
        rows: [],
        showImages: preferences.showImages === true,
        showDailyDetails: preferences.showDailyDetails === true,
        dailyDetailsLoaded: false,
        dailyDates: [],
        viewMode: preferences.viewMode === 'categories' ? 'categories' : 'products',
        totals: null,
        page: 1,
        pageSize: [25, 50, 100].indexOf(Number(preferences.pageSize)) !== -1
            ? Number(preferences.pageSize) : 50,
        totalCount: 0,
        totalPages: 1,
        paginationEnabled: false,
        unpaginatedSnapshot: null,
        exportBusy: false,
        loading: false,
        reportLoaded: false,
        lastLoadedAt: 0,
        followCurrentPeriod: true
    };
    if (state.viewMode === 'categories') state.showDailyDetails = false;
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
    var searchNodes = {
        store: document.getElementById('ue1cr-store-search'),
        subject: document.getElementById('ue1cr-subject-search'),
        manager: document.getElementById('ue1cr-manager-search'),
        article: document.getElementById('ue1cr-article-search')
    };
    var filterOptions = { store: [], subject: [], manager: [], article: [] };
    var filterMenus = nodes.form.querySelectorAll('.ue1cr-multi');
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
    var detailedMoney = new Intl.NumberFormat('ru-RU', {
        style: 'currency', currency: 'RUB', maximumFractionDigits: 2
    });
    var dailyColumns = [
        { key: 'advertising_spend', label: 'Расходы на рекламу, ₽', format: 'money' },
        { key: 'orders_count', label: 'Заказы, шт.', format: 'number' },
        { key: 'net_orders_count', label: 'Заказы − отмены, шт.', format: 'number' },
        { key: 'buyout_percent', label: 'Выкуп, %', format: 'percent' },
        { key: 'vat_percent', label: 'НДС, %', format: 'percent' },
        { key: 'usn_percent', label: 'УСН, %', format: 'percent' },
        { key: 'customer_price', label: 'Цена с СПП, ₽', format: 'money' },
        { key: 'retail_price', label: 'Цена без СПП, ₽', format: 'money' },
        { key: 'acquiring_percent', label: 'Эквайринг, %', format: 'percent' },
        { key: 'logistics', label: 'Логистика, ₽', format: 'money' },
        { key: 'storage', label: 'Хранение, ₽', format: 'money' },
        { key: 'commission_percent', label: 'Комиссия WB, %', format: 'percent' },
        { key: 'team_commission_percent', label: 'Комиссия компании, %', format: 'percent' },
        { key: 'fulfillment_cost', label: 'Фулфилмент, ₽', format: 'money' },
        { key: 'purchase_price', label: 'Закупочная цена, ₽', format: 'money' },
        { key: 'net_profit', label: 'Чистая прибыль, ₽', format: 'money', tone: true },
        { key: 'net_revenue', label: 'Чистая выручка, ₽', format: 'money' },
        { key: 'advertising_per_unit', label: 'Реклама за 1 шт., ₽', format: 'money' },
        { key: 'vat_value', label: 'НДС, ₽', format: 'money' },
        { key: 'usn_value', label: 'УСН, ₽', format: 'money' }
    ];
    var reportColumnGroups = [
        { key: 'product', label: 'Товар', fixed: true, columns: [
            { key: 'identity', label: 'Товар', format: 'identity' }
        ] },
        { key: 'store', label: 'Магазин', columns: [
            { key: 'store_name', label: 'Магазин', format: 'text' }
        ] },
        { key: 'subject', label: 'Категория', columns: [
            { key: 'subject', label: 'Категория', format: 'text' }
        ] },
        { key: 'manager', label: 'Менеджер', columns: [
            { key: 'manager', label: 'Менеджер', format: 'text' }
        ] },
        { key: 'funnel', label: 'Воронка продаж', columns: [
            { key: 'orders_count', label: 'Всего заказов, шт.', format: 'number' },
            { key: 'cancel_count', label: 'Всего отмен, шт.', format: 'number' },
            { key: 'net_orders_count', label: 'Заказы − отмены, шт.', format: 'number' },
            { key: 'orders_amount', label: 'ТО заказов, ₽', format: 'turnover' },
            { key: 'cancel_amount', label: 'Сумма отмен, ₽', format: 'turnover' },
            { key: 'net_orders_amount', label: 'ТО после отмен, ₽', format: 'turnover' },
            { key: 'buyout_percent', label: 'Выкуп', format: 'percent' }
        ] },
        { key: 'stock', label: 'Остатки', columns: [
            { key: 'stock', label: 'Всего', format: 'number', help: 'Текущие остатки WB: FBS + FBO + ФФ' },
            { key: 'stock_fbs', label: 'FBS', format: 'number' },
            { key: 'stock_fbo', label: 'FBO', format: 'number' },
            { key: 'stock_fulfillment', label: 'ФФ', format: 'number' },
            { key: 'stock_days', label: 'Хватит, дней', format: 'number',
                help: 'Текущие остатки / среднесуточные заказы за последние 21 день, как в юнитке WB. Без заказов — 0.' }
        ] },
        { key: 'advertising', label: 'Реклама', columns: [
            { key: 'impressions', label: 'Показы', format: 'number' },
            { key: 'clicks', label: 'Клики', format: 'number' },
            { key: 'ctr', label: 'CTR', format: 'percent', help: 'Доля кликов от показов' },
            { key: 'cpc', label: 'CPC', format: 'money', help: 'Средняя стоимость клика' },
            { key: 'advertising_spend', label: 'Расходы, ₽', format: 'money' },
            { key: 'drr', label: 'ДРР', format: 'percent', help: 'Доля рекламных расходов с учётом выкупа' }
        ] },
        { key: 'result', label: 'Результат', columns: [
            { key: 'margin_orders_count', label: 'Расчётные выкупы', format: 'number' },
            { key: 'margin', label: 'Маржа периода, ₽', format: 'number', tone: true, coverage: true },
            { key: 'purchase_value', label: 'Закупка периода, ₽', format: 'money', coverage: true },
            { key: 'roi', label: 'ROI', format: 'percent', tone: true, coverage: true,
                help: 'Окупаемость вложений' }
        ] }
    ];
    var defaultColumnOrder = reportColumnGroups.map(function (group) { return group.key; });
    var defaultMetricKeys = reportColumnGroups.reduce(function (keys, group) {
        return keys.concat(group.columns.map(function (column) { return column.key; }));
    }, []);
    var savedColumnPreferences = preferences.columns || {};
    var columnPreferences = {
        order: Array.isArray(savedColumnPreferences.order)
            ? savedColumnPreferences.order.filter(function (key) {
                return defaultColumnOrder.indexOf(key) !== -1;
            }) : defaultColumnOrder.slice(),
        hidden: Array.isArray(savedColumnPreferences.hidden)
            ? savedColumnPreferences.hidden.filter(function (key) {
                return defaultColumnOrder.indexOf(key) !== -1 && key !== 'product';
            }) : [],
        hiddenColumns: Array.isArray(savedColumnPreferences.hiddenColumns)
            ? savedColumnPreferences.hiddenColumns.filter(function (key) {
                return defaultMetricKeys.indexOf(key) !== -1 && key !== 'identity';
            }) : []
    };
    defaultColumnOrder.forEach(function (key) {
        if (columnPreferences.order.indexOf(key) === -1) columnPreferences.order.push(key);
    });
    columnPreferences.order = ['product'].concat(columnPreferences.order.filter(function (key) {
        return key !== 'product';
    }));
    var draggedColumnKey = null;

    function savePreferences() {
        try {
            window.localStorage.setItem(preferenceKey, JSON.stringify({
                viewMode: state.viewMode,
                pageSize: state.pageSize,
                showDailyDetails: state.showDailyDetails,
                showImages: state.showImages,
                columns: columnPreferences
            }));
        } catch (error) {
            // The report remains fully usable when browser storage is unavailable.
        }
    }

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
    function detailedRub(item) {
        return item === null || item === undefined ? '—' : detailedMoney.format(item);
    }
    function dayLabel(day) {
        var parts = String(day || '').split('-');
        return parts.length === 3 ? parts[2] + '.' + parts[1] + '.' + parts[0] : String(day || '');
    }
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
        var days = Array.isArray(row.margin_missing_days)
            ? row.margin_missing_days.map(dayLabel).join(', ') : '';
        return ' title="Маржа рассчитана только по датам с доступными снимками.'
            + (days ? ' Нет снимка маржи за: ' + escapeHtml(days) : '') + '"';
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
    function renderFilterOptions(kind) {
        var query = searchNodes[kind]
            ? searchNodes[kind].value.trim().toLocaleLowerCase('ru-RU') : '';
        var filtered = filterOptions[kind].filter(function (item) {
            var text = kind === 'article' ? [item.article, item.name, item.store_name].join(' ')
                : kind === 'store' ? [item.slug, item.name].join(' ') : String(item);
            return !query || text.toLocaleLowerCase('ru-RU').indexOf(query) !== -1;
        });
        if (kind === 'article') filtered = filtered.slice(0, 200);
        renderOptions(kind, filtered);
    }
    function setFilterOptions(kind, items) {
        filterOptions[kind] = items || [];
        renderFilterOptions(kind);
    }
    function closeFilterMenus(except) {
        filterMenus.forEach(function (menu) {
            if (menu !== except) menu.open = false;
        });
    }
    function productMedia(row) {
        if (!state.showImages || !row.image_url) return '';
        return '<img src="' + escapeHtml(row.image_url) + '" alt="" loading="lazy">';
    }
    function dailyCalculation(row, day) {
        if (!row._dailyByDate) {
            row._dailyByDate = {};
            (row.daily_calculations || []).forEach(function (item) {
                row._dailyByDate[item.date] = item;
            });
        }
        return row._dailyByDate[day] || null;
    }
    function dailyValue(item, column) {
        if (!item || item[column.key] === null || item[column.key] === undefined) return '—';
        if (column.format === 'money') return detailedRub(item[column.key]);
        if (column.format === 'percent') return value(item[column.key], '%');
        return value(item[column.key]);
    }
    function dailyCellsHtml(row) {
        if (!state.dailyDetailsLoaded) return '';
        return state.dailyDates.map(function (day, dayIndex) {
            var item = dailyCalculation(row, day);
            var incomplete = item && (item.available === false || item.complete === false);
            var title = incomplete
                ? ' title="Нет сохранённых параметров маржи для части данных за ' + escapeHtml(dayLabel(day)) + '"'
                : '';
            return dailyColumns.map(function (column) {
                var raw = item ? item[column.key] : null;
                var classes = 'num ue1cr-daily-cell ue1cr-daily-day-' + (dayIndex % 2);
                if (column.tone) classes += tone(raw);
                if (incomplete) classes += ' ue1cr-daily-cell--incomplete';
                return '<td class="' + classes + '"' + title + '>' + dailyValue(item, column) + '</td>';
            }).join('');
        }).join('');
    }
    function reportGroupByKey(key) {
        return reportColumnGroups.find(function (group) { return group.key === key; });
    }
    function visibleColumnsForGroup(group) {
        if (!group || group.fixed) return group ? group.columns.slice() : [];
        if (columnPreferences.hidden.indexOf(group.key) !== -1) return [];
        return group.columns.filter(function (column) {
            return columnPreferences.hiddenColumns.indexOf(column.key) === -1;
        });
    }
    function visibleColumnGroups() {
        return columnPreferences.order.map(reportGroupByKey).filter(Boolean).map(function (group) {
            return {
                key: group.key,
                label: group.label,
                fixed: group.fixed === true,
                columns: visibleColumnsForGroup(group)
            };
        }).filter(function (group) { return group.columns.length > 0; });
    }
    function reportColumnLabel(column) {
        var label = state.viewMode === 'categories' && column.key === 'identity'
            ? 'Категория' : column.label;
        var content = escapeHtml(label);
        if (column.help) {
            content = '<abbr title="' + escapeHtml(column.help) + '">' + content + '</abbr>';
        }
        return content;
    }
    function headerTotalHtml(column) {
        if (column.key === 'identity') {
            return '<strong class="ue1cr-head-total ue1cr-head-total--label" '
                + 'data-report-total-count>Итого: 0 поз.</strong>';
        }
        if (!column.key || ['store_name', 'subject', 'manager'].indexOf(column.key) !== -1) {
            return '<strong class="ue1cr-head-total ue1cr-head-total--empty" aria-hidden="true">&nbsp;</strong>';
        }
        return '<strong class="ue1cr-head-total" data-report-total="' + column.key + '">—</strong>';
    }
    function reportColumnHeader(column, columnIndex, groupKey) {
        var numeric = ['number', 'turnover', 'money', 'percent'].indexOf(column.format) !== -1;
        return '<th class="' + (numeric ? 'num' : '') + '" scope="col" data-report-group="'
            + escapeHtml(groupKey) + '" data-filter-column="' + columnIndex + '"'
            + (numeric ? ' data-filter-type="number"' : '') + '><span class="ue1cr-head-heading">'
            + '<span class="ue1cr-head-label">' + reportColumnLabel(column) + '</span>'
            + headerTotalHtml(column) + '</span></th>';
    }
    function renderBaseHeaders() {
        var groups = visibleColumnGroups();
        var groupHtml = '';
        var metricHtml = '';
        var columnIndex = 0;
        groups.forEach(function (group) {
            if (group.columns.length === 1) {
                groupHtml += reportColumnHeader(group.columns[0], columnIndex, group.key)
                    .replace('<th ', '<th rowspan="2" ');
                columnIndex += 1;
                return;
            }
            groupHtml += '<th colspan="' + group.columns.length + '" scope="colgroup" data-report-group="'
                + escapeHtml(group.key) + '">' + escapeHtml(group.label) + '</th>';
            group.columns.forEach(function (column) {
                metricHtml += reportColumnHeader(column, columnIndex, group.key);
                columnIndex += 1;
            });
        });
        nodes.tableHead.innerHTML = '<tr class="ue1cr-groups">' + groupHtml
            + '</tr><tr>' + metricHtml + '</tr>';
        nodes.groupHead = nodes.tableHead.querySelector('.ue1cr-groups');
        nodes.metricHead = nodes.tableHead.querySelector('tr:nth-child(2)');
    }
    function renderDailyHeaders() {
        renderBaseHeaders();
        if (!state.dailyDetailsLoaded) return;
        state.dailyDates.forEach(function (day, dayIndex) {
            var group = document.createElement('th');
            group.className = 'ue1cr-daily-column ue1cr-daily-group ue1cr-daily-day-' + (dayIndex % 2);
            group.colSpan = dailyColumns.length;
            group.scope = 'colgroup';
            group.textContent = dayLabel(day);
            nodes.groupHead.appendChild(group);
            dailyColumns.forEach(function (column) {
                var heading = document.createElement('th');
                heading.className = 'num ue1cr-daily-column ue1cr-daily-metric ue1cr-daily-day-'
                    + (dayIndex % 2);
                heading.scope = 'col';
                heading.textContent = column.label;
                nodes.metricHead.appendChild(heading);
            });
        });
    }
    function identityHtml(row) {
        if (row.row_kind === 'category') {
            return '<div class="ue1cr-category-cell"><strong>' + escapeHtml(row.name)
                + '</strong><span>' + value(row.product_count) + ' товаров</span></div>';
        }
        return '<div class="ue1cr-product-cell">' + productMedia(row) + '<div><strong>'
            + escapeHtml(row.name) + '</strong><span>'
            + copyIdentifier('Артикул', row.article, 'Арт. ' + row.article)
            + '</span></div></div>';
    }
    function reportCellHtml(row, column) {
        if (column.key === 'identity') return '<td>' + identityHtml(row) + '</td>';
        var raw = row[column.key];
        var content;
        if (column.format === 'turnover') content = turnover(raw);
        else if (column.format === 'money') content = rub(raw);
        else if (column.format === 'percent') content = value(raw, '%');
        else if (column.format === 'number') content = value(raw);
        else content = escapeHtml(raw === null || raw === undefined || raw === '' ? '—' : raw);
        var classes = column.format === 'text' ? '' : 'num';
        if (column.tone) classes += tone(raw);
        if (column.key === 'margin' && row.margin_complete === false) {
            classes += ' ue1cr-margin-cell--partial';
        }
        var title = column.coverage ? marginCoverageTitle(row) : '';
        var parsed = Number(raw);
        var filterValue = Number.isFinite(parsed) && column.format !== 'text'
            ? ' data-filter-value="' + String(parsed) + '"' : '';
        return '<td class="' + classes + '"' + filterValue + title + '>' + content + '</td>';
    }
    function reportCellsHtml(row) {
        return visibleColumnGroups().map(function (group) {
            return group.columns.map(function (column) {
                var cell = reportCellHtml(row, column);
                return cell.replace('<td', '<td data-report-group="' + escapeHtml(group.key) + '"');
            }).join('');
        }).join('');
    }
    function rowHtml(row, index) {
        return '<tr data-row-index="' + index + '" data-row-kind="' + escapeHtml(row.row_kind || 'product')
            + '">' + reportCellsHtml(row) + dailyCellsHtml(row) + '</tr>';
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
        if (countNode) {
            countNode.textContent = 'Итого: ' + value(rowCount)
                + (state.viewMode === 'categories' ? ' кат.' : ' поз.');
        }
        setHeaderTotal('orders_count', value(total.orders_count));
        setHeaderTotal('cancel_count', value(total.cancel_count));
        setHeaderTotal('net_orders_count', value(total.net_orders_count));
        setHeaderTotal('orders_amount', rub(total.orders_amount));
        setHeaderTotal('cancel_amount', rub(total.cancel_amount));
        setHeaderTotal('net_orders_amount', rub(total.net_orders_amount));
        setHeaderTotal('buyout_percent', value(total.buyout_percent, '%'));
        setHeaderTotal('stock', value(total.stock));
        setHeaderTotal('stock_fbs', value(total.stock_fbs));
        setHeaderTotal('stock_fbo', value(total.stock_fbo));
        setHeaderTotal('stock_fulfillment', value(total.stock_fulfillment));
        setHeaderTotal('stock_days', value(total.stock_days));
        setHeaderTotal('impressions', value(total.impressions));
        setHeaderTotal('clicks', value(total.clicks));
        setHeaderTotal('ctr', value(total.ctr, '%'));
        setHeaderTotal('cpc', rub(total.cpc));
        setHeaderTotal('advertising_spend', rub(total.advertising_spend));
        setHeaderTotal('drr', value(total.drr, '%'));
        setHeaderTotal('margin_orders_count', value(total.margin_orders_count));
        setHeaderTotal('margin', value(total.margin), total.margin);
        setHeaderTotal('purchase_value', rub(total.purchase_value));
        setHeaderTotal('roi', value(total.roi, '%'), total.roi);
    }
    function numeric(item) {
        var parsed = Number(item);
        return Number.isFinite(parsed) ? parsed : 0;
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
            result.stock_fbs += numeric(row.stock_fbs);
            result.stock_fbo += numeric(row.stock_fbo);
            result.stock_fulfillment += numeric(row.stock_fulfillment);
            result.stock_average_daily_orders += numeric(row.stock_average_daily_orders);
            result.impressions += numeric(row.impressions);
            result.clicks += numeric(row.clicks);
            result.advertising_spend += numeric(row.advertising_spend);
            result.expected_buyout_amount += numeric(row.expected_buyout_amount);
            if (row.margin !== null && row.margin !== undefined) {
                result.margin += numeric(row.margin);
                result.margin_available = true;
            }
            result.margin_orders_count += numeric(row.margin_orders_count);
            if (row.purchase_value !== null && row.purchase_value !== undefined) {
                result.purchase_value += numeric(row.purchase_value);
                result.purchase_available = true;
            }
            if (row.margin_complete === false) result.margin_complete = false;
            return result;
        }, {
            orders_count: 0, cancel_count: 0, net_orders_count: 0,
            orders_amount: 0, cancel_amount: 0, net_orders_amount: 0,
            buyout_orders_count: 0, buyout_weighted: 0, stock: 0,
            stock_fbs: 0, stock_fbo: 0, stock_fulfillment: 0, stock_average_daily_orders: 0,
            impressions: 0, clicks: 0, advertising_spend: 0,
            expected_buyout_amount: 0,
            margin_orders_count: 0, margin: 0, purchase_value: 0, margin_complete: true,
            margin_available: rows.length === 0, purchase_available: rows.length === 0
        });
        total.orders_amount = Math.round(total.orders_amount * 100) / 100;
        total.cancel_amount = Math.round(total.cancel_amount * 100) / 100;
        total.net_orders_amount = Math.round(total.net_orders_amount * 100) / 100;
        total.advertising_spend = Math.round(total.advertising_spend * 100) / 100;
        total.margin = Math.round(total.margin * 100) / 100;
        total.purchase_value = Math.round(total.purchase_value * 100) / 100;
        total.stock_days = total.stock_average_daily_orders > 0
            ? Math.round(total.stock / total.stock_average_daily_orders * 100) / 100 : 0;
        total.buyout_percent = total.buyout_orders_count
            ? Math.round(total.buyout_weighted / total.buyout_orders_count * 100) / 100 : null;
        total.ctr = total.impressions
            ? Math.round(total.clicks / total.impressions * 10000) / 100 : 0;
        total.cpc = total.clicks
            ? Math.round(total.advertising_spend / total.clicks * 100) / 100 : 0;
        total.drr = total.expected_buyout_amount
            ? Math.round(total.advertising_spend / total.expected_buyout_amount * 10000) / 100
            : total.advertising_spend ? 100 : 0;
        if (!total.margin_available) total.margin = null;
        if (!total.purchase_available) total.purchase_value = null;
        total.roi = total.margin !== null && total.purchase_value
            ? Math.round(total.margin / total.purchase_value * 10000) / 100
            : total.margin !== null ? 0 : null;
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
    function updateDailyVisibility() {
        var categories = state.viewMode === 'categories';
        nodes.dailyToggle.hidden = categories;
        root.classList.toggle(
            'ue1cr-page--daily-hidden',
            !state.showDailyDetails || categories
        );
        nodes.dailyToggle.classList.toggle('is-active', state.showDailyDetails);
        nodes.dailyToggle.setAttribute('aria-pressed', state.showDailyDetails ? 'true' : 'false');
        nodes.dailyToggle.textContent = state.showDailyDetails
            ? 'Скрыть показатели по дням'
            : 'Показатели по дням';
    }
    function renderPagination() {
        nodes.pagination.hidden = !state.paginationEnabled;
        if (!state.paginationEnabled) return;
        var first = state.totalCount ? (state.page - 1) * state.pageSize + 1 : 0;
        var last = Math.min(state.page * state.pageSize, state.totalCount);
        nodes.pageSummary.textContent = state.totalCount
            ? number.format(first) + '–' + number.format(last) + ' из ' + number.format(state.totalCount)
            : '0 строк';
        nodes.pageLabel.textContent = state.page + ' / ' + state.totalPages;
        nodes.pagePrev.disabled = state.page <= 1;
        nodes.pageNext.disabled = state.page >= state.totalPages;
        nodes.pageSize.value = String(state.pageSize);
    }
    function renderRows() {
        renderDailyHeaders();
        nodes.rows.innerHTML = state.rows.map(rowHtml).join('');
        renderHeaderTotals(
            state.totalCount ? state.totals : aggregateRows([]),
            state.totalCount
        );
        nodes.empty.textContent = state.reportLoaded
            ? 'За выбранный период данных нет.'
            : 'Настройте параметры и нажмите «Сформировать».';
        nodes.empty.hidden = state.rows.length > 0;
        updateDailyVisibility();
        renderPagination();
        if (window.CheckStockTableFilter && typeof window.CheckStockTableFilter.refresh === 'function') {
            window.CheckStockTableFilter.refresh(nodes.table);
        } else updateVisibleTotal();
    }
    function resetTableFilters() {
        nodes.table._tfFilters = {};
        nodes.table.querySelectorAll('.tf-btn').forEach(function (button) {
            button.classList.remove('tf-btn--active');
        });
    }
    function renderColumnSettings() {
        nodes.columnList.innerHTML = columnPreferences.order.map(function (key, index) {
            var group = reportGroupByKey(key);
            if (!group) return '';
            var visibleColumns = visibleColumnsForGroup(group);
            var checked = group.fixed || visibleColumns.length > 0;
            var canMoveUp = !group.fixed && index > 1;
            var canMoveDown = !group.fixed && index < columnPreferences.order.length - 1;
            var metricOptions = !group.fixed && group.columns.length > 1
                ? '<div class="ue1cr-column-metrics">' + group.columns.map(function (column) {
                    var metricChecked = columnPreferences.hiddenColumns.indexOf(column.key) === -1;
                    return '<label><input type="checkbox" data-column-metric="'
                        + escapeHtml(column.key) + '"' + (metricChecked ? ' checked' : '')
                        + '><span>' + escapeHtml(column.label) + '</span></label>';
                }).join('') + '</div>' : '';
            return '<div class="ue1cr-column-option" data-column-key="' + escapeHtml(key)
                + '" draggable="' + (group.fixed ? 'false' : 'true') + '" aria-grabbed="false">'
                + '<div class="ue1cr-column-option-head"><span class="ue1cr-column-grip" title="Перетащить">⋮⋮</span>'
                + '<label><input type="checkbox" data-column-group-toggle' + (checked ? ' checked' : '')
                + (group.fixed ? ' disabled' : '') + '><span>' + escapeHtml(group.label) + '</span></label>'
                + '<div><button type="button" data-column-move="up"' + (canMoveUp ? '' : ' disabled')
                + ' aria-label="Выше">↑</button><button type="button" data-column-move="down"'
                + (canMoveDown ? '' : ' disabled') + ' aria-label="Ниже">↓</button></div></div>'
                + metricOptions + '</div>';
        }).join('');
        nodes.columnList.querySelectorAll('[data-column-group-toggle]').forEach(function (input) {
            var option = input.closest('[data-column-key]');
            var group = option ? reportGroupByKey(option.dataset.columnKey) : null;
            if (!group || group.fixed || group.columns.length < 2) return;
            var visibleCount = visibleColumnsForGroup(group).length;
            input.indeterminate = visibleCount > 0 && visibleCount < group.columns.length;
        });
    }
    function applyColumnPreferences() {
        columnPreferences.order = ['product'].concat(columnPreferences.order.filter(function (key) {
            return key !== 'product';
        }));
        resetTableFilters();
        savePreferences();
        renderColumnSettings();
        renderRows();
    }
    function setView(mode) {
        state.viewMode = mode === 'categories' ? 'categories' : 'products';
        if (state.viewMode === 'categories') state.showDailyDetails = false;
        state.page = 1;
        state.dailyDetailsLoaded = false;
        state.unpaginatedSnapshot = null;
        nodes.tableTitle.textContent = state.viewMode === 'categories' ? 'Категории' : 'Товары';
        nodes.viewButtons.forEach(function (button) {
            var active = button.dataset.reportView === state.viewMode;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        resetTableFilters();
        savePreferences();
        updateDailyVisibility();
        updateExportLink();
        if (state.reportLoaded) load();
    }
    function appendFilters(query, kind, parameter) {
        state[kind].forEach(function (item) { query.append(parameter, item); });
    }
    function reportQuery(includeView) {
        var query = new URLSearchParams({ date_from: nodes.from.value, date_to: nodes.to.value });
        appendFilters(query, 'store', 'store');
        appendFilters(query, 'subject', 'subject');
        appendFilters(query, 'manager', 'manager');
        appendFilters(query, 'article', 'article');
        if (state.showDailyDetails) query.set('daily_details', '1');
        if (state.viewMode === 'categories') query.set('group_by', 'subject');
        if (!includeView && state.showDailyDetails && state.viewMode === 'products') {
            query.set('page', String(state.page));
            query.set('page_size', String(state.pageSize));
        }
        return query;
    }
    function updateExportLink() {
        if (!nodes.export) return;
        nodes.export.href = '/sales/unit-economics-1c/reports/unit-profit.xlsx?'
            + reportQuery(true).toString();
    }
    async function loadFilterOptions() {
        var requestId = ++filterRequestId;
        var query = new URLSearchParams();
        appendFilters(query, 'store', 'store');
        try {
            var response = await window.fetch(filtersEndpoint + (query.toString() ? '?' + query : ''), {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var result = await response.json();
            if (!response.ok || !result.ok) {
                throw new Error(result.error || 'Не удалось загрузить параметры отчёта');
            }
            if (requestId !== filterRequestId) return;
            setFilterOptions('subject', result.filters.subjects);
            setFilterOptions('manager', result.filters.managers);
            setFilterOptions('article', result.filters.articles);
        } catch (error) {
            if (requestId !== filterRequestId) return;
            nodes.error.textContent = error.message || 'Не удалось загрузить параметры отчёта';
            nodes.error.hidden = false;
        }
    }
    function exportFilename(response) {
        var disposition = response.headers.get('Content-Disposition') || '';
        var utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        if (utf8Match) {
            try { return decodeURIComponent(utf8Match[1]); }
            catch (error) { return utf8Match[1]; }
        }
        var plainMatch = disposition.match(/filename="?([^";]+)"?/i);
        if (plainMatch) return plainMatch[1];
        return 'unit_profit_' + nodes.from.value + '_' + nodes.to.value + '.xlsx';
    }
    function setExportBusy(busy) {
        state.exportBusy = busy;
        nodes.export.classList.toggle('is-loading', busy);
        nodes.export.setAttribute('aria-busy', busy ? 'true' : 'false');
        nodes.export.setAttribute('aria-disabled', busy ? 'true' : 'false');
        nodes.export.textContent = busy ? 'Формируем Excel…' : 'Excel';
    }
    async function downloadExcel(event) {
        event.preventDefault();
        if (state.exportBusy) return;
        updateExportLink();
        setExportBusy(true);
        nodes.error.hidden = true;
        try {
            var response = await window.fetch(nodes.export.href, {
                headers: {
                    'Accept': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'X-Requested-With': 'fetch'
                }
            });
            if (!response.ok) {
                var message = 'Не удалось сформировать Excel';
                try {
                    var errorResult = await response.json();
                    message = errorResult.error || errorResult.detail || message;
                } catch (error) { /* The server did not return JSON. */ }
                throw new Error(message);
            }
            var blob = await response.blob();
            var objectUrl = window.URL.createObjectURL(blob);
            var download = document.createElement('a');
            download.href = objectUrl;
            download.download = exportFilename(response);
            document.body.appendChild(download);
            download.click();
            download.remove();
            window.setTimeout(function () { window.URL.revokeObjectURL(objectUrl); }, 1000);
        } catch (error) {
            nodes.error.textContent = error.message || 'Не удалось сформировать Excel';
            nodes.error.hidden = false;
        } finally {
            setExportBusy(false);
        }
    }
    async function load(options) {
        options = options || {};
        var silent = options.silent === true;
        if (state.loading) return;
        state.loading = true;
        if (!silent) {
            nodes.submit.disabled = true;
            nodes.dailyToggle.disabled = true;
            nodes.pagePrev.disabled = true;
            nodes.pageNext.disabled = true;
            nodes.submit.textContent = 'Считаем…';
            nodes.error.hidden = true;
        }
        var query = reportQuery(false);
        updateExportLink();
        try {
            var response = await window.fetch('/api/unit-economics-1c/reports/unit-profit?' + query.toString(), {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'Не удалось сформировать отчёт');
            nodes.error.hidden = true;
            nodes.scopeNote.hidden = true;
            nodes.marginCoverageNote.hidden = true;
            state.rows = result.rows || [];
            state.totals = result.totals;
            state.dailyDetailsLoaded = result.daily_details === true;
            state.dailyDates = state.dailyDetailsLoaded && state.rows.length
                ? (state.rows[0].daily_calculations || []).map(function (item) { return item.date; })
                : [];
            state.page = Number((result.pagination || {}).page || 1);
            state.pageSize = Number((result.pagination || {}).page_size || state.pageSize);
            state.totalCount = Number((result.pagination || {}).total_count || 0);
            state.totalPages = Number((result.pagination || {}).total_pages || 1);
            state.paginationEnabled = (result.pagination || {}).enabled === true;
            if (!state.showDailyDetails && state.viewMode === 'products') {
                state.unpaginatedSnapshot = {
                    rows: state.rows,
                    totals: state.totals,
                    totalCount: state.totalCount
                };
            }
            state.reportLoaded = true;
            state.lastLoadedAt = Date.now();
            setFilterOptions('subject', result.filters.subjects);
            setFilterOptions('manager', result.filters.managers);
            setFilterOptions('article', result.filters.articles);
            renderRows();
            var undercoveredDays = result.totals
                ? result.totals.margin_undercovered_days || [] : [];
            if (undercoveredDays.length) {
                nodes.marginCoverageNote.textContent = 'Снимки маржи есть менее чем у 70% товаров за '
                    + undercoveredDays.map(dayLabel).join(', ')
                    + '. Маржа рассчитана по остальным датам.';
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
            state.loading = false;
            if (!silent) {
                nodes.submit.disabled = false;
                nodes.dailyToggle.disabled = false;
                nodes.submit.textContent = 'Сформировать';
            }
            renderPagination();
        }
    }

    function moscowToday() {
        var parts = new Intl.DateTimeFormat('en-CA', {
            timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'
        }).formatToParts(new Date());
        var values = {};
        parts.forEach(function (part) { values[part.type] = part.value; });
        return values.year + '-' + values.month + '-' + values.day;
    }
    function daysBefore(day, count) {
        var parts = day.split('-').map(Number);
        var value = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
        value.setUTCDate(value.getUTCDate() - count);
        return value.toISOString().slice(0, 10);
    }
    function refreshReportIfStale(force) {
        if (!state.reportLoaded || document.hidden || state.loading || state.exportBusy) return;
        if (!force && Date.now() - state.lastLoadedAt < AUTO_REFRESH_STALE_MS) return;
        if (state.followCurrentPeriod) {
            var today = moscowToday();
            nodes.to.value = today;
            nodes.from.value = daysBefore(today, 6);
            updateExportLink();
        }
        load({ silent: true });
    }

    function setDailyDetails(enabled) {
        state.showDailyDetails = !!enabled;
        state.page = 1;
        savePreferences();
        updateDailyVisibility();
        updateExportLink();
        if (!state.reportLoaded) return;
        if (state.showDailyDetails) {
            state.dailyDetailsLoaded = false;
            load();
            return;
        }
        state.dailyDetailsLoaded = false;
        state.dailyDates = [];
        if (state.unpaginatedSnapshot) {
            state.rows = state.unpaginatedSnapshot.rows;
            state.totals = state.unpaginatedSnapshot.totals;
            state.totalCount = state.unpaginatedSnapshot.totalCount;
            state.totalPages = 1;
            state.paginationEnabled = false;
            renderRows();
        } else {
            load();
        }
    }

    root.addEventListener('change', function (event) {
        var kind = event.target.dataset.filterOption;
        if (!kind) {
            if (event.target === nodes.from || event.target === nodes.to) {
                state.unpaginatedSnapshot = null;
                state.followCurrentPeriod = false;
            }
            updateExportLink();
            return;
        }
        state.unpaginatedSnapshot = null;
        if (event.target.checked) state[kind].add(event.target.value);
        else state[kind].delete(event.target.value);
        if (kind === 'store') {
            ['subject', 'manager', 'article'].forEach(function (child) {
                state[child].clear();
                if (searchNodes[child]) searchNodes[child].value = '';
                summary(child);
            });
            loadFilterOptions();
        }
        summary(kind);
        updateExportLink();
    });
    nodes.showImages.addEventListener('change', function () {
        state.showImages = nodes.showImages.checked;
        savePreferences();
        renderRows();
    });
    nodes.viewButtons.forEach(function (button) {
        button.addEventListener('click', function () { setView(button.dataset.reportView); });
    });
    nodes.columnsToggle.addEventListener('click', function () {
        var open = nodes.columnPanel.hidden;
        nodes.columnPanel.hidden = !open;
        nodes.columnsToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    filterMenus.forEach(function (menu) {
        menu.addEventListener('toggle', function () {
            if (menu.open) closeFilterMenus(menu);
        });
    });
    document.addEventListener('click', function (event) {
        filterMenus.forEach(function (menu) {
            if (!menu.contains(event.target)) menu.open = false;
        });
        if (nodes.columnPanel.hidden || nodes.columnPanel.contains(event.target)
            || nodes.columnsToggle.contains(event.target)) return;
        nodes.columnPanel.hidden = true;
        nodes.columnsToggle.setAttribute('aria-expanded', 'false');
    });
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        filterMenus.forEach(function (menu) {
            if (!menu.open) return;
            event.preventDefault();
            menu.open = false;
            if (menu.contains(document.activeElement)) menu.querySelector('summary').focus();
        });
    });
    nodes.columnList.addEventListener('change', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option || event.target.type !== 'checkbox' || option.dataset.columnKey === 'product') return;
        var key = option.dataset.columnKey;
        var metricKey = event.target.dataset.columnMetric;
        if (metricKey) {
            columnPreferences.hiddenColumns = columnPreferences.hiddenColumns.filter(function (item) {
                return item !== metricKey;
            });
            if (!event.target.checked) columnPreferences.hiddenColumns.push(metricKey);
            else columnPreferences.hidden = columnPreferences.hidden.filter(function (item) {
                return item !== key;
            });
            applyColumnPreferences();
            return;
        }
        if (!event.target.hasAttribute('data-column-group-toggle')) return;
        columnPreferences.hidden = columnPreferences.hidden.filter(function (item) { return item !== key; });
        if (!event.target.checked) columnPreferences.hidden.push(key);
        else {
            var group = reportGroupByKey(key);
            var metricKeys = group ? group.columns.map(function (column) { return column.key; }) : [];
            columnPreferences.hiddenColumns = columnPreferences.hiddenColumns.filter(function (item) {
                return metricKeys.indexOf(item) === -1;
            });
        }
        applyColumnPreferences();
    });
    nodes.columnList.addEventListener('click', function (event) {
        var button = event.target.closest('[data-column-move]');
        var option = event.target.closest('[data-column-key]');
        if (!button || !option || option.dataset.columnKey === 'product') return;
        var index = columnPreferences.order.indexOf(option.dataset.columnKey);
        var next = button.dataset.columnMove === 'up' ? index - 1 : index + 1;
        if (index < 1 || next < 1 || next >= columnPreferences.order.length) return;
        var moved = columnPreferences.order.splice(index, 1)[0];
        columnPreferences.order.splice(next, 0, moved);
        applyColumnPreferences();
    });
    nodes.columnList.addEventListener('dragstart', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option || option.dataset.columnKey === 'product') return;
        draggedColumnKey = option.dataset.columnKey;
        option.classList.add('is-dragging');
        option.setAttribute('aria-grabbed', 'true');
        if (event.dataTransfer) event.dataTransfer.setData('text/plain', draggedColumnKey);
    });
    nodes.columnList.addEventListener('dragover', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option || !draggedColumnKey || option.dataset.columnKey === 'product'
            || option.dataset.columnKey === draggedColumnKey) return;
        event.preventDefault();
        option.classList.add('is-drag-target');
    });
    nodes.columnList.addEventListener('dragleave', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (option) option.classList.remove('is-drag-target');
    });
    nodes.columnList.addEventListener('drop', function (event) {
        var option = event.target.closest('[data-column-key]');
        if (!option || !draggedColumnKey || option.dataset.columnKey === 'product') return;
        event.preventDefault();
        var from = columnPreferences.order.indexOf(draggedColumnKey);
        var to = columnPreferences.order.indexOf(option.dataset.columnKey);
        if (from >= 1 && to >= 1 && from !== to) {
            var moved = columnPreferences.order.splice(from, 1)[0];
            columnPreferences.order.splice(to, 0, moved);
            applyColumnPreferences();
        }
    });
    nodes.columnList.addEventListener('dragend', function () {
        draggedColumnKey = null;
        nodes.columnList.querySelectorAll('.is-dragging, .is-drag-target').forEach(function (item) {
            item.classList.remove('is-dragging', 'is-drag-target');
            item.setAttribute('aria-grabbed', 'false');
        });
    });
    nodes.dailyToggle.addEventListener('click', function () {
        setDailyDetails(!state.showDailyDetails);
    });
    nodes.pagePrev.addEventListener('click', function () {
        if (state.page <= 1) return;
        state.page -= 1;
        state.dailyDetailsLoaded = false;
        load();
    });
    nodes.pageNext.addEventListener('click', function () {
        if (state.page >= state.totalPages) return;
        state.page += 1;
        state.dailyDetailsLoaded = false;
        load();
    });
    nodes.pageSize.addEventListener('change', function () {
        state.pageSize = Number(nodes.pageSize.value) || 50;
        state.page = 1;
        state.dailyDetailsLoaded = false;
        savePreferences();
        load();
    });
    Object.keys(searchNodes).forEach(function (kind) {
        var input = searchNodes[kind];
        if (!input) return;
        input.addEventListener('input', function () { renderFilterOptions(kind); });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') event.preventDefault();
        });
    });
    nodes.table.addEventListener('tablefilterchange', updateVisibleTotal);
    nodes.export.addEventListener('click', downloadExcel);
    nodes.form.addEventListener('submit', function (event) {
        event.preventDefault();
        closeFilterMenus();
        state.page = 1;
        state.dailyDetailsLoaded = false;
        state.unpaginatedSnapshot = null;
        load();
    });
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) refreshReportIfStale(false);
    });
    window.addEventListener('focus', function () { refreshReportIfStale(false); });
    window.setInterval(function () { refreshReportIfStale(true); }, AUTO_REFRESH_INTERVAL_MS);
    nodes.from.value = config.defaultDateFrom;
    nodes.to.value = config.defaultDateTo;
    nodes.showImages.checked = state.showImages;
    nodes.pageSize.value = String(state.pageSize);
    nodes.tableTitle.textContent = state.viewMode === 'categories' ? 'Категории' : 'Товары';
    nodes.viewButtons.forEach(function (button) {
        var active = button.dataset.reportView === state.viewMode;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    updateDailyVisibility();
    renderColumnSettings();
    renderDailyHeaders();
    renderPagination();
    setFilterOptions('store', config.stores);
    setFilterOptions('subject', []);
    setFilterOptions('manager', []);
    setFilterOptions('article', []);
    updateExportLink();
    loadFilterOptions();
})();
