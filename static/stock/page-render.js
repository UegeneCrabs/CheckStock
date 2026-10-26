(() => {
    'use strict';
    const ui = window.CheckStockUI;
    const e = ui.escapeHtml;
    const n = value => Number(value || 0).toLocaleString('ru-RU');
    const money = value => value == null ? '—' : Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ₽';
    const cell = value => Number(value) ? n(value) : '—';
    const marketNames = { WB: 'Wildberries', OZON: 'Ozon', 'YANDEX MARKET': 'Яндекс Маркет' };
    const operations = {"delivery": "Поставка на ФФ", "manual_add": "Ручная докладка", "transfer": "Перемещение", "transfer_dispatch": "Перемещение отправлено", "transfer_receive": "Перемещение принято", "transfer_receive_revert": "Приёмка возвращена в путь", "transfer_cancel": "Перемещение отменено", "shipment": "Отгрузка со стока", "fbo_dispatch": "Отгрузка на склады FBO", "fbo_receive": "Отгрузка на FBO принята", "fbo_receive_revert": "Приёмка отгрузки на FBO возвращена в путь", "fbo_cancel": "Отгрузка на FBO отменена", "fbs_transfer": "Перемещение на FBS", "trash": "Списание в мусорку"};
    const filters = [['', 'Все'], ['delivery', 'Поставки'], ['transfer', 'Перемещения'], ['fbs_transfer', 'На FBS'], ['shipment', 'Отгрузки'], ['manual_add', 'Ручные докладки']];
    const views = [['summary', 'Сводная'], ['deliveries', 'Поставки'], ['transfers', 'Перемещения'], ['shipments', 'Отгрузки'], ['fbs_transfers', 'На FBS'], ['fbs_sales', 'Продажи FBS']];
    const option = (value, label, selected) => `<option value="${e(value)}"${value === selected ? ' selected' : ''}>${e(label)}</option>`;
    const storeOptions = (context, selected, all = true) => (all ? option('', 'Все магазины', selected) : '') + context.stores.map(store => option(store.slug, store.name, selected)).join('');
    const name = (context, slug) => context.stores.find(store => store.slug === slug)?.name || slug;
    const markets = context => [...new Set(context.stores.flatMap(store => store.marketplaces))];
    const link = (path, values) => e(path + '?' + new URLSearchParams(values));
    const empty = (columns, text = 'Нет данных') => `<tr class="empty-row"><td colspan="${columns}">${e(text)}</td></tr>`;
    const copy = (value, kind) => `<button class="stock-copy" type="button" data-copy-kind="${e(kind)}" data-copy-value="${e(value)}" aria-label="Скопировать ${e(kind)} ${e(value)}">${e(value)}</button>`;
    const metric = (label, value) => `<div role="listitem"><span>${e(label)}</span><strong>${e(value)}</strong></div>`;
    const head = (label, total, cls, totalClass = '') => `<th class="${e(cls)}"><span class="stock-head-heading"><span class="stock-head-label">${e(label)}</span><strong class="stock-head-total ${e(totalClass)}">${e(total)}</strong></span></th>`;

    function product(item) {
        const title = item.name || item.article;
        const image = /^https?:\/\//.test(item.image_url || '') ? `<img class="stock-product-image" src="${e(item.image_url)}" alt="" loading="lazy" decoding="async">` : '';
        const tone = [...item.article].reduce((sum, char) => sum + char.codePointAt(0), 0) % 5;
        return `<td class="col-product"><div class="stock-product"><span class="stock-product-media stock-product-media--${tone}" aria-hidden="true"><span>${e(title.slice(0, 1).toUpperCase())}</span>${image}</span><span class="stock-product-copy"><strong class="stock-product-name" title="${e(title)}">${e(title)}</strong><span class="stock-product-meta">${copy(item.article, 'Артикул')}<span class="stock-meta-separator" aria-hidden="true">·</span>${item.barcode ? copy(item.barcode, 'Баркод') : '<span class="stock-code-empty">Баркод —</span>'}</span></span></div></td>`;
    }

    async function overview(context) {
        const data = await ui.request('/stock/data');
        const cards = data.stores.map(store => {
            const problems = context.alerts.filter(alert => alert.store_slug === store.store_slug);
            const title = name(context, store.store_slug);
            const status = problems.length ? `Проблем с доступом: ${problems.length}` : store.marketplaces.length ? 'Данные доступны' : 'Ожидает синхронизации';
            return `<a class="store-card stock-store-card" href="/stock/${e(store.store_slug)}" data-store-color="${e(store.store_slug)}"><span class="stock-store-head"><span class="store-avatar">${e(title.slice(0, 2).toUpperCase())}</span><span aria-hidden="true">›</span></span><strong class="store-name">${e(title)}</strong><span class="store-card-status${problems.length ? ' store-card-status--warning' : ''}"><i aria-hidden="true"></i>${e(status)}</span><span class="store-card-metrics"><span><small>Остаток</small><b>${n(store.total_stock)}</b></span><span><small>Товаров</small><b>${n(store.sku_count)}</b></span></span><span class="store-marketplaces">${store.marketplaces.map(mp => `<span>${e(marketNames[mp] || mp)}</span>`).join('') || '<span class="is-muted">Нет данных</span>'}</span></a>`;
        }).join('');
        return { title: 'CheckStock — Остатки', section: 'stock', values: { last_sync: e(ui.date(data.last_sync)), store_cards: cards, sync_products_button: '<button class="btn-primary btn-sync" type="button" id="sync-products-btn" aria-describedby="sync-products-status">Обновить остатки</button>', stock_summary: `<div class="stock-summary" role="list">${metric('Магазинов', data.stores.length)}${metric('Общий остаток', n(data.total_stock))}${metric('Товаров в каталогах', n(data.total_sku))}${metric('Подключений к площадкам', n(data.total_connections))}${metric('Состояние данных', context.alerts.length || 'В норме')}</div>` } };
    }

    async function stock(context, url) {
        const data = await ui.request(url.pathname + '/data' + url.search);
        const store = context.stores.find(item => item.slug === data.store_slug);
        const schemeTitle = scheme => data.marketplace === 'YANDEX MARKET' ? (scheme === 'fbo' ? 'FBY — склады Маркета' : scheme === 'fbs' ? 'FBS — склады продавца' : 'FBS — ' + scheme.replace(/^fbs_?/, '')) : 'Текущий сток в продаже ' + (scheme === 'rfbs' ? 'rFBS' : scheme.toUpperCase());
        const headers = head('Товар / артикул', 'Итого', 'col-product', 'stock-head-total--label') + head('Тотал', n(data.totals.grand), 'col-row-total', 'tot-grand') + head('Доступно ФФ для распределения', n(data.totals.ff), 'col-ff-available', 'tot-ff') + head('В пути', n(data.totals.transit), 'col-transit', 'tot-transit') + data.schemes.map(scheme => head(schemeTitle(scheme), n(data.totals[scheme]), 'col-scheme col-' + scheme, 'tot-' + scheme)).join('') + '<th class="col-filler"></th>';
        const rows = data.items.map(item => `<tr${item.ff_available < 0 || item.ff_available > 0 && !Object.values(item.schemes).some(Boolean) ? ' class="row-alert"' : ''} data-article="${e(item.article)}" data-search-aliases="${e(item.barcodes.join(' '))}" tabindex="0" aria-label="Открыть карточку ${e(item.name || item.article)}">${product(item)}<td class="col-row-total">${cell(item.total)}</td><td class="col-ff-available">${cell(item.ff_available)}</td><td class="col-transit">${cell(item.transit)}</td>${data.schemes.map(scheme => `<td class="col-scheme col-${e(scheme)}">${cell(item.schemes[scheme])}</td>`).join('')}<td class="col-filler"><button class="stock-row-open" type="button" aria-label="Открыть карточку">›</button></td></tr>`).join('');
        return { title: 'CheckStock — Остатки — ' + store.name, section: 'stock', contentClass: 'content--store', values: {
            mutation_user_id: e(context.user.id), store_name: e(store.name), slug: e(store.slug), marketplace: e(data.marketplace), mp_ready: data.ready ? '1' : '0', can_edit_stock: data.can_edit ? '1' : '0',
            ff_options: data.fulfillments.map(value => option(value, value)).join(''),
            mp_tabs: store.marketplaces.map(mp => `<button class="mp-tab${mp === data.marketplace ? ' active' : ''}" type="button" role="tab" data-mp="${e(mp)}" data-ready="${Number(store.configured_marketplaces.includes(mp))}">${e(mp)}</button>`).join(''),
            total_button: '<button class="mp-tab mp-tab--total" type="button" role="tab" aria-selected="false" data-store-total>ТОТАЛ</button>',
            total_download_button: `<a class="btn-secondary stock-total-download" href="${link('/stock/total.xlsx', { store: store.slug })}" download>Скачать XLSX</a>`,
            mp_source_options: store.marketplaces.map(mp => option(mp, mp + ' — не распределено', 'WB')).join(''),
            mp_target_options: markets(context).map(mp => option(mp, mp + ' — не распределено', 'WB')).join(''),
            access_problems: e(JSON.stringify(context.alerts.filter(alert => alert.store_slug === store.slug && ['credentials_missing', 'access_denied'].includes(alert.code)).map(alert => ({ kind: 'invalid', title: marketNames[alert.marketplace] || alert.marketplace, status: alert.code === 'credentials_missing' ? 'API-ключ не заполнен или недействителен.' : 'Проверьте права API-ключа для раздела ' + alert.scope, detail: 'Обновите ключ и повторите выгрузку. До исправления показываем ранее загруженные данные.' })))),
            stock_head: '<tr>' + headers + '</tr>', scheme_list: e(data.schemes.join(',')), scheme_count: data.schemes.length,
            stock_rows: rows || empty(5 + data.schemes.length, 'Пока нет остатков по этому магазину')
        } };
    }

    function warehouseTable(rows, message, top = 0) {
        if (!rows.length) return `<div class="warehouse-table-block"><div class="table-wrap"><table class="data-table data-table--warehouses"><tbody>${empty(1, message)}</tbody></table></div></div>`;
        const warehouseTotals = new Map();
        rows.forEach(row => warehouseTotals.set(row.warehouse, (warehouseTotals.get(row.warehouse) || 0) + row.quantity));
        let columns = [...warehouseTotals.keys()].sort((a, b) => a.localeCompare(b, 'ru'));
        let others = new Set();
        if (top && columns.length > top) {
            columns.sort((a, b) => warehouseTotals.get(b) - warehouseTotals.get(a) || a.localeCompare(b, 'ru'));
            others = new Set(columns.slice(top)); columns = columns.slice(0, top); columns.push(null);
        }
        const products = new Map();
        rows.forEach(row => {
            const key = JSON.stringify([row.barcode, row.article, row.name, row.image_url]);
            if (!products.has(key)) products.set(key, { item: row, quantities: new Map() });
            const quantities = products.get(key).quantities;
            const column = others.has(row.warehouse) ? null : row.warehouse;
            quantities.set(column, (quantities.get(column) || 0) + row.quantity);
        });
        const totals = columns.map(column => [...products.values()].reduce((sum, row) => sum + (row.quantities.get(column) || 0), 0));
        const grand = totals.reduce((a, b) => a + b, 0);
        const headers = head('Товар / артикул', 'Итого', 'col-product', 'stock-head-total--label') + head('Тотал', n(grand), 'col-total', 'tot-grand') + columns.map((column, index) => head(column ?? 'Другие склады', n(totals[index]), 'col-warehouse' + (column === null ? ' col-other' : ''), 'tot-warehouse')).join('');
        const body = [...products.values()].map(row => `<tr>${product(row.item)}<td class="col-total">${n([...row.quantities.values()].reduce((a, b) => a + b, 0))}</td>${columns.map(column => `<td class="col-warehouse${column === null ? ' col-other' : ''}">${row.quantities.has(column) ? n(row.quantities.get(column)) : '—'}</td>`).join('')}</tr>`).join('');
        return `<div class="warehouse-table-block"><div class="warehouse-summary"><span><small>Товаров</small><strong>${n(products.size)}</strong></span><span><small>Складов</small><strong>${n(warehouseTotals.size)}</strong></span><span><small>Единиц</small><strong>${n(grand)}</strong></span>${others.size ? `<span class="warehouse-summary-note">Показан топ-${top}; остальные склады собраны отдельно</span>` : ''}</div><div class="table-wrap table-wrap--scroll-10"><table class="data-table data-table--warehouses${columns.length > 14 ? ' is-dense-2' : columns.length > 7 ? ' is-dense' : ''}" style="--warehouse-min-width:${460 + columns.length * 106}px"><thead><tr>${headers}</tr></thead><tbody>${body}</tbody></table></div></div>`;
    }

    async function warehouses(context, url) {
        const data = await ui.request(url.pathname + '/data' + url.search);
        const trash = data.trash.map(row => `<tr class="${row.checked ? 'is-checked' : ''}">${product(row)}<td data-label="Склад">${e(row.warehouse)}</td><td data-label="Количество" class="trash-qty${row.quantity < 0 ? ' trash-qty--surplus' : ''}">${n(row.quantity)}</td><td data-label="Проконтролировано"><label class="trash-check"><input type="checkbox" class="trash-checkbox" data-article="${e(row.article)}" data-warehouse="${e(row.warehouse)}"${row.checked ? ' checked' : ''}${data.can_edit ? '' : ' disabled'}></label></td></tr>`).join('');
        const trashHeaders = head('Товар / артикул', 'Итого', 'col-product') + head('Склад', '', '') + head('Количество', n(data.trash.reduce((sum, row) => sum + row.quantity, 0)), '', 'tot-grand') + head('Проконтролировано', '', '');
        return { title: 'CheckStock — Склады — ' + name(context, data.store_slug), section: 'stock', contentClass: 'content--warehouses', values: { slug: e(data.store_slug), store_name: e(name(context, data.store_slug)), marketplace: e(data.marketplace), fbo_table: warehouseTable(data.fbo, 'Нет остатков на складах маркетплейса', context.warehouse_display_limit), fbs_table: warehouseTable(data.fbs, 'Нет остатков FBS'), ff_table: warehouseTable(data.fulfillment, 'Нет остатков на фулфилментах'), trash_table: `<table class="data-table data-table--trash" data-table-filter><thead><tr>${trashHeaders}</tr></thead><tbody>${trash || empty(4, 'Мусорка пуста — потерянного товара по этому маркетплейсу нет')}</tbody></table>` } };
    }

    async function totals(context, url) {
        const data = await ui.request('/stock/total/data' + url.search);
        const rows = data.rows.map(row => `<tr data-store="${e(row.store_slug)}" data-search-aliases="${e([...row.articles, ...row.barcodes].join(' '))}" data-grand-total="${row.quantities[data.value_keys[0]]}" data-purchase-price="${e(row.purchase_price ?? '')}"><td>${copy(row.article, 'Артикул')}</td><td>${copy(row.barcode, 'Баркод')}</td><td title="${e(row.name || row.article)}">${e(row.name || row.article)}</td><td data-filter-value="${e(row.purchase_price ?? '')}">${money(row.purchase_price)}</td><td class="stock-total-locations">${e(row.locations.map(target => name(context, target.store_slug) + ' ' + ({ 'YANDEX MARKET': 'ЯМ' }[target.marketplace] || target.marketplace)).join(', '))}</td>${data.value_keys.map(key => `<td data-filter-value="${row.quantities[key]}">${n(row.quantities[key])}</td>`).join('')}</tr>`).join('');
        const totalCells = data.value_keys.map((key, index) => `<th data-total-column="${index + 5}">${n(data.totals[key])}</th>`).join('');
        const costCells = data.value_keys.map((key, index) => `<th data-cost-total-column="${index + 5}">${money(data.rows.reduce((sum, row) => sum + (row.purchase_price || 0) * row.quantities[key], 0))}</th>`).join('');
        const totalRows = `<tr class="totals-row"><th>ИТОГО</th><th></th><th data-total-positions>позиций: ${data.rows.length}</th><th></th><th></th>${totalCells}</tr><tr class="totals-row totals-row--cost"><th>ИТОГО В ЗЦ</th><th></th><th data-cost-total-positions>ЗЦ: ${data.rows.filter(row => row.purchase_price !== null).length} из ${data.rows.length} поз.</th><th></th><th></th>${costCells}</tr>`;
        return { title: 'CheckStock — Сводные остатки', section: 'stock', contentClass: 'content--stock-total', values: { store_options: storeOptions(context, data.store), download_href: link('/stock/total.xlsx', { store: data.store }), rows: rows || empty(24, 'Нет товаров с остатками'), total_row: totalRows } };
    }

    function endpoint(fulfillment, marketplace, fallback) {
        return `<span class="ops-endpoint"><strong>${e(fulfillment || fallback)}</strong>${marketplace ? `<small>${e(marketplace)}</small>` : ''}</span>`;
    }

    async function history(context, url) {
        const data = await ui.request(url.pathname + '/data' + url.search);
        const counts = { ...data.counts };
        counts[''] = Object.values(data.counts).reduce((a, b) => a + b, 0);
        counts.transfer = ['transfer', 'transfer_dispatch', 'transfer_receive', 'transfer_receive_revert', 'transfer_cancel'].reduce((sum, key) => sum + (data.counts[key] || 0), 0);
        counts.shipment = ['shipment', 'fbo_dispatch', 'fbo_receive', 'fbo_receive_revert', 'fbo_cancel'].reduce((sum, key) => sum + (data.counts[key] || 0), 0);
        const rows = data.operations.map(op => {
            const kind = Object.hasOwn(operations, op.kind) ? op.kind : 'other';
            const detail = [op.note, op.source_name || ({ file: 'файл', sheet: 'Google Таблица', manual: 'ручной ввод' }[op.source_type] || '')].filter(Boolean).join(' · ') || 'Без примечания';
            return `<tr class="ops-row ops-row--${kind}"><td data-label="Когда"><time class="ops-time">${e(ui.date(op.created_at))}</time></td><td data-label="Операция"><span class="ops-kind ops-kind--${kind}"><i aria-hidden="true"></i>${e(operations[op.kind] || op.kind)}</span></td><td data-label="Маршрут"><span class="ops-route">${endpoint(op.from_fulfillment, op.from_marketplace, kind === 'delivery' ? 'Поставка' : kind === 'manual_add' ? 'Ручной ввод' : 'Не указано')}<span class="ops-route-arrow" aria-hidden="true">→</span>${endpoint(op.to_fulfillment, op.to_marketplace, kind === 'shipment' ? 'Отгрузка' : kind === 'fbs_transfer' ? 'FBS' : kind === 'trash' ? 'Мусорка' : 'Не указано')}</span></td><td data-label="Объём"><span class="ops-volume"><strong class="ops-volume-value${op.units < 0 ? ' ops-volume-value--negative' : ''}">${n(op.units)} ед.</strong><small>${n(op.positions)} поз.</small></span></td><td data-label="Сотрудник"><span class="ops-person"><strong>${e(op.user_name === 'marketplace_api' ? 'Маркетплейс' : op.user_name)}</strong><small title="${e(detail)}">${e(detail)}</small></span></td><td data-label="Выгрузка"><a class="ops-download" href="/admin/operations/${op.id}/xlsx">Excel</a></td></tr>`;
        }).join('');
        const pages = Math.max(1, Math.ceil(data.summary.total / data.page_size));
        const pageLink = (number, label) => `<a class="ops-page-link" href="${link(url.pathname, { kind: data.kind, page: number })}">${label}</a>`;
        const visiblePages = new Set(pages <= 7 ? Array.from({ length: pages }, (_, index) => index + 1) : [1, pages, data.page - 1, data.page, data.page + 1]);
        if (pages > 7 && data.page <= 3) for (let number = 1; number <= 5; number++) visiblePages.add(number);
        if (pages > 7 && data.page >= pages - 2) for (let number = pages - 4; number <= pages; number++) visiblePages.add(number);
        let previousPage = 0;
        const pageNumbers = [...visiblePages].filter(number => number >= 1 && number <= pages).sort((a, b) => a - b).map(number => {
            const gap = previousPage && number > previousPage + 1 ? '<span class="ops-page-gap">…</span>' : '';
            previousPage = number;
            return gap + (number === data.page ? `<span class="ops-page-link" aria-current="page">${number}</span>` : pageLink(number, number));
        }).join('');
        const pagination = `<nav class="ops-pagination" aria-label="Страницы истории операций"><span class="ops-pagination-range">Записи ${data.summary.total ? (data.page - 1) * data.page_size + 1 : 0}–${Math.min(data.page * data.page_size, data.summary.total)} из ${n(data.summary.total)}</span><div class="ops-pagination-navigation"><span class="ops-pagination-position">Страница ${data.page} из ${pages}</span><div class="ops-pagination-controls">${data.page > 1 ? pageLink(data.page - 1, '←') : ''}${pages > 1 ? pageNumbers : ''}${data.page < pages ? pageLink(data.page + 1, '→') : ''}</div></div></nav>`;
        return { title: 'CheckStock — История операций', section: 'stock_operations', contentClass: 'content--operations', values: { slug: e(data.store_slug), store_name: e(name(context, data.store_slug)), kind: e(data.kind), kind_tabs: filters.map(([key, label]) => `<a class="ops-filter${key === data.kind ? ' active' : ''}" href="${link(url.pathname, { kind: key })}">${label}<span class="ops-filter-count">${n(counts[key])}</span></a>`).join(''), summary: `<div class="ops-summary" role="list">${metric('Операций', n(data.summary.total))}${metric('Товарных позиций', n(data.summary.positions))}${metric('Движение, ед.', n(data.summary.units))}${metric('Сотрудников', n(data.summary.employees))}</div>`, rows: rows || empty(6, 'Движений пока не было'), pagination } };
    }

    function costTable(headers, rows, cls) {
        return `<div class="cost-table-wrap table-wrap"><table class="${cls} data-table"><thead><tr>${headers.map(label => `<th>${e(label)}</th>`).join('')}</tr></thead><tbody>${rows.join('') || empty(headers.length, 'За выбранный период данных нет')}</tbody></table></div>`;
    }

    async function costs(context, url) {
        const data = await ui.request('/stock/cost-report/data' + url.search);
        const selected = views.some(([key]) => key === url.searchParams.get('view')) ? url.searchParams.get('view') : 'summary';
        const query = { date_from: data.date_from, date_to: data.date_to, store: url.searchParams.get('store') || '', marketplace: url.searchParams.get('marketplace') || '', view: selected };
        const costMetric = value => `<span class="cost-metric"><strong>${money(value.cost)}</strong><small>${n(value.units)} ед.</small></span>`;
        let detail;
        if (selected === 'summary') {
            const rows = data.summary.map(row => `<tr><th>${e(name(context, row.store_slug))}</th><td>${e(row.marketplace)}</td>${['deliveries', 'moved_out', 'moved_in', 'shipped'].map(key => '<td>' + costMetric(row[key]) + '</td>').join('')}<td>${row.fbs_formula?.available ? `<span class="cost-metric"><strong>${n(row.fbs_sales.units)} ед.</strong><span class="formula-note">${n(row.fbs_formula.start_units)} + ${n(row.fbs_formula.moved_units)} − ${n(row.fbs_formula.end_units)}</span></span>` : '<span class="snapshot-wait">Снимки границ ещё не накоплены</span>'}</td><td>${row.fbs_formula?.available ? money(row.fbs_sales.cost) : '—'}</td><td>${n(row.fbs_actual_sales.units)} ед.</td></tr>`);
            detail = costTable(['Магазин', 'Маркетплейс', 'Поставки', 'Со стока', 'На сток', 'Отгружено наружу', 'Продано FBS по формуле', 'ЗЦ продаж FBS', 'По данным маркетплейса'], rows, 'cost-summary-table');
        } else if (selected === 'fbs_sales') {
            const actual = new Map(data.fbs_actual_sales.map(row => [JSON.stringify([row.store_slug, row.marketplace, row.article]), row.quantity]));
            detail = costTable(['Магазин', 'Маркетплейс', 'Товар', 'Продано FBS', 'ЗЦ/ед.', 'ЗЦ продаж', 'Начало + на FBS − конец', 'По данным маркетплейса'], data.fbs_sales.map(row => `<tr><td>${e(name(context, row.store_slug))}</td><td>${e(row.marketplace)}</td>${product(row)}<td>${n(row.quantity)} ед.</td><td>${money(row.purchase_price)}</td><td>${row.purchase_cost == null ? 'нет ЗЦ' : money(row.purchase_cost)}</td><td>${n(row.start_quantity)} + ${n(row.moved_quantity)} − ${n(row.end_quantity)}</td><td>${actual.has(JSON.stringify([row.store_slug, row.marketplace, row.article])) ? n(actual.get(JSON.stringify([row.store_slug, row.marketplace, row.article]))) : '—'}</td></tr>`), 'cost-sales-table');
        } else {
            const select = op => selected === 'deliveries' ? ['delivery', 'manual_add'].includes(op.kind) : selected === 'transfers' ? op.kind.startsWith('transfer') : selected === 'fbs_transfers' ? op.kind === 'fbs_transfer' : op.kind === 'shipment' || op.kind.startsWith('fbo_');
            detail = costTable(['Когда', 'Магазин', 'Маркетплейс', 'Операция', 'Маршрут', 'Количество', 'ЗЦ', 'Примечание', 'Учёт отгрузки'], data.operations.filter(select).map(op => `<tr><td>${e(ui.date(op.created_at))}</td><td>${e(name(context, op.store_slug))}</td><td>${e(op.from_marketplace && op.to_marketplace && op.from_marketplace !== op.to_marketplace ? op.from_marketplace + ' → ' + op.to_marketplace : op.to_marketplace || op.from_marketplace || '—')}</td><td>${e(operations[op.kind] || op.kind)}</td><td>${e(op.from_fulfillment || '—')} → ${e(op.to_fulfillment || '—')}</td><td data-filter-value="${op.units}"><strong>${n(op.units)} ед.</strong><small>${n(op.positions)} поз.</small></td><td>${money(op.purchase_cost)}</td><td>${e(op.note || '—')}</td><td>${op.kind === 'shipment' && data.can_edit ? `<form class="fbs-classifier" method="post" action="/stock/cost-report/operations/${op.id}/fbs-transfer">${Object.entries(query).map(([key, value]) => `<input type="hidden" name="${e(key)}" value="${e(value)}">`).join('')}<input type="hidden" name="is_fbs_transfer" value="${op.is_fbs_transfer ? 0 : 1}"><button class="${op.is_fbs_transfer ? 'is-active' : ''}" type="submit">${op.is_fbs_transfer ? 'На FBS' : 'Отгрузка наружу'}</button></form>` : '—'}</td></tr>`), 'cost-operation-table');
        }
        return { title: 'CheckStock — Движение и ЗЦ', section: 'stock_cost_report', contentClass: 'content--stock-cost-report', values: { date_from: e(data.date_from), date_to: e(data.date_to), store_options: storeOptions(context, query.store), marketplace_options: option('', 'Все площадки', query.marketplace) + markets(context).map(mp => option(mp, mp, query.marketplace)).join(''), current_week_url: link(url.pathname, { ...query, date_from: data.current_from, date_to: data.current_to }), previous_week_url: link(url.pathname, { ...query, date_from: data.previous_from, date_to: data.previous_to }), export_url: link('/stock/cost-report.xlsx', query), export_hidden: '', tabs: views.map(([key, label]) => `<a class="${selected === key ? 'is-active' : ''}" href="${link(url.pathname, { ...query, view: key })}">${label}</a>`).join(''), detail } };
    }

    async function supplies(context) {
        const data = await ui.request('/stock/supplies/data');
        return { title: 'CheckStock — Поставки', section: 'stock_supplies', contentClass: 'content--stock-supplies', values: { wb_store_options: storeOptions({ stores: context.stores.filter(store => store.marketplaces.includes('WB')) }, '', false), manual_store_options: storeOptions(context, '', false), wb_date_min: e(data.min_date), wb_date_max: e(data.max_date), wb_date_from: e(data.default_from), wb_date_to: e(data.default_to), can_edit_supply: '1' } };
    }

    async function inbound(context, url) {
        return { title: 'CheckStock — Поставки FBO', section: 'stock_inbound', values: { store_options: storeOptions(context, url.searchParams.get('store'), false), marketplace_options: markets(context).map(mp => option(mp, marketNames[mp] || mp, url.searchParams.get('mp'))).join(''), scopes: e(JSON.stringify(context.stores.flatMap(store => store.marketplaces.map(mp => [store.slug, mp])))), refresh_hidden: '', schedule_text: e(context.inbound_schedule) } };
    }

    function register(matches, prepare) {
        window.CheckStockPage.register(matches, async (context, url) => ({
            ...await prepare(context, url), showAlerts: false,
        }));
    }

    const routes = [['/stock', overview], ['/stock/total', totals], ['/stock/cost-report', costs], ['/stock/supplies', supplies], ['/stock/inbound', inbound]];
    routes.forEach(([path, prepare]) => register(url => url.pathname === path, prepare));
    register(url => /^\/stock\/[^/]+\/warehouses$/.test(url.pathname), warehouses);
    register(url => /^\/stock\/[^/]+\/operations$/.test(url.pathname), history);
    register(url => /^\/stock\/[^/]+$/.test(url.pathname), stock);
})();
