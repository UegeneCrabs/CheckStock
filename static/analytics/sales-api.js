(function () {
    'use strict';
    const root = document.getElementById('sales-api');
    if (!root) return;
    const M = window.CheckStockSalesApi;
    const config = JSON.parse(document.getElementById('sales-api-config').textContent);
    const $ = id => root.querySelector('#' + id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
    const fmt = value => value == null ? '—' : Number(value).toLocaleString('ru-RU');
    const shortDate = day => new Date(day + 'T12:00:00').toLocaleDateString('ru-RU', {day: '2-digit', month: '2-digit'});
    const weekday = day => new Date(day + 'T12:00:00').toLocaleDateString('ru-RU', {weekday: 'short'});
    const addDays = (day, count) => {
        const date = new Date(day + 'T12:00:00Z');
        date.setUTCDate(date.getUTCDate() + count);
        return date.toISOString().slice(0, 10);
    };
    const periodLabel = () => payload.date_from === payload.date_to ? shortDate(payload.date_to) + '.' + payload.date_to.slice(0, 4)
        : shortDate(payload.date_from) + '.' + payload.date_from.slice(0, 4) + ' – ' + shortDate(payload.date_to) + '.' + payload.date_to.slice(0, 4);
    const state = {query: '', segment: 'all', filters: {}, sort: M.turnover.index, direction: -1, page: 1, pageSize: 20};
    let payload, rows = [], columns = [], fields = [], loading = false, pending, focusBeforeDrawer, toastTimer;
    const preferenceKey = 'checkstock.sales-api.view.v1';
    const preferences = ['sheet-order', 'compact', 'heat', 'show-days'];
    try {
        const saved = JSON.parse(localStorage.getItem(preferenceKey) || '{}');
        preferences.forEach(id => { if (typeof saved[id] === 'boolean') $(id).checked = saved[id]; });
    } catch { /* View preferences are optional. */ }
    function savePreferences() {
        try { localStorage.setItem(preferenceKey, JSON.stringify(Object.fromEntries(preferences.map(id => [id, $(id).checked])))); } catch { /* Storage can be disabled. */ }
    }
    function toast(message) {
        $('toast').textContent = message;
        $('toast').hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
    }
    function setPanel(id, open) {
        $(id + '-panel').hidden = !open;
        $(id + '-toggle').setAttribute('aria-expanded', String(open));
    }
    function buildColumns() {
        if (!payload) return;
        fields = [...M.columns(payload.dates, true), M.combined];
        columns = M.columns(payload.dates, $('sheet-order').checked, $('show-days').checked);
        $('sales-table').classList.toggle('compact', $('compact').checked);
        $('sales-table').style.width = columns.reduce((sum, column) => sum + column.width, 0) + 'px';
        $('cols').innerHTML = columns.map(column => `<col style="width:${column.width}px">`).join('');
        const identities = $('sheet-order').checked ? 5 : 3;
        $('head').innerHTML = `<tr class="group"><th class="frozen">${$('sheet-order').checked ? 'Проект' : 'Товар'}</th><th colspan="${identities - 1}">Данные товара</th><th class="total-group" title="Сумма заказов за сегодня по московскому времени, без вычета отмен">Сегодня · ${shortDate(payload.turnover_day)}</th><th colspan="2" class="total-group">За выбранный период</th>${$('show-days').checked ? `<th colspan="${payload.dates.length}" class="sales-group">Заказы по дням · ${periodLabel()}</th>` : ''}</tr><tr class="heads">${columns.map((column, i) => {
            const weekend = column.day != null && [0, 6].includes(new Date(column.key + 'T12:00:00').getDay());
            return `<th class="${i === 0 ? 'frozen ' : ''}${weekend ? 'weekend ' : ''}${column.key === 'orders' || column.day === 0 ? 'section-start' : ''}" data-filter-column="${column.index}" ${column.numeric ? 'data-filter-type="number"' : ''} title="${esc(column.day != null ? column.key + ' · Заказы, шт.' : column.label)}">${column.day != null ? shortDate(column.key) + `<span class="day-week">${weekday(column.key)}</span>` : esc(column.label)}</th>`;
        }).join('')}</tr><tr class="totals"></tr>`;
        $('sales-table')._tfFilters = state.filters;
        window.CheckStockTableFilter.refresh($('sales-table'));
        $('head').querySelectorAll('[data-filter-column]').forEach(header => {
            header.querySelector('.tf-btn')?.classList.toggle('tf-btn--active', !!state.filters[header.dataset.filterColumn]);
        });
        savePreferences();
    }
    function photo(row) {
        const url = /^https?:\/\//i.test(row.image) || /^\/(?!\/)/.test(row.image) ? row.image : '';
        return `<span class="product-photo" aria-hidden="true">${url ? `<img loading="lazy" decoding="async" src="${esc(url)}" alt="">` : 'WB'}</span>`;
    }
    root.addEventListener('error', event => {
        if (event.target.matches?.('.product-photo img')) event.target.parentElement.textContent = 'WB';
    }, true);
    function cell(row, column, i) {
        const value = M.value(row, column), frozen = i === 0 ? ' frozen' : '';
        const name = `<button class="product-name" data-product="${esc(row.id)}" title="${esc(row.name)}">${esc(row.name)}</button>`;
        if (column.key === 'product') return `<td class="text${frozen}"><div class="product-cell">${photo(row)}<div class="product-description">${name}<div class="product-meta"><span>WB ${esc(row.article)}</span><span class="barcode" title="${esc(row.barcode)}">${esc(row.barcode)}</span></div></div></div></td>`;
        if (column.key === 'name') return `<td class="text${frozen}"><div class="product-cell">${photo(row)}<div class="product-description">${name}</div></div></td>`;
        if (column.key === 'category') return `<td class="text${frozen}"><span class="category-label" title="${esc(value)}">${esc(value) || '—'}</span></td>`;
        if (!column.numeric) return `<td class="text muted${frozen}" title="${esc(value)}">${esc(value) || '—'}</td>`;
        if (column.day == null) return `<td class="${column.key === 'orders' ? 'section-start' : ''}"><span class="${value === 0 ? 'zero' : column.key === 'cancels' ? 'cancel-main' : 'number-main'}">${fmt(value)}</span>${value != null && ['orders','cancels'].includes(column.key) && !row.complete ? '<small class="partial" title="Сумма только по загруженным дням">неполные данные</small>' : ''}</td>`;
        const max = Math.max(1, ...row.days.filter(value => value != null));
        const alpha = value > 0 ? 0.035 + value / max * 0.11 : 0;
        return `<td class="daily ${!value ? 'zero' : ''} ${$('heat').checked ? 'heat' : ''} ${column.day === 0 ? 'section-start' : ''}" style="--heat:rgba(57,145,104,${alpha})"><span class="metric-cell">${fmt(value)}</span></td>`;
    }
    function renderPage() {
        if (!payload) return;
        const visible = M.filteredRows(rows, state, fields);
        const pages = Math.max(1, Math.ceil(visible.length / state.pageSize));
        state.page = Math.min(Math.max(1, state.page), pages);
        const start = (state.page - 1) * state.pageSize;
        $('head').querySelector('.totals').innerHTML = columns.map((column, i) => {
            if (i === 0) return `<th class="frozen">Итого<small>${fmt(visible.length)} товаров</small></th>`;
            if (!column.numeric) return '<th></th>';
            const total = M.total(visible, column);
            return `<th title="Сумма по всем отфильтрованным товарам${total.partial ? '; данные загружены не полностью' : ''}">${fmt(total.value)}${total.partial && total.value != null ? '<small>неполные данные</small>' : ''}</th>`;
        }).join('');
        $('head').querySelectorAll('[data-filter-column]').forEach(header => header.setAttribute('aria-sort', Number(header.dataset.filterColumn) === state.sort ? state.direction === 1 ? 'ascending' : 'descending' : 'none'));
        $('rows').innerHTML = visible.slice(start, start + state.pageSize).map(row => `<tr>${columns.map((column, i) => cell(row, column, i)).join('')}</tr>`).join('');
        $('count').textContent = fmt(visible.length);
        $('pagination-label').textContent = visible.length ? `${fmt(start + 1)}–${fmt(Math.min(start + state.pageSize, visible.length))} из ${fmt(visible.length)} товаров` : '0 товаров';
        $('page-number').textContent = `${state.page} / ${pages}`;
        $('prev-page').disabled = loading || state.page <= 1;
        $('next-page').disabled = loading || state.page >= pages;
        $('export').disabled = loading || !visible.length;
        $('empty').hidden = !!visible.length || loading;
        $('reset').hidden = !state.query && state.segment === 'all' && !Object.keys(state.filters).length && state.sort === M.turnover.index && state.direction === -1;
        root.querySelectorAll('[data-segment]').forEach(button => {
            button.classList.toggle('active', button.dataset.segment === state.segment);
            button.setAttribute('aria-pressed', String(button.dataset.segment === state.segment));
        });
    }
    $('sales-table')._tfAdapter = {
        values: idx => {
            const column = fields.find(column => column.index === Number(idx));
            return column ? M.externalRows(rows, state).map(row => M.filterValue(row, column)) : [];
        },
        filter: filters => { state.filters = filters || {}; change(); },
        sort: (idx, direction) => { state.sort = Number(idx); state.direction = direction === 'desc' ? -1 : 1; change(); },
    };
    function change() { state.page = 1; renderPage(); $('table-scroll').scrollTop = 0; }
    async function loadPeriod(start, end) {
        pending?.abort();
        const controller = new AbortController();
        pending = controller;
        loading = true;
        $('table-scroll').setAttribute('aria-busy', 'true');
        $('export').disabled = true;
        $('load-status').hidden = false;
        $('load-status').textContent = 'Загрузка заказов из базы…';
        closeDrawer();
        try {
            const query = start && end ? '?' + new URLSearchParams({date_from: start, date_to: end}) : '';
            const response = await fetch('/api/analytics/sales-api' + query, {signal: controller.signal, headers: {'Accept': 'application/json'}});
            const body = await response.json();
            if (!response.ok || !body.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось загрузить заказы');
            if (controller !== pending) return;
            payload = body;
            rows = body.rows;
            state.page = 1;
            // Daily column positions change with the period, so reset all column filters and sorting.
            state.filters = {};
            state.sort = M.turnover.index; state.direction = -1;
            $('period-label').textContent = periodLabel();
            $('day-count').textContent = `${body.dates.length} дн.`;
            $('start').value = body.date_from;
            $('end').value = body.date_to;
            $('start').max = $('end').max = body.today;
            $('start').min = $('end').min = '2000-01-01';
            $('full-period').disabled = !body.available_from;
            $('load-status').textContent = '';
            $('load-status').hidden = true;
            loading = false;
            buildColumns();
        } catch (error) {
            if (error.name === 'AbortError') return;
            $('load-status').hidden = false;
            $('load-status').textContent = error.message + (payload ? '. На экране остался предыдущий период. ' : '. ');
            const retry = document.createElement('button');
            retry.className = 'reset';
            retry.textContent = 'Повторить';
            retry.onclick = () => loadPeriod(start, end);
            $('load-status').append(retry);
            if (!payload) $('period-label').textContent = 'Выбрать период';
        } finally {
            if (controller === pending) {
                loading = false;
                $('table-scroll').setAttribute('aria-busy', 'false');
                renderPage();
            }
        }
    }
    function applyPeriod() {
        const start = $('start').value, end = $('end').value;
        const length = (new Date(end + 'T12:00:00Z') - new Date(start + 'T12:00:00Z')) / 86400000 + 1;
        if (!start || !end || !Number.isFinite(length) || length < 1 || start < '2000-01-01' || end > config.today || length > config.maxDays) {
            $('date-error').textContent = `Выберите от 1 до ${config.maxDays} дней, не позднее сегодняшнего дня.`;
            $('date-error').hidden = false;
            return;
        }
        $('date-error').hidden = true;
        setPanel('period', false);
        loadPeriod(start, end);
    }
    function closeDrawer() {
        if (!$('drawer').hidden) {
            $('drawer').hidden = $('overlay').hidden = true;
            root.querySelector('.sales-main').inert = false;
            focusBeforeDrawer?.focus();
        }
    }
    function openDrawer(id) {
        const row = rows.find(row => row.id === id);
        if (!row || loading) return;
        focusBeforeDrawer = document.activeElement;
        const best = Math.max(1, ...row.days.filter(value => value != null));
        $('drawer-body').innerHTML = `<h3 class="drawer-name">${esc(row.name)}</h3><div class="drawer-meta">${esc(row.category) || 'Без категории'}<br>Артикул WB: ${esc(row.article)}<br>Баркод: ${esc(row.barcode) || '—'}<br>Проект: ${esc(row.project)}</div><div class="detail-grid"><div><small>Заказы, шт.</small><strong class="number-main">${fmt(row.orders)}</strong></div><div><small>Отмены, шт.</small><strong>${fmt(row.cancels)}</strong></div></div><p class="coverage">Загружено ${row.known_days} из ${payload.dates.length} дней. ${row.complete ? '' : 'Итоги учитывают только известные значения.'}</p><h3>${periodLabel()}</h3><div class="chart-scroll" tabindex="0" aria-label="Заказы по дням с прокруткой"><div class="chart" style="min-width:${Math.max(370, payload.dates.length * 49)}px">${row.days.map((value, i) => `<div class="bar ${value === best ? 'peak' : ''}" style="height:${value == null ? 2 : Math.max(2, value / best * 100)}%" title="${payload.dates[i]}: ${fmt(value)}"><b>${fmt(value)}</b><small>${shortDate(payload.dates[i])}</small></div>`).join('')}</div></div><h3>Детализация периода</h3><table class="detail-list"><thead><tr><th>Дата</th><th>Заказы, шт.</th><th>Отмены, шт.</th></tr></thead><tbody>${payload.dates.map((day, i) => `<tr><td>${shortDate(day)}.${day.slice(0, 4)} <span class="muted">${weekday(day)}</span></td><td>${fmt(row.days[i])}</td><td>${fmt(row.cancellations[i])}</td></tr>`).join('')}</tbody></table>`;
        $('drawer').hidden = $('overlay').hidden = false;
        root.querySelector('.sales-main').inert = true;
        $('close-drawer').focus();
    }
    $('search').addEventListener('input', event => { state.query = event.target.value; change(); });
    $('page-size').addEventListener('change', event => { state.pageSize = Number(event.target.value); change(); });
    root.querySelectorAll('[data-segment]').forEach(button => button.addEventListener('click', () => { state.segment = button.dataset.segment; change(); }));
    $('reset').addEventListener('click', () => {
        state.query = '';
        state.segment = 'all'; state.filters = {}; state.sort = M.turnover.index; state.direction = -1; state.page = 1;
        $('search').value = '';
        buildColumns();
    });
    preferences.forEach(id => $(id).addEventListener('change', buildColumns));
    ['view', 'period'].forEach(id => $(id + '-toggle').addEventListener('click', () => {
        const open = $(id + '-panel').hidden;
        setPanel(id === 'view' ? 'period' : 'view', false);
        setPanel(id, open);
    }));
    $('view-done').addEventListener('click', () => setPanel('view', false));
    $('apply-period').addEventListener('click', applyPeriod);
    $('latest-period').addEventListener('click', () => { setPanel('period', false); $('date-error').hidden = true; loadPeriod(); });
    $('full-period').addEventListener('click', () => {
        if (!payload?.available_from) return;
        const earliest = addDays(payload.available_to, 1 - config.maxDays);
        $('start').value = payload.available_from < earliest ? earliest : payload.available_from;
        $('end').value = payload.available_to;
        if (payload.available_from < earliest) toast(`Выбраны последние ${config.maxDays} загруженных дней`);
        applyPeriod();
    });
    $('prev-page').addEventListener('click', () => { state.page--; renderPage(); $('table-scroll').scrollTop = 0; });
    $('next-page').addEventListener('click', () => { state.page++; renderPage(); $('table-scroll').scrollTop = 0; });
    $('rows').addEventListener('click', event => { const button = event.target.closest('[data-product]'); if (button) openDrawer(button.dataset.product); });
    ['overlay', 'close-drawer', 'drawer-done'].forEach(id => $(id).addEventListener('click', closeDrawer));
    document.addEventListener('click', event => {
        if (!event.target.closest('.sales-api .view-wrap')) setPanel('view', false);
        if (!event.target.closest('.sales-api .period-wrap')) setPanel('period', false);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') { closeDrawer(); setPanel('view', false); setPanel('period', false); }
        if (event.key === 'Tab' && !$('drawer').hidden) {
            const first = $('close-drawer'), last = $('drawer-done');
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    $('export').addEventListener('click', () => {
        if (!payload || loading) return;
        const visible = M.filteredRows(rows, state, fields), exportColumns = M.columns(payload.dates, true);
        const content = '\ufeff' + [exportColumns.map(column => column.label), ...visible.map(row => exportColumns.map(column => M.value(row, column)))].map(row => row.map(M.csvCell).join(';')).join('\r\n');
        const url = URL.createObjectURL(new Blob([content], {type: 'text/csv;charset=utf-8'})), link = document.createElement('a');
        link.href = url;
        link.download = `wb-orders-${payload.date_from}-${payload.date_to}.csv`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
        toast(`В CSV выгружено ${fmt(visible.length)} товаров`);
    });
    loadPeriod();
})();
