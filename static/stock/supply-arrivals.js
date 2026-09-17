(() => {
    'use strict';
    const root = document.querySelector('[data-arrivals-page]');
    if (!root) return;
    const q = (selector) => root.querySelector(selector);
    const esc = window.CheckStockUI.escapeHtml;
    const storageKey = `checkstock-arrivals:${root.dataset.preferenceKey}:v1`;
    const dayMs = 86400000;
    const weekMs = 7 * dayMs;
    const formatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    let rows = [], today = '', snapshot = null, loading = false, timer;
    let initialized = false, pendingRefresh = false;
    const expanded = new Set();

    function readPreferences() {
        try { return JSON.parse(localStorage.getItem(storageKey)) || {}; } catch { return {}; }
    }
    const saved = readPreferences();
    const stringList = (value) => Array.isArray(value) ? value.filter((v) => typeof v === 'string') : null;
    const state = {
        search: typeof saved.search === 'string' ? saved.search : '',
        project: typeof saved.project === 'string' ? saved.project : '',
        warehouse: typeof saved.warehouse === 'string' ? saved.warehouse : '',
        statuses: stringList(saved.statuses) || ['В пути из китая'],
        weeks: stringList(saved.weeks) || [],
        defaultWeeks: saved.defaultWeeks !== false,
    };
    function persist() { try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* Private mode. */ } }
    q('[data-arrivals-search]').value = state.search;

    const epoch = (iso) => iso ? Date.parse(`${iso}T00:00:00Z`) : NaN;
    function monday(time) {
        const date = new Date(time);
        return time - ((date.getUTCDay() + 6) % 7) * dayMs;
    }
    function weekKey(iso) {
        if (!iso) return 'undated';
        const start = monday(epoch(iso));
        const year = new Date(start + 3 * dayMs).getUTCFullYear();
        const first = monday(Date.UTC(year, 0, 4));
        return `${year}-W${String(1 + Math.round((start - first) / weekMs)).padStart(2, '0')}`;
    }
    const isoDate = (time) => new Date(time).toISOString().slice(0, 10);
    const dateLabel = (iso) => iso ? new Date(epoch(iso)).toLocaleDateString('ru-RU', { timeZone: 'UTC' }) : '—';
    function weekLabel(key) {
        if (key === 'undated') return 'Без даты';
        const [year, week] = key.split('-W').map(Number);
        const start = monday(Date.UTC(year, 0, 4)) + (week - 1) * weekMs;
        const short = (time) => dateLabel(isoDate(time)).slice(0, 5);
        return `W${week} ${year} · ${short(start)}–${short(start + 6 * dayMs)}`;
    }
    function transit(row) { return row.status.trim().toLocaleLowerCase('ru-RU') === 'в пути из китая'; }
    function delayed(row) { return row.arrival && epoch(row.arrival) < epoch(today) && transit(row); }
    const unique = (items) => [...new Set(items.filter(Boolean))];
    function matchesStatuses(row) { return !state.statuses.length || state.statuses.includes(row.status || 'Без статуса'); }
    function weekOptions() {
        return unique(rows.filter(matchesStatuses).map((row) => weekKey(row.arrival))).sort();
    }
    function normalizeWeeks(statusChanged = false) {
        if (state.defaultWeeks) {
            state.weeks = [weekKey(today), weekKey(isoDate(epoch(today) + weekMs))];
        } else if (statusChanged) {
            const available = weekOptions();
            state.weeks = state.weeks.filter((week) => available.includes(week));
        }
    }
    function fillSelect(selector, values, field, placeholder) {
        const options = unique(values).sort((a, b) => a.localeCompare(b, 'ru'));
        // A saved filter may become unavailable when permissions or source data change.
        if (!options.includes(state[field])) state[field] = '';
        q(selector).innerHTML = `<option value="">${placeholder}</option>` + options.map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
        q(selector).value = state[field];
    }
    function renderOptions(field, options) {
        const selected = state[field];
        const all = selected.length === 0;
        q(`[data-arrivals-options="${field}"]`).innerHTML =
            `<label><input type="checkbox" data-pick="${field}" data-all="true" ${all ? 'checked' : ''}>Все ${field === 'weeks' ? 'недели' : 'статусы'}</label>` +
            options.map((value) => `<label><input type="checkbox" data-pick="${field}" value="${esc(value)}" ${all || selected.includes(value) ? 'checked' : ''}>${esc(field === 'weeks' ? weekLabel(value) : value)}</label>`).join('');
        q(`[data-arrivals-summary="${field}"]`).textContent = all
            ? (field === 'weeks' ? 'Все недели' : 'Все статусы')
            : field === 'weeks' && state.defaultWeeks ? 'Текущая и следующая'
            : selected.length === 1 ? (field === 'weeks' ? weekLabel(selected[0]) : selected[0])
            : `Выбрано: ${selected.length}`;
    }
    function renderFilters() {
        renderOptions('statuses', unique(rows.map((row) => row.status || 'Без статуса')).sort());
        renderOptions('weeks', weekOptions());
    }
    function sum(items, field) {
        const numbers = items.map((row) => row[field]).filter((value) => typeof value === 'number' && Number.isFinite(value));
        return { value: numbers.length ? numbers.reduce((a, b) => a + b, 0) : items.length ? null : 0, missing: items.length - numbers.length };
    }
    const number = (value) => value === null ? '—' : formatter.format(value);
    function numberCell(items, field) {
        const total = sum(items, field);
        return `<td class="arrivals-number"${total.missing ? ` title="Без значения: ${total.missing}"` : ''}>${number(total.value)}${total.missing && total.value !== null ? '<span class="arrivals-muted">неполные данные</span>' : ''}</td>`;
    }
    function rowClass(items) {
        const start = monday(epoch(today));
        let css = '';
        if (items.every((row) => row.arrival && epoch(row.arrival) >= start + weekMs && epoch(row.arrival) < start + 2 * weekMs)) css = 'is-next';
        if (items.every((row) => row.arrival && epoch(row.arrival) >= start + 2 * weekMs)) css = 'is-later';
        if (items.some((row) => row.arrival && [0, dayMs].includes(epoch(row.arrival) - epoch(today)))) css = 'is-urgent';
        if (items.some(delayed)) css += ' is-delayed';
        return css;
    }
    function fileLink(row) {
        // Defence in depth: the API already validates links from the source.
        if (!/^https?:\/\//i.test(row.file)) return '—';
        return `<a href="${esc(row.file)}" target="_blank" rel="noopener noreferrer" aria-label="Файл заказа ${esc(row.order)}">Файл ↗</a>`;
    }
    function cells(items, group = '') {
        const first = items[0];
        const join = (field) => esc(unique(items.map((row) => row[field])).join(', ') || '—');
        const dates = unique(items.map((row) => row.arrival)).sort();
        const date = dates.length > 1 ? `${dateLabel(dates[0])}<br>${dateLabel(dates.at(-1))}` : dateLabel(dates[0]);
        const weeks = unique(items.map((row) => weekKey(row.arrival)));
        const week = weeks.map((key) => key === 'undated' ? 'Без даты' : key.split('-')[1]).join(', ');
        const errors = items.flatMap((row) => row.warnings || []);
        const toggle = group ? `<button class="arrivals-group-toggle" type="button" data-arrivals-expand="${esc(group)}" aria-expanded="${expanded.has(group)}" aria-label="${expanded.has(group) ? 'Свернуть' : 'Раскрыть'} группу ${esc(group)}">${expanded.has(group) ? '−' : '+'}</button>` : '';
        const order = group ? `${toggle}${esc(group)}<span class="arrivals-muted">Заказов: ${items.length}</span>` : esc(first.order);
        const warning = errors.length ? `<span class="arrivals-muted" title="${esc(unique(errors).join('; '))}">⚠ Проверьте данные</span>` : '';
        return `<td title="${esc(weeks.map(weekLabel).join('; '))}">${esc(week)}</td><td>${join('project')}</td><td>${order}${warning}</td>
            <td>${group ? '<span class="arrivals-muted">Внутри группы</span>' : fileLink(first)}</td><td>${join('category')}</td><td>${join('warehouse')}</td>
            <td><span class="arrivals-status">${join('status')}</span>${items.some(delayed) ? '<span class="arrivals-delay">ОПАЗДЫВАЕТ</span>' : ''}</td>
            <td>${date}${dates.length < items.length && items.some((row) => !row.arrival) ? '<span class="arrivals-muted">Есть заказы без даты</span>' : ''}</td>
            ${numberCell(items, 'volume')}${numberCell(items, 'weight')}${numberCell(items, 'boxes')}<td>${join('shipping')}</td>`;
    }
    function renderTable(filtered) {
        const sorted = [...filtered].sort((a, b) => (epoch(a.arrival) || Infinity) - (epoch(b.arrival) || Infinity) || a.row - b.row);
        const groups = new Map();
        sorted.forEach((row) => { if (row.group.trim()) { const key = row.group.trim(); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(row); } });
        const emitted = new Set();
        let html = '';
        sorted.forEach((row) => {
            const group = row.group.trim(), members = groups.get(group);
            if (!group || members.length < 2) { html += `<tr class="${rowClass([row])}">${cells([row])}</tr>`; return; }
            if (emitted.has(group)) return;
            emitted.add(group);
            html += `<tr class="is-group ${rowClass(members)}">${cells(members, group)}</tr>`;
            if (expanded.has(group)) members.forEach((child) => { html += `<tr class="is-child ${rowClass([child])}">${cells([child])}</tr>`; });
        });
        if (!filtered.length) html = `<tr><td colspan="12" class="arrivals-empty"><strong>${snapshot?.last_success ? 'Поставок по этим фильтрам нет' : 'Поставки ещё не загружены'}</strong>${snapshot?.last_success ? 'Выберите другие недели или сбросьте фильтры.' : 'Первое обновление заполняет реестр из Google Таблицы.'}</td></tr>`;
        q('[data-arrivals-body]').innerHTML = html;
    }
    function render() {
        if (!snapshot) return;
        const search = state.search.trim().toLocaleLowerCase('ru-RU');
        const base = rows.filter((row) => matchesStatuses(row)
            && (!state.project || row.project === state.project)
            && (!state.warehouse || row.warehouse === state.warehouse)
            && (!search || [row.project, row.order, row.category, row.group].join(' ').toLocaleLowerCase('ru-RU').includes(search)));
        const filtered = base.filter((row) => !state.weeks.length || state.weeks.includes(weekKey(row.arrival)));
        q('[data-arrivals-kpi="week"]').textContent = snapshot.last_success ? String(base.filter((row) => weekKey(row.arrival) === weekKey(today)).length) : '—';
        const weekCaption = `${weekLabel(weekKey(today))} · без фильтра недель`;
        q('[data-arrivals-week-caption]').textContent = weekCaption;
        q('[data-arrivals-kpi="week"]').closest('.arrivals-kpi').title = weekCaption;
        for (const field of ['volume', 'weight', 'boxes']) {
            const total = sum(filtered, field);
            q(`[data-arrivals-kpi="${field}"]`).textContent = snapshot.last_success ? number(total.value) : '—';
            const caption = q(`[data-arrivals-caption="${field}"]`);
            caption.textContent = total.missing ? `Без значения: ${total.missing} из ${filtered.length}` : 'По выбранным поставкам';
            caption.classList.toggle('arrivals-sr-only', !total.missing);
            caption.closest('.arrivals-kpi').title = `По выбранным поставкам${total.missing ? ` · ${caption.textContent}` : ''}`;
        }
        q('[data-arrivals-count]').textContent = `Показано ${filtered.length} из ${rows.length} поставок`;
        renderTable(filtered);
    }
    function notice(text) { q('[data-arrivals-notice]').textContent = text; q('[data-arrivals-notice]').hidden = !text; }
    function showState() {
        const running = snapshot.running || pendingRefresh;
        q('[data-arrivals-refresh]').disabled = running;
        q('[data-arrivals-refresh]').textContent = running ? 'Обновление…' : 'Обновить данные';
        const stamp = snapshot.last_success ? new Date(snapshot.last_success).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : null;
        q('[data-arrivals-updated]').textContent = stamp ? `Обновлено ${stamp} МСК${running ? ' · загружаем свежие данные…' : ''}` : running ? 'Загружаем данные из Google Таблицы…' : 'Ожидаем первую загрузку';
        const text = snapshot.error ? `${snapshot.error}${stamp ? ' Показана последняя успешная загрузка.' : ''}`
            : snapshot.stale && stamp ? 'Данные давно не обновлялись. Показана последняя успешная загрузка.' : '';
        notice(text);
        if (snapshot.source_url && /^https:\/\/docs\.google\.com\//.test(snapshot.source_url)) {
            q('[data-arrivals-source]').href = snapshot.source_url;
            q('[data-arrivals-source]').hidden = false;
        }
    }
    async function request(url, options = {}) {
        const response = await fetch(url, { ...options, headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' }, signal: AbortSignal.timeout(20000) });
        if (!response.ok) throw new Error(response.status === 401 ? 'Сессия завершена. Войдите на сайт заново.' : response.status === 403 ? 'Нет доступа к этому действию.' : 'Не удалось получить данные. Повторите попытку.');
        return response.json();
    }
    async function load() {
        if (loading) return;
        clearTimeout(timer);
        loading = true;
        try {
            const data = await request('/supply-schedule/data');
            snapshot = data;
            rows = data.rows;
            today = data.today;
            pendingRefresh = false;
            normalizeWeeks();
            fillSelect('[data-arrivals-project]', rows.map((row) => row.project), 'project', 'Все проекты');
            fillSelect('[data-arrivals-warehouse]', rows.map((row) => row.warehouse), 'warehouse', 'Все склады');
            if (!initialized) {
                renderFilters();
                initialized = true;
            } else {
                // Do not close an open menu while the user is choosing filters.
                if (!q('[data-arrivals-filter][open]')) renderFilters();
            }
            showState();
            render();
        } catch (error) {
            notice(error.name === 'TimeoutError' ? 'Сервер не ответил вовремя. Повторяем загрузку.' : error.message);
            pendingRefresh = false;
            q('[data-arrivals-refresh]').disabled = false;
            q('[data-arrivals-refresh]').textContent = 'Повторить обновление';
            if (!snapshot) q('[data-arrivals-body]').innerHTML = '<tr><td colspan="12" class="arrivals-empty">Данные недоступны. Повторите загрузку.</td></tr>';
        } finally {
            loading = false;
            timer = setTimeout(load, snapshot?.running ? 2000 : 60000);
        }
    }
    q('[data-arrivals-search]').addEventListener('input', (event) => { state.search = event.target.value; persist(); render(); });
    for (const field of ['project', 'warehouse']) q(`[data-arrivals-${field}]`).addEventListener('change', (event) => { state[field] = event.target.value; persist(); render(); });
    root.addEventListener('change', (event) => {
        const input = event.target.closest('[data-pick]');
        if (!input) return;
        const field = input.dataset.pick;
        const options = field === 'weeks' ? weekOptions() : unique(rows.map((row) => row.status || 'Без статуса')).sort();
        if (input.dataset.all) state[field] = [];
        else {
            const values = new Set(state[field].length ? state[field] : options);
            if (input.checked) values.add(input.value); else values.delete(input.value);
            state[field] = [...values];
            if (state[field].length === options.length) state[field] = [];
        }
        if (field === 'weeks') state.defaultWeeks = false;
        if (field === 'statuses') normalizeWeeks(true);
        renderFilters(); persist(); render();
    });
    root.addEventListener('click', (event) => {
        const button = event.target.closest('[data-arrivals-expand]');
        if (!button) return;
        const group = button.dataset.arrivalsExpand;
        if (expanded.has(group)) expanded.delete(group); else expanded.add(group);
        render();
        [...root.querySelectorAll('[data-arrivals-expand]')].find((el) => el.dataset.arrivalsExpand === group)?.focus({ preventScroll: true });
    });
    document.addEventListener('click', (event) => root.querySelectorAll('[data-arrivals-filter][open]').forEach((details) => { if (!details.contains(event.target)) details.open = false; }));
    root.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') root.querySelectorAll('[data-arrivals-filter][open]').forEach((details) => { details.open = false; details.querySelector('summary').focus(); });
    });
    q('[data-arrivals-reset]').addEventListener('click', () => {
        Object.assign(state, { search: '', project: '', warehouse: '', statuses: ['В пути из китая'], defaultWeeks: true });
        q('[data-arrivals-search]').value = '';
        q('[data-arrivals-project]').value = '';
        q('[data-arrivals-warehouse]').value = '';
        if (today) normalizeWeeks();
        renderFilters(); persist(); render();
    });
    q('[data-arrivals-refresh]').addEventListener('click', async () => {
        if (pendingRefresh) return;
        pendingRefresh = true;
        q('[data-arrivals-refresh]').disabled = true;
        q('[data-arrivals-refresh]').textContent = 'Обновление…';
        try { await request('/supply-schedule/sync', { method: 'POST' }); await load(); }
        catch (error) { notice(error.message); pendingRefresh = false; q('[data-arrivals-refresh]').disabled = false; q('[data-arrivals-refresh]').textContent = 'Повторить обновление'; }
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
    load();
})();
