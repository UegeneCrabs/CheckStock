(function () {
    'use strict';
    const root = document.getElementById('analyzer');
    if (!root) return;
    const M = window.CheckStockAnalyzer;
    const config = JSON.parse(document.getElementById('analyzer-config').textContent);
    const $ = id => root.querySelector('#' + id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
    const num = (value, digits = 0) => value == null ? '—' : Number(value).toLocaleString('ru-RU', {maximumFractionDigits: digits});
    const money = value => value == null ? '—' : num(value, 2) + ' ₽';
    const pct = value => value == null ? '—' : num(value, 2) + '%';
    const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const shortDate = day => new Date(day + 'T12:00:00').toLocaleDateString('ru-RU', {day: '2-digit', month: '2-digit'});
    const dateTime = value => value ? new Date(value).toLocaleString('ru-RU', {timeZone: 'Europe/Moscow'}) : '—';
    const addDays = (day, count) => {
        const date = new Date(day + 'T12:00:00Z');
        date.setUTCDate(date.getUTCDate() + count);
        return date.toISOString().slice(0, 10);
    };
    const monday = day => addDays(day, -((new Date(day + 'T12:00:00Z').getUTCDay() + 6) % 7));
    let payload = null, rows = [], columns = [], loadedWeek = null, requestedWeek = monday(config.today);
    let pending = null, loading = false, selected = null, editingNoteId = null, noteSaving = false, toastTimer;
    const noteDrafts = new Map();
    const state = {query: '', project: '', manager: '', segment: 'all', filters: {}, sort: null, direction: 1, page: 1, pageSize: 20};
    const visibleGroups = new Set(Object.keys(M.groups));
    const preferenceKey = 'checkstock.analyzer.columns.v1';
    try {
        const saved = JSON.parse(localStorage.getItem(preferenceKey) || 'null');
        if (saved) {
            $('dense').checked = !!saved.dense;
            $('sheet-order').checked = !!saved.exact;
            if (Array.isArray(saved.groups) && saved.groups.length) {
                visibleGroups.clear();
                saved.groups.forEach(group => { if (M.groups[group]) visibleGroups.add(group); });
            }
        }
    } catch { /* Preferences are optional. */ }
    function savePreferences() {
        try { localStorage.setItem(preferenceKey, JSON.stringify({dense: $('dense').checked, exact: $('sheet-order').checked, groups: [...visibleGroups]})); } catch { /* Storage may be disabled. */ }
    }
    function toast(text) {
        $('toast').textContent = text;
        $('toast').hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
    }
    function label(column) {
        if (column.type === 'day' || column.type === 'note') return weekdays[column.idx] + '<br>' + shortDate(payload.week[column.idx]);
        if (column.metric) return column.label + '<br>' + shortDate(payload.traffic_days[column.idx]);
        if (['spend', 'yesterdayDrr'].includes(column.key)) return column.label.replace('вчера', shortDate(addDays(payload.anchor, -1)));
        return column.label;
    }
    function title(column) {
        if (column.metric === 'impressions' || column.metric === 'ctr' || column.key === 'totalCtr') return 'Показы и CTR рекламных кампаний WB. CTR = клики / показы × 100%.';
        if (column.metric === 'carts') return 'Добавления в корзину из сохранённой воронки WB.';
        if (column.key === 'stock') return 'Последний сохранённый остаток: FBO + FBS + ФФ. Если часть источников отсутствует, показана известная часть.';
        if (column.key === 'roi') return 'ROI по дневным расчётам юнит-экономики за выбранную неделю. Неполные данные отмечены в карточке.';
        if (['drr', 'yesterdayDrr'].includes(column.key)) return 'Расход рекламы / ожидаемый выкупленный оборот × 100%, как в юнит-экономике WB. Только дни с известными исходными значениями.';
        if (column.key === 'previousPrice') return 'Сумма заказов / количество заказов за ' + shortDate(payload.previous_from) + '–' + shortDate(payload.previous_to) + '.';
        if (column.key === 'coeff') return 'Коэффициент D временно равен 1 для всех товаров.';
        if (column.key === 'fact') return 'Сумма заказов за сегодня (' + shortDate(payload.turnover_day) + ') из последней сохранённой воронки WB. Включает все размеры артикула WB; в итогах учитывается один раз.';
        if (column.key === 'forecast') return 'Коэффициент D × ТО факт на данный момент за сегодня. Оборот всех размеров артикула WB учитывается в итогах один раз.';
        if (column.key === 'difference') return 'ТО цель на день − прогнозируемый ТО на день.';
        if (column.key === 'deviation') return '(ТО цель на день − прогнозируемый ТО на день) / ТО цель на день × 100%.';
        if (column.key === 'plan') return 'Средняя цена заказов предыдущей выбранной недели × цель по заказам на день.';
        if (column.type === 'goal' || ['goal', 'dayGoal'].includes(column.key)) return 'Последняя сохранённая цель из справочника юнит-экономики. Если дневная цель отсутствует, используется недельная / 7.';
        return column.label.replace(/<br>/g, ' ');
    }
    function format(value, column) {
        if (value == null) return '—';
        if (['roi', 'drr', 'percent', 'deviation'].includes(column.type)) return pct(value);
        if (['money', 'difference'].includes(column.type)) return money(value);
        return num(value, ['decimal', 'rating'].includes(column.type) ? 2 : 0);
    }
    function photo(row) {
        const url = /^https?:\/\//i.test(row.image) || /^\/(?!\/)/.test(row.image) ? row.image : '';
        return url ? `<img class="thumb" loading="lazy" src="${esc(url)}" alt="${esc(row.name)}">` : '<span class="thumb no-photo" aria-label="Нет фото">WB</span>';
    }
    function cell(row, column) {
        const value = M.value(row, column);
        if (column.key === 'product') return `<div class="product">${photo(row)}<div><button class="product-name" data-open="${esc(row.id)}" title="${esc(row.name)}">${esc(row.name)}</button><div class="product-meta"><span>WB</span><button data-copy="${esc(row.article)}" title="Копировать артикул">${esc(row.article)} ⧉</button></div><div class="product-barcode">${esc(row.barcode)}</div></div></div>`;
        if (column.type === 'photo') return photo(row);
        if (column.type === 'name') return `<button class="product-name" data-open="${esc(row.id)}">${esc(value)}</button>`;
        if (column.type === 'spacer') return '';
        if (column.type === 'person') return value ? `<span class="person"><i>${esc(value[0])}</i>${esc(value)}</span>` : '—';
        if (column.type === 'project') return `<span class="project"><i></i>${esc(value)}</span>`;
        if (column.type === 'code') return value ? `<span class="code ${value.toUpperCase() === 'NEW' ? 'new' : ''}">${esc(value)}</span>` : '—';
        if (column.type === 'text') return esc(value) || '—';
        if (column.type === 'goal') return `<div class="goal">${num(row.goal)}<span>/</span>${num(row.dayGoal, 1)} шт</div><span class="stock-status">${esc([row.stockStatus, row.stockEnds].filter(Boolean).join(' · '))}</span>`;
        if (column.type === 'note') return `<button class="cell-note ${value ? '' : 'empty'}" data-note="${esc(row.id)}" data-day="${column.idx}" aria-label="Записи ${weekdays[column.idx]}: ${esc(row.name)}"><span class="note-content">${esc(value || (config.canEdit ? '+ Добавить запись' : 'Нет записей'))}</span></button>`;
        if (value == null) return '<span class="empty-value">—</span>';
        if (column.type === 'stock') return `<strong>${num(value)}</strong><span class="cell-sub" title="Сток / среднесуточные заказы за ${shortDate(payload.stockPreviousFrom)}–${shortDate(payload.stockPreviousTo)}">${row.stockDays != null ? '≈ ' + num(row.stockDays, 1) + ' дней запаса' : row.stockOrdersComplete && row.stockDailyOrders === 0 ? 'Нет заказов за прошлую неделю' : 'Запас дней: нет данных'}</span>`;
        if (column.type === 'rating') return `<span class="rating">★</span> <strong>${num(value, 1)}</strong>`;
        if (column.type === 'day') return `<span class="cell-main">${num(value)}</span>${row.dayGoal > 0 ? `<span class="progress"><i style="width:${Math.min(value / row.dayGoal * 100, 100)}%"></i></span>` : ''}`;
        const partial = column.key === 'roi' && !row.coverage.roiComplete || column.key === 'drr' && row.coverage.drr < row.coverage.expected;
        return `<span class="cell-main ${column.type === 'roi' ? value < 0 ? 'negative' : 'positive' : ''}">${format(value, column)}</span>${partial ? '<span class="cell-sub">неполные данные</span>' : ''}`;
    }
    function buildColumns() {
        columns = M.displayColumns(visibleGroups, $('sheet-order').checked, innerWidth < 800 ? 220 : 290);
        root.classList.toggle('compact', $('dense').checked);
        $('table').style.width = columns.reduce((sum, column) => sum + column.width, 0) + 'px';
        $('cols').innerHTML = columns.map(column => `<col style="width:${column.width}px">`).join('');
        const groups = [];
        columns.forEach(column => {
            if (column.frozenLeft == null && groups.at(-1)?.frozenLeft == null && groups.at(-1)?.key === column.group) groups.at(-1).columns.push(column);
            else groups.push({key: column.group, columns: [column], frozenLeft: column.frozenLeft});
        });
        const groupHeaders = groups.map(group => {
            const pinned = group.frozenLeft != null;
            return `<th colspan="${group.columns.length}" data-section="${group.key}" class="tone-${group.key} ${pinned ? 'frozen' : ''}" ${pinned ? `style="left:${group.frozenLeft}px"` : ''}>${pinned ? group.columns[0].key === 'code' ? 'Код' : 'Товар' : group.key === 'product' ? 'Команда и проект' : M.groups[group.key]}</th>`;
        }).join('');
        $('thead').innerHTML = `<tr class="group">${groupHeaders}</tr><tr class="heads">${columns.map(column => `<th ${columnAttributes(column)} ${column.type === 'spacer' ? '' : `data-filter-column="${column.index}" ${M.numeric(column) ? 'data-filter-type="number"' : ''}`} title="${esc(title(column))}">${label(column)}</th>`).join('')}</tr><tr class="totals"></tr>`;
        root.querySelectorAll('[data-jump]').forEach(button => { button.hidden = !columns.some(column => column.group === button.dataset.jump); });
        // Stable column IDs keep filters attached to the right fields when groups move.
        $('table')._tfFilters = state.filters;
        window.CheckStockTableFilter.refresh($('table'));
        $('thead').querySelectorAll('[data-filter-column]').forEach(header => {
            header.querySelector('.tf-btn')?.classList.toggle('tf-btn--active', !!state.filters[header.dataset.filterColumn]);
        });
        savePreferences();
    }
    function columnAttributes(column, extra = '') {
        return `class="tone-${column.group} ${column.frozenLeft != null ? 'frozen ' : ''}${column.type === 'spacer' ? 'spacer ' : ''}${extra}" ${column.frozenLeft != null ? `style="left:${column.frozenLeft}px"` : ''}`;
    }
    function renderStoreSummary(filtered) {
        $('summary-caption').textContent = 'Товарооборот на ' + shortDate(payload.turnover_day) + ' · по фильтрам таблицы';
        $('store-summary').innerHTML = M.storeTurnover(filtered).map(store => `<tr><th scope="row">${esc(store.project)}</th>${['plan', 'fact', 'forecast', 'difference', 'deviation'].map(key => `<td title="${store.partial[key] ? 'Неполные данные: учтены только известные значения' : ''}">${key === 'deviation' ? pct(store[key]) : num(store[key], 0)}${store.partial[key] && store[key] != null ? '<sup>*</sup>' : ''}</td>`).join('')}</tr>`).join('') || '<tr><td colspan="6">Нет товаров по выбранным фильтрам</td></tr>';
    }
    function renderPage() {
        if (!payload) return;
        const filtered = M.tableRows(rows, state);
        const pages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
        state.page = Math.min(Math.max(1, state.page), pages);
        const start = (state.page - 1) * state.pageSize;
        $('thead').querySelector('.totals').innerHTML = columns.map((column, i) => {
            const value = M.summary(filtered, column);
            return `<th ${columnAttributes(column)} title="Итоги по всем отфильтрованным товарам; неизвестные значения не включены">${i === 0 ? 'Итого · ' + filtered.length : value == null ? '' : format(value, column)}</th>`;
        }).join('');
        $('tbody').innerHTML = filtered.slice(start, start + state.pageSize).map(row => `<tr data-row="${esc(row.id)}" class="${selected?.id === row.id ? 'selected' : ''}">${columns.map(column => `<td ${columnAttributes(column, ['product', 'person', 'name', 'text', 'note', 'goal'].includes(column.type) ? 'text' : ['project', 'code', 'photo', 'day'].includes(column.type) ? 'center' : '')}>${cell(row, column)}</td>`).join('')}</tr>`).join('');
        renderStoreSummary(filtered);
        $('count').textContent = num(filtered.length);
        $('pagination').textContent = filtered.length ? `Показано ${start + 1}–${Math.min(start + state.pageSize, filtered.length)} из ${filtered.length} товаров` : 'Показано 0 товаров';
        $('page-number').textContent = state.page + ' / ' + pages;
        $('page-prev').disabled = state.page === 1 || loading;
        $('page-next').disabled = state.page === pages || loading;
        $('empty').hidden = filtered.length > 0 || loading;
        $('export').disabled = loading || !filtered.length;
    }
    $('table')._tfAdapter = {
        values: idx => M.filter(rows, state).map(row => M.filterValue(row, [...M.fields, M.combined][idx])),
        filter: filters => { state.filters = filters || {}; state.page = 1; renderPage(); },
        sort: (idx, direction) => { state.sort = Number(idx); state.direction = direction === 'desc' ? -1 : 1; state.page = 1; renderPage(); },
    };
    async function loadWeek(week) {
        pending?.abort();
        const controller = new AbortController();
        pending = controller;
        requestedWeek = week;
        loading = true;
        $('table-scroll').setAttribute('aria-busy', 'true');
        $('load-status').hidden = false;
        $('load-status').textContent = 'Загрузка данных за ' + shortDate(week) + '–' + shortDate(addDays(week, 6)) + '…';
        $('export').disabled = true;
        closeDrawer();
        try {
            const response = await fetch('/api/analytics/analyzer?week=' + encodeURIComponent(week), {signal: controller.signal, headers: {'Accept': 'application/json'}});
            const body = await response.json();
            if (!response.ok || !body.ok) throw new Error(body.detail || body.error || 'Не удалось загрузить данные');
            if (controller !== pending) return;
            payload = body;
            rows = body.rows;
            loadedWeek = body.week[0];
            state.page = 1;
            state.filters = {};
            $('week-label').textContent = shortDate(body.week[0]) + '–' + shortDate(body.week[6]) + ' ' + body.week[6].slice(0, 4);
            $('week-caption').textContent = loadedWeek === monday(body.today) ? 'Текущая неделя' : 'Выбранная неделя';
            $('load-status').hidden = true;
            loading = false;
            buildColumns();
        } catch (error) {
            if (error.name === 'AbortError') return;
            $('load-status').textContent = `${error.message}. ${payload ? 'На экране осталась ранее загруженная неделя. ' : ''}`;
            const retry = document.createElement('button');
            retry.className = 'button';
            retry.textContent = 'Повторить';
            retry.onclick = () => loadWeek(week);
            $('load-status').append(retry);
            requestedWeek = loadedWeek || monday(config.today);
        } finally {
            if (controller === pending) {
                loading = false;
                $('table-scroll').setAttribute('aria-busy', 'false');
                $('next-week').disabled = requestedWeek >= monday(config.today);
                renderPage();
            }
        }
    }
    function closeDrawer() {
        rememberDraft();
        $('drawer').hidden = true;
        selected = null;
        $('tbody').querySelectorAll('.selected').forEach(row => row.classList.remove('selected'));
    }
    function openDrawer(id, day) {
        const row = rows.find(item => item.id === id);
        if (!row || loading) return;
        rememberDraft();
        selected = row;
        const barsMax = Math.max(1, ...row.week.filter(value => value != null));
        $('drawer-body').innerHTML = `<div class="drawer-product">${photo(row)}<div><h2>${esc(row.name)}</h2><p>${esc(row.project)} · ${esc(row.article)}<br>${esc(row.barcode)}</p></div></div>
            <details class="product-metrics"><summary>Показатели и заказы за неделю</summary>
            <div class="detail-metrics"><div><small>Сток${row.stockPartial ? ' · известная часть' : ''}</small><strong>${num(row.stock)}</strong><small>${row.stockDays != null ? '≈ ' + num(row.stockDays, 1) + ' дней запаса' : 'Запас дней не рассчитан'}</small></div><div><small>Заказов в сутки · ${shortDate(payload.stockPreviousFrom)}–${shortDate(payload.stockPreviousTo)}</small><strong>${num(row.stockDailyOrders, 1)}</strong></div><div><small>РОИ · накопительный итог</small><strong>${pct(row.roi)}</strong></div><div><small>ДРР · накопительный итог</small><strong>${pct(row.drr)}</strong></div></div>
            <p class="data-coverage">Заказы: ${row.coverage.orders}/${row.coverage.expected} дней · Реклама: ${row.coverage.advertising}/${row.coverage.expected} дней.<br>${row.coverage.roiComplete ? 'Данные ROI полные.' : 'ROI рассчитан по доступным данным.'}</p>
            ${!row.coverage.roiComplete ? `<details class="coverage-details"><summary>Каких данных не хватает для ROI</summary>${row.coverage.roiMessages.map(message => `<p>${esc(message)}</p>`).join('') || '<p>Недостаточно исходных данных.</p>'}</details>` : ''}
            <h3>Заказы по дням</h3><div class="days-bars">${row.week.map((value, idx) => `<div class="bar ${payload.week[idx] === payload.today ? 'current' : ''}" style="height:${value == null ? 3 : Math.max(3, value / barsMax * 80)}px"><b>${num(value)}</b><small>${weekdays[idx]}</small></div>`).join('')}</div>
            </details>
            <h3>Комментарии · ${shortDate(payload.week[0])}–${shortDate(payload.today)}</h3>
            <div id="note-history" class="note-history"></div>
            ${config.canEdit ? `<div class="note-editor-heading"><label for="note-input" id="note-editor-label">Комментарий на сегодня · ${shortDate(payload.today)}</label><button type="button" class="note-edit" id="new-note">Новая запись</button></div><textarea id="note-input" maxlength="4000" placeholder="Что сделали, что проверить, план действий"></textarea>` : '<p>Доступ только для просмотра записей.</p>'}
            <details class="source-details"><summary>Сохранённые данные</summary>${Object.entries({stock: 'Каталог', prices: 'Цена', reference: 'Цели и справочник', orders: 'Заказы', advertising: 'Реклама'}).map(([key, name]) => `<p>${name}: ${dateTime(row.updated[key])}</p>`).join('')}<p>ТО факт за ${shortDate(payload.turnover_day)}: ${money(row.fact)}. Коэф D = 1.</p></details>`;
        $('save-note').hidden = !config.canEdit;
        $('save-note').disabled = noteSaving;
        $('drawer').hidden = false;
        $('drawer-body').scrollTop = 0;
        renderNotes();
        if (config.canEdit) {
            const draft = noteDrafts.get(row.id), latest = (row.noteHistory || []).filter(note => note.can_edit).at(-1);
            setNoteEditor(draft?.id ?? (draft ? null : latest?.id), draft?.text ?? latest?.note ?? '');
            $('note-input').disabled = noteSaving;
            $('new-note').disabled = noteSaving;
            $('note-input').oninput = rememberDraft;
            $('new-note').onclick = () => { setNoteEditor(null, ''); rememberDraft(); $('note-input').focus(); };
        }
        $('tbody').querySelectorAll('[data-row]').forEach(node => node.classList.toggle('selected', node.dataset.row === row.id));
        if (day != null) $('note-history').querySelector(`[data-date="${payload.week[day]}"]`)?.scrollIntoView({block: 'nearest'});
    }
    function rememberDraft() {
        if (selected && $('note-input')) noteDrafts.set(selected.id, {id: editingNoteId, text: $('note-input').value});
    }
    function setNoteEditor(id, text) {
        editingNoteId = id ?? null;
        if (!$('note-input')) return;
        $('note-input').value = text;
        $('note-editor-label').textContent = (editingNoteId != null ? 'Изменить комментарий' : 'Новый комментарий') + ' · ' + shortDate(payload.today);
    }
    function renderNotes() {
        $('note-history').innerHTML = (selected.noteHistory || []).map(note => `<article data-date="${esc(note.action_date)}"><div class="note-heading"><strong>${shortDate(note.action_date)}.${note.action_date.slice(0, 4)}</strong>${config.canEdit && note.can_edit ? `<button class="note-edit" data-edit-note="${note.id}">Изменить</button>` : ''}</div><p>${esc(note.note)}</p><small>${esc(note.user_name)} · ${esc(dateTime(note.created_at))}</small>${note.updated_at ? `<small>Изменено: ${esc(note.updated_by)} · ${esc(dateTime(note.updated_at))}</small>` : ''}</article>`).join('') || '<p class="muted">С начала выбранной недели записей пока нет.</p>';
        $('note-history').querySelectorAll('[data-edit-note]').forEach(button => button.onclick = () => {
            if (noteSaving) return;
            const note = selected.noteHistory.find(note => note.id === Number(button.dataset.editNote));
            if (!note?.can_edit) return;
            setNoteEditor(note.id, note.note); rememberDraft(); $('note-input').focus();
        });
    }
    $('save-note').addEventListener('click', async () => {
        const note = $('note-input')?.value.trim();
        if (noteSaving) return;
        if (!selected || !note) { toast('Введите текст записи'); return; }
        const row = selected, noteId = editingNoteId, textArea = $('note-input');
        noteSaving = true;
        $('save-note').disabled = true;
        textArea.disabled = true;
        $('new-note').disabled = true;
        try {
            const response = await fetch('/api/analytics/analyzer/notes' + (noteId != null ? '/' + noteId : ''), {method: noteId != null ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(noteId != null ? {note} : {store: row.store_slug, article: row.article, day: payload.today, note})});
            const body = await response.json();
            if (!response.ok || !body.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось сохранить запись');
            row.noteHistory = [...(row.noteHistory || []).filter(item => item.id !== body.note.id), body.note].sort((a, b) => a.action_date.localeCompare(b.action_date) || a.created_at.localeCompare(b.created_at) || a.id - b.id);
            const day = payload.week.indexOf(body.note.action_date);
            if (day >= 0) row.notes[day] = row.noteHistory.filter(note => note.action_date === body.note.action_date);
            noteDrafts.set(row.id, {id: body.note.id, text: body.note.note});
            if (selected === row) { renderNotes(); setNoteEditor(body.note.id, body.note.note); }
            renderPage();
            toast('Запись сохранена');
        } catch (error) { toast(error.message); }
        finally {
            noteSaving = false;
            $('save-note').disabled = false;
            textArea.disabled = false;
            if ($('note-input')) $('note-input').disabled = false;
            if ($('new-note')) $('new-note').disabled = false;
        }
    });
    $('groups-list').innerHTML = Object.entries(M.groups).map(([key, name]) => `<label><input type="checkbox" data-group="${key}" ${visibleGroups.has(key) ? 'checked' : ''}>${name}</label>`).join('');
    root.querySelectorAll('[data-group]').forEach(input => input.addEventListener('change', () => {
        if (!input.checked && visibleGroups.size === 1) { input.checked = true; return; }
        if (input.checked) visibleGroups.add(input.dataset.group); else visibleGroups.delete(input.dataset.group);
        // Hidden filters must not silently exclude products.
        M.fields.filter(column => !visibleGroups.has(column.group)).forEach(column => { delete state.filters[column.index]; });
        if (!visibleGroups.has('product')) delete state.filters[M.combined.index];
        if (payload) buildColumns();
    }));
    $('show-all').onclick = () => { Object.keys(M.groups).forEach(key => visibleGroups.add(key)); root.querySelectorAll('[data-group]').forEach(input => { input.checked = true; }); if (payload) buildColumns(); };
    $('sheet-order').onchange = () => { state.filters = {}; if (payload) buildColumns(); };
    $('dense').onchange = () => { root.classList.toggle('compact', $('dense').checked); savePreferences(); };
    function toggleColumns(open) {
        $('column-panel').hidden = !open;
        $('columns-toggle').setAttribute('aria-expanded', String(open));
        if (open) {
            const rect = $('columns-toggle').getBoundingClientRect();
            $('column-panel').style.top = rect.bottom + 6 + 'px';
            $('column-panel').style.right = Math.max(10, innerWidth - rect.right) + 'px';
            $('column-panel').style.maxHeight = Math.max(160, innerHeight - rect.bottom - 18) + 'px';
        }
    }
    $('columns-toggle').onclick = () => toggleColumns($('column-panel').hidden);
    $('columns-done').onclick = () => toggleColumns(false);
    document.addEventListener('click', event => { if (!event.target.closest('.columns-wrap')) toggleColumns(false); });
    $('search').oninput = event => { state.query = event.target.value; state.page = 1; renderPage(); };
    root.querySelectorAll('[data-state]').forEach(button => button.onclick = () => {
        state.segment = button.dataset.state;
        root.querySelectorAll('[data-state]').forEach(item => { item.classList.toggle('active', item === button); item.setAttribute('aria-pressed', String(item === button)); });
        state.page = 1;
        renderPage();
    });
    $('reset').onclick = () => {
        state.query = state.project = state.manager = '';
        $('search').value = '';
        state.filters = {};
        $('table')._tfFilters = {};
        root.querySelector('[data-state="all"]').click();
        buildColumns();
    };
    $('page-size').onchange = event => { state.pageSize = Number(event.target.value); state.page = 1; renderPage(); };
    $('page-prev').onclick = () => { state.page--; renderPage(); };
    $('page-next').onclick = () => { state.page++; renderPage(); };
    $('prev-week').onclick = () => loadWeek(addDays(requestedWeek, -7));
    $('next-week').onclick = () => { if (requestedWeek < monday(config.today)) loadWeek(addDays(requestedWeek, 7)); };
    $('close-drawer').onclick = closeDrawer;
    $('tbody').addEventListener('click', async event => {
        const open = event.target.closest('[data-open]'), note = event.target.closest('[data-note]'), copy = event.target.closest('[data-copy]');
        if (open) openDrawer(open.dataset.open);
        if (note) openDrawer(note.dataset.note, Number(note.dataset.day));
        if (copy) { try { await navigator.clipboard.writeText(copy.dataset.copy); toast('Артикул скопирован'); } catch { toast('Не удалось скопировать артикул'); } }
    });
    root.querySelectorAll('[data-jump]').forEach(button => button.onclick = () => {
        const idx = columns.findIndex(column => column.group === button.dataset.jump);
        if (idx < 0) return;
        const offset = columns.slice(0, idx).reduce((sum, column) => sum + column.width, 0);
        const pinnedWidth = columns.filter(column => column.frozenLeft != null).reduce((sum, column) => sum + column.width, 0);
        $('table-scroll').scrollTo({left: Math.max(0, offset - pinnedWidth), behavior: 'instant'});
        root.querySelectorAll('[data-jump]').forEach(item => item.classList.toggle('active', item === button));
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') { closeDrawer(); toggleColumns(false); }
    });
    $('export').onclick = () => {
        if (!payload || loading) return;
        const exported = M.fields.filter(column => column.type !== 'spacer');
        const lines = [exported.map(column => label(column).replace(/<br>/g, ' '))];
        M.tableRows(rows, state).forEach(row => lines.push(exported.map(column => column.type === 'photo' ? row.image : M.value(row, column))));
        const blob = new Blob(['\uFEFF' + lines.map(line => line.map(M.csvCell).join(';')).join('\r\n')], {type: 'text/csv;charset=utf-8'});
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url;
        link.download = `wb-analyzer-${loadedWeek}.csv`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast('Экспортированы все отфильтрованные товары');
    };
    loadWeek(requestedWeek);
})();
