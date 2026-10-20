(() => {
    const root = document.getElementById('rating-report');
    if (!root) return;
    const config = JSON.parse(document.getElementById('rating-config').textContent);
    const byId = (id) => document.getElementById(id);
    const form = byId('rating-filters');
    const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const labels = { problem: 'Требует внимания', healthy: 'Без проблем', no_data: 'Нет данных' };
    const periods = [ ['7d', '7 дней'], ['30d', '30 дней'], ['6m', '6 месяцев'], ['1y', 'Год'] ];
    const storageKey = 'checkstock:rating-report:period';
    let page = 1;
    let sortBy = 'change';
    let sortOrder = 'asc';
    let requestNumber = 0;

    const { displayDate, isoDate } = window.CheckStockReportDaily;
    let saved;
    try { saved = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) { /* Ignore invalid state. */ }
    const dateFrom = byId('rating-date-from');
    const dateTo = byId('rating-date-to');
    dateFrom.value = displayDate(saved?.from || config.dateFrom);
    dateTo.value = displayDate(saved?.to || config.dateTo);
    [dateFrom, dateTo].forEach(window.CheckStockReportDaily.bindDatePicker);

    function setOptions(select, values, names) {
        const selected = select.value;
        select.replaceChildren(new Option('Все', ''));
        values.forEach((value, index) => select.add(new Option(names?.[index] || value, value)));
        select.value = Array.from(select.options).some((option) => option.value === selected) ? selected : '';
        select.syncSearch?.();
        return select.value !== selected;
    }
    const storeNames = new Map(config.stores.map((store) => [store.slug, store.name]));
    setOptions(byId('rating-store'), config.stores.map((store) => store.slug), config.stores.map((store) => store.name));

    // The same searchable select control is used by the SPP and rating reports.
    root.querySelectorAll('[data-wb-filter] select').forEach((select) => window.CheckStockReportSelect?.(select));

    const cell = (row, value) => {
        const td = document.createElement('td');
        td.textContent = value;
        row.append(td);
        return td;
    };
    const ratingText = (value) => value === null || value === undefined ? '—' : number.format(value);

    function drawChart(container, days, start = 0, end = days.length - 1) {
        container.replaceChildren();
        if (!days.some((day) => day.rating !== null)) {
            container.textContent = 'За этот срок нет дневных замеров рейтинга.';
            container.classList.add('rating-chart-empty');
            return;
        }
        const controls = document.createElement('div');
        controls.className = 'wb-history__controls';
        const range = document.createElement('span');
        range.textContent = `${displayDate(days[start].date)} — ${displayDate(days[end].date)}`;
        const buttons = document.createElement('div');
        buttons.className = 'wb-history__zoom-buttons';
        function zoom(factor, anchor = (start + end) / 2) {
            const count = Math.max(3, Math.min(days.length, Math.round((end - start + 1) * factor)));
            const next = Math.max(0, Math.min(days.length - count, Math.round(anchor - count / 2)));
            drawChart(container, days, next, next + count - 1);
        }
        for (const [label, action] of [ ['−', () => zoom(1.5)], ['+', () => zoom(0.67)], ['Весь период', () => drawChart(container, days)] ]) {
            const button = document.createElement('button');
            button.type = 'button'; button.textContent = label;
            button.addEventListener('click', action); buttons.append(button);
        }
        controls.append(range, buttons);
        container.append(controls);
        const frame = document.createElement('div');
        frame.className = 'wb-history__frame';
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 800 240');
        svg.setAttribute('role', 'img');
        svg.setAttribute('tabindex', '0');
        svg.setAttribute('aria-label', 'Рейтинг по дням. Стрелки выбирают день.');
        svg.classList.add('wb-history__chart');
        const tooltip = document.createElement('div');
        tooltip.className = 'wb-history__tooltip';
        tooltip.hidden = true;
        tooltip.setAttribute('role', 'tooltip');
        frame.append(svg, tooltip); container.append(frame);
        const low = Math.max(0, Math.min(...days.filter((day) => day.rating !== null).map((day) => day.rating)) - 0.2);
        const high = Math.min(5, Math.max(...days.filter((day) => day.rating !== null).map((day) => day.rating)) + 0.2);
        const y = (value) => 190 - (value - low) / (high - low || 1) * 150;
        const x = (index) => 45 + (index - start) / Math.max(1, end - start) * 725;
        const add = (name, attrs, text) => {
            const node = document.createElementNS('http://www.w3.org/2000/svg', name);
            Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
            if (text !== undefined) node.textContent = text;
            svg.append(node);
            return node;
        };
        for (const value of [low, (low + high) / 2, high]) {
            add('line', { x1: 45, x2: 770, y1: y(value), y2: y(value), class: 'wb-history__grid' });
            add('text', { x: 40, y: y(value) + 4, 'text-anchor': 'end', class: 'wb-history__label' }, number.format(value));
        }
        window.CheckStockReportDaily.segments(days, (day) => day.rating, start, end)
            .forEach((segment) => {
                if (segment.length > 1) add('polyline', {
                    points: segment.map((point) => `${x(point.index)},${y(point.value)}`).join(' '),
                    class: 'wb-history__line',
                });
            });
        for (let index = start; index <= end; index++) {
            if (days[index].rating !== null) add('circle', { cx: x(index), cy: y(days[index].rating), r: 3.5, class: 'wb-history__dot' });
        }
        let selected = end;
        const show = (index) => {
            selected = Math.max(start, Math.min(end, index));
            const day = days[selected];
            tooltip.textContent = `${displayDate(day.date)} · ${day.rating === null ? 'Нет замера' : `Рейтинг ${ratingText(day.rating)}`}${day.reviews_count === null ? '' : ` · Отзывов: ${day.reviews_count}`}`;
            tooltip.hidden = false;
            tooltip.style.left = `${Math.max(70, Math.min(frame.clientWidth - 70, x(selected) / 800 * frame.clientWidth))}px`;
            tooltip.style.top = '10px';
            tooltip.style.transform = 'translateX(-50%)';
        };
        svg.addEventListener('pointermove', (event) => {
            const rect = svg.getBoundingClientRect();
            show(Math.round(start + (event.clientX - rect.left) / rect.width * (end - start)));
        });
        svg.addEventListener('pointerleave', () => { tooltip.hidden = true; });
        svg.addEventListener('wheel', (event) => { event.preventDefault(); zoom(event.deltaY > 0 ? 1.5 : 0.67, selected); }, { passive: false });
        svg.addEventListener('keydown', (event) => {
            if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); show(selected + (event.key === 'ArrowRight' ? 1 : -1)); }
            if (event.key === '+' || event.key === '=') zoom(0.67, selected);
            if (event.key === '-') zoom(1.5, selected);
        });
        let dragged;
        svg.addEventListener('pointerdown', (event) => { dragged = event.clientX; svg.setPointerCapture(event.pointerId); });
        svg.addEventListener('pointerup', (event) => {
            if (dragged === undefined) return;
            const shift = Math.round((dragged - event.clientX) / svg.clientWidth * (end - start));
            dragged = undefined;
            const next = Math.max(0, Math.min(days.length - (end - start + 1), start + shift));
            if (next !== start) drawChart(container, days, next, next + end - start);
        });
    }

    function expand(row, product) {
        const next = row.nextElementSibling;
        if (next?.classList.contains('wb-history-row')) { next.remove(); return; }
        const detail = document.createElement('tr');
        detail.className = 'wb-history-row';
        const td = document.createElement('td');
        td.colSpan = 8; td.className = 'wb-history'; detail.append(td); row.after(detail);
        const header = document.createElement('div'); header.className = 'wb-history__header';
        const title = document.createElement('strong'); title.textContent = `История рейтинга · ${product.article}`;
        const buttons = document.createElement('div'); buttons.className = 'wb-history__periods';
        const chart = document.createElement('div'); td.append(header, chart); header.append(title, buttons);
        async function loadHistory(period) {
            buttons.querySelectorAll('button').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.period === period)));
            chart.textContent = 'Загрузка…';
            try {
                const params = new URLSearchParams({ store: product.store_slug, nm_id: product.nm_id, period });
                const response = await fetch(`/api/unit-economics-1c/reports/rating/history?${params}`);
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить историю');
                drawChart(chart, result.days);
            } catch (error) { chart.textContent = error.message; }
        }
        periods.forEach(([value, label]) => {
            const button = document.createElement('button'); button.type = 'button';
            button.textContent = label; button.dataset.period = value;
            button.addEventListener('click', () => loadHistory(value)); buttons.append(button);
        });
        loadHistory('7d');
    }

    function render(result) {
        byId('rating-message').textContent = result.total ? '' : 'Нет товаров для выбранных фильтров.';
        byId('rating-problem').textContent = String(result.summary.problem);
        byId('rating-healthy').textContent = String(result.summary.healthy);
        byId('rating-no-data').textContent = `Нет данных для сравнения: ${result.summary.no_data}`;
        byId('rating-period-note').textContent = `Выбранный период: ${displayDate(result.period.date_from)} — ${displayDate(result.period.date_to)}. Предыдущий: ${displayDate(result.period.previous_from)} — ${displayDate(result.period.previous_to)}.`;
        for (const [field, values] of Object.entries(result.options)) {
            const select = byId(`rating-${field === 'stores' ? 'store' : field === 'brands' ? 'brand' : field === 'managers' ? 'manager' : field === 'subjects' ? 'subject' : 'article'}`);
            setOptions(select, values, field === 'stores' ? values.map((value) => storeNames.get(value) || value) : undefined);
        }
        const tbody = byId('rating-rows'); tbody.replaceChildren();
        result.rows.forEach((product) => {
            const tr = document.createElement('tr');
            const article = cell(tr, '');
            const toggle = document.createElement('button');
            toggle.type = 'button'; toggle.className = 'wb-history-toggle'; toggle.textContent = '▸';
            toggle.setAttribute('aria-label', `Показать историю рейтинга ${product.article}`);
            toggle.setAttribute('aria-expanded', 'false');
            toggle.addEventListener('click', () => { expand(tr, product); const open = tr.nextElementSibling?.classList.contains('wb-history-row'); toggle.setAttribute('aria-expanded', String(open)); toggle.textContent = open ? '▾' : '▸'; });
            article.append(toggle, document.createTextNode(product.article));
            cell(tr, product.name);
            const latest = cell(tr, ratingText(product.latest_rating));
            if (product.latest_observed_at) { const stamp = document.createElement('small'); stamp.className = 'rating-fresh-time'; stamp.textContent = `Обновлено ${new Date(product.latest_observed_at).toLocaleString('ru-RU')}`; latest.append(stamp); }
            cell(tr, product.reviews_count === null ? '—' : String(product.reviews_count));
            cell(tr, ratingText(product.current_rating));
            cell(tr, ratingText(product.previous_rating));
            const delta = cell(tr, product.change === null ? '—' : `${product.change > 0 ? '+' : ''}${ratingText(product.change)}`);
            if (product.change !== null) delta.className = product.change < 0 ? 'rating-change--down' : 'rating-change--up';
            const statusCell = cell(tr, ''); const badge = document.createElement('span');
            badge.className = `wb-status wb-status--${product.status}`; badge.textContent = labels[product.status]; statusCell.append(badge);
            tbody.append(tr);
        });
        byId('rating-page-label').textContent = `${result.page} / ${Math.max(1, Math.ceil(result.total / result.page_size))}`;
        byId('rating-prev').disabled = result.page <= 1;
        byId('rating-next').disabled = result.page * result.page_size >= result.total;
        root.querySelectorAll('[data-rating-sort]').forEach((button) => button.closest('th').setAttribute('aria-sort', button.dataset.ratingSort === sortBy ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'));
    }

    async function load() {
        const sequence = ++requestNumber;
        const from = isoDate(dateFrom.value); const to = isoDate(dateTo.value);
        if (!from || !to || from > to) { byId('rating-message').textContent = 'Выберите корректные даты в формате ДД.ММ.ГГГГ'; return; }
        const params = new URLSearchParams(new FormData(form));
        params.set('date_from', from); params.set('date_to', to);
        params.set('page', String(page)); params.set('sort_by', sortBy); params.set('sort_order', sortOrder);
        byId('rating-message').textContent = 'Загрузка…';
        try {
            const response = await fetch(`/api/unit-economics-1c/reports/rating?${params}`);
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || 'Не удалось загрузить отчёт');
            if (sequence === requestNumber) { sessionStorage.setItem(storageKey, JSON.stringify({ from, to })); render(result); }
        } catch (error) { if (sequence === requestNumber) byId('rating-message').textContent = error.message; }
    }
    form.addEventListener('submit', (event) => { event.preventDefault(); page = 1; load(); });
    byId('rating-reset').addEventListener('click', () => {
        root.querySelectorAll('[data-wb-filter] select').forEach((select) => { select.value = ''; select.resetSearch?.(); });
        byId('rating-search').value = ''; byId('rating-tolerance').value = '0';
        dateFrom.value = displayDate(config.dateFrom); dateTo.value = displayDate(config.dateTo);
        sessionStorage.removeItem(storageKey); page = 1; load();
    });
    root.querySelectorAll('[data-wb-filter] select').forEach((select) => select.addEventListener('change', () => { page = 1; load(); }));
    root.querySelectorAll('[data-rating-sort]').forEach((button) => button.addEventListener('click', () => { const field = button.dataset.ratingSort; sortOrder = sortBy === field && sortOrder === 'asc' ? 'desc' : 'asc'; sortBy = field; page = 1; load(); }));
    byId('rating-prev').addEventListener('click', () => { page--; load(); });
    byId('rating-next').addEventListener('click', () => { page++; load(); });
    load();
})();
