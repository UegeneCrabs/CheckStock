(() => {
    const root = document.getElementById('spp-report');
    if (!root) return;
    const config = JSON.parse(document.getElementById('spp-config').textContent);
    const byId = (id) => document.getElementById(id);
    const form = byId('spp-filters');
    const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
    const plural = new Intl.PluralRules('ru-RU');
    const observationCount = (count) => `${count} ${{ one: 'замер', few: 'замера', many: 'замеров', other: 'замера' }[plural.select(count)]}`;
    const historyPeriods = [
        { value: '7d', label: '7 дней' },
        { value: '30d', label: '30 дней' },
        { value: '6m', label: '6 месяцев' },
        { value: '1y', label: 'Год' },
    ];
    let currentPage = 1;
    let requestNumber = 0;
    let sortBy = 'turnover';
    let sortOrder = 'desc';
    const storageKey = 'checkstock:spp-report:period';
    const dateFrom = byId('spp-date-from');
    const dateTo = byId('spp-date-to');
    const tolerance = byId('spp-tolerance');
    const toleranceValue = byId('spp-tolerance-value');
    tolerance.value = '0';
    toleranceValue.value = '0';

    function displayDate(iso) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
        return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
    }

    function isoDate(value) {
        const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(value.trim());
        if (!match) return null;
        const iso = `${match[3]}-${match[2]}-${match[1]}`;
        const parsed = new Date(`${iso}T12:00:00Z`);
        return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === iso ? iso : null;
    }

    let savedPeriod;
    try { savedPeriod = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) { /* Invalid saved state. */ }
    const savedFrom = savedPeriod && isoDate(displayDate(savedPeriod.from));
    const savedTo = savedPeriod && isoDate(displayDate(savedPeriod.to));
    dateFrom.value = displayDate(savedFrom || config.dateFrom);
    dateTo.value = displayDate(savedTo || config.dateTo);
    dateTo.setAttribute('aria-label', 'Дата окончания в формате ДД.ММ.ГГГГ');
    dateFrom.setAttribute('aria-label', 'Дата начала в формате ДД.ММ.ГГГГ');

    [dateFrom, dateTo].forEach((textInput) => {
        const nativeInput = byId(`${textInput.id}-native`);
        const openPicker = () => {
            nativeInput.value = isoDate(textInput.value) || '';
            if (nativeInput.showPicker) nativeInput.showPicker();
            else nativeInput.click();
        };
        textInput.addEventListener('click', openPicker);
        textInput.parentElement.querySelector('button').addEventListener('click', openPicker);
        nativeInput.addEventListener('change', () => {
            if (!nativeInput.value) return;
            textInput.value = displayDate(nativeInput.value);
            textInput.dispatchEvent(new Event('change', { bubbles: true }));
        });
    });

    function updateTolerance() {
        toleranceValue.value = tolerance.value;
    }
    tolerance.addEventListener('input', updateTolerance);
    toleranceValue.addEventListener('input', () => {
        const value = Number(toleranceValue.value);
        if (toleranceValue.value !== '' && Number.isFinite(value) && value >= 0) {
            tolerance.value = String(Math.min(value, 100));
        }
    });
    updateTolerance();
    function rememberPeriod() {
        const from = isoDate(dateFrom.value);
        const to = isoDate(dateTo.value);
        if (from && to && from <= to) sessionStorage.setItem(storageKey, JSON.stringify({ from, to }));
    }
    dateFrom.addEventListener('change', rememberPeriod);
    dateTo.addEventListener('change', rememberPeriod);

    byId('spp-manager-label').hidden = !config.showManager;

    function setOptions(select, values, labels) {
        const previous = select.value;
        select.replaceChildren(new Option('Все', ''));
        values.forEach((value, index) => select.add(new Option(labels ? labels[index] : value, value)));
        select.value = Array.from(select.options).some((option) => option.value === previous) ? previous : '';
        select.syncSearch?.();
    }

    function setupSearchableSelect(select) {
        const container = select.closest('[data-spp-filter]');
        const control = document.createElement('div');
        const input = document.createElement('input');
        const toggle = document.createElement('button');
        const list = document.createElement('div');
        let open = false;
        let active = -1;
        let matches = [];
        control.className = 'spp-filter__control';
        input.type = 'search';
        input.id = `${select.id}-search`;
        input.autocomplete = 'off';
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', `${select.id}-options`);
        toggle.type = 'button';
        toggle.className = 'spp-filter__toggle';
        toggle.textContent = '▾';
        toggle.setAttribute('aria-label', `Показать все варианты: ${container.querySelector('label').textContent}`);
        list.id = `${select.id}-options`;
        list.className = 'spp-filter__options';
        list.setAttribute('role', 'listbox');
        list.hidden = true;
        control.append(input, toggle);
        container.append(control, list);
        select.hidden = true;

        function sync() {
            input.value = select.selectedOptions[0]?.textContent || 'Все';
        }
        function setActive(index) {
            active = index;
            list.querySelectorAll('[role="option"]').forEach((option, position) => {
                option.classList.toggle('is-active', position === active);
                if (position === active) option.scrollIntoView({ block: 'nearest' });
            });
            if (active < 0) input.removeAttribute('aria-activedescendant');
            else input.setAttribute('aria-activedescendant', `${list.id}-${active}`);
        }
        function draw(query = '') {
            const normalized = query.trim().toLocaleLowerCase('ru');
            matches = Array.from(select.options).filter((option) =>
                !normalized || option.textContent.toLocaleLowerCase('ru').includes(normalized));
            list.replaceChildren();
            matches.forEach((option, index) => {
                const item = document.createElement('button');
                item.type = 'button';
                item.id = `${list.id}-${index}`;
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', String(option.value === select.value));
                item.textContent = option.textContent;
                item.addEventListener('mousedown', (event) => event.preventDefault());
                item.addEventListener('click', () => choose(option));
                list.append(item);
            });
            if (!matches.length) {
                const empty = document.createElement('div');
                empty.className = 'spp-filter__empty';
                empty.textContent = 'Ничего не найдено';
                list.append(empty);
            }
            setActive(-1);
        }
        function show(query = '') {
            draw(query);
            open = true;
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
        }
        function close() {
            open = false;
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
            sync();
        }
        function choose(option) {
            select.value = option.value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            close();
            input.focus();
        }
        input.addEventListener('focus', () => input.select());
        input.addEventListener('click', () => { input.select(); show(); });
        input.addEventListener('input', () => show(input.value));
        toggle.addEventListener('click', () => {
            if (open) close();
            else { show(); input.focus(); }
        });
        input.addEventListener('keydown', (event) => {
            if (event.isComposing) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (!open) show();
                if (matches.length) setActive(active < 0 ? (event.key === 'ArrowDown' ? 0 : matches.length - 1)
                    : (active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
            } else if (event.key === 'Enter' && open) {
                event.preventDefault();
                if (active >= 0) choose(matches[active]);
                else if (matches.length === 1) choose(matches[0]);
            } else if (event.key === 'Escape' && open) {
                event.preventDefault();
                close();
            }
        });
        document.addEventListener('pointerdown', (event) => {
            if (open && !container.contains(event.target)) close();
        });
        container.addEventListener('focusout', (event) => {
            if (open && !container.contains(event.relatedTarget)) close();
        });
        select.syncSearch = () => { sync(); if (open) draw(); };
        sync();
    }
    root.querySelectorAll('[data-spp-filter] select').forEach(setupSearchableSelect);
    setOptions(byId('spp-store'), config.stores.map((store) => store.slug), config.stores.map((store) => store.name));

    function cell(row, value) {
        const td = document.createElement('td');
        td.textContent = value;
        row.append(td);
        return td;
    }

    function renderHistory(container, days, viewStart = 0, viewEnd = days.length - 1) {
        container.replaceChildren();
        const observed = days.filter((day) => day.average_spp !== null);
        if (!observed.length) {
            container.textContent = 'За этот срок нет замеров СПП.';
            return;
        }
        const title = document.createElement('p');
        title.className = 'spp-history__title';
        title.textContent = 'Средний СПП по дням, %';
        container.append(title);
        const values = observed.map((day) => day.average_spp);
        const minimum = Math.max(0, Math.floor(Math.min(...values) - 2));
        const maximum = Math.ceil(Math.max(...values) + 2);
        const low = minimum === maximum ? Math.max(0, minimum - 1) : minimum;
        const high = minimum === maximum ? maximum + 1 : maximum;
        const visibleDays = days.slice(viewStart, viewEnd + 1);
        const width = Math.max(320, Math.ceil(container.getBoundingClientRect().width - 2));
        const height = 240;
        const left = 52;
        const right = 18;
        const top = 20;
        const bottom = 35;
        const plotWidth = width - left - right;
        const x = (index) => left + (index - viewStart) * plotWidth / Math.max(1, viewEnd - viewStart);
        const y = (value) => top + (high - value) * (height - top - bottom) / (high - low);
        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        const dateIndexAt = (clientX) => {
            const bounds = svg.getBoundingClientRect();
            const chartX = (clientX - bounds.left) * width / bounds.width;
            return clamp(Math.round(viewStart + (chartX - left) * (viewEnd - viewStart) / plotWidth), viewStart, viewEnd);
        };
        const zoom = (factor, anchorIndex = (viewStart + viewEnd) / 2) => {
            const currentCount = viewEnd - viewStart + 1;
            const nextCount = clamp(Math.round(currentCount * factor), 3, days.length);
            if (nextCount === currentCount) return;
            const relative = (anchorIndex - viewStart) / Math.max(1, currentCount - 1);
            const nextStart = clamp(Math.round(anchorIndex - relative * (nextCount - 1)), 0, days.length - nextCount);
            renderHistory(container, days, nextStart, nextStart + nextCount - 1);
        };
        const controls = document.createElement('div');
        controls.className = 'spp-history__controls';
        const range = document.createElement('span');
        range.textContent = `${displayDate(days[viewStart].date)} — ${displayDate(days[viewEnd].date)}`;
        const buttons = document.createElement('div');
        buttons.className = 'spp-history__zoom-buttons';
        for (const [label, action, disabled] of [
            ['−', () => zoom(1.5), viewStart === 0 && viewEnd === days.length - 1],
            ['+', () => zoom(0.67), viewEnd - viewStart < 3],
            ['Весь период', () => renderHistory(container, days), viewStart === 0 && viewEnd === days.length - 1],
        ]) {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = label;
            button.title = label === '−' ? 'Уменьшить масштаб' : label === '+' ? 'Увеличить масштаб' : label;
            button.disabled = disabled;
            button.addEventListener('click', action);
            buttons.append(button);
        }
        controls.append(range, buttons);
        container.append(controls);
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', 'История СПП по дням. Используйте стрелки для просмотра значений.');
        svg.setAttribute('tabindex', '0');
        svg.classList.add('spp-history__chart');
        const frame = document.createElement('div');
        frame.className = 'spp-history__frame';
        const scroll = document.createElement('div');
        scroll.className = 'spp-history__scroll';
        const tooltip = document.createElement('div');
        tooltip.className = 'spp-history__tooltip';
        tooltip.hidden = true;
        tooltip.setAttribute('role', 'tooltip');
        const guide = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        const highlight = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        guide.setAttribute('class', 'spp-history__guide');
        highlight.setAttribute('class', 'spp-history__highlight');
        guide.style.display = 'none';
        highlight.style.display = 'none';
        const hideTooltip = () => {
            tooltip.hidden = true;
            guide.style.display = 'none';
            highlight.style.display = 'none';
        };
        let selectedIndex = viewEnd;
        while (selectedIndex > viewStart && days[selectedIndex].average_spp === null) selectedIndex -= 1;
        const showTooltip = (index) => {
            const day = days[index];
            selectedIndex = index;
            tooltip.textContent = day.average_spp === null
                ? `${displayDate(day.date)} · нет замеров СПП`
                : `${displayDate(day.date)} · ${number.format(day.average_spp)}% · ${observationCount(day.observations)}`;
            tooltip.hidden = false;
            const frameBounds = frame.getBoundingClientRect();
            const chartBounds = svg.getBoundingClientRect();
            const pointX = chartBounds.left + x(index) * chartBounds.width / width - frameBounds.left;
            const pointY = chartBounds.top + y(day.average_spp === null ? (low + high) / 2 : day.average_spp) * chartBounds.height / height - frameBounds.top;
            const half = tooltip.offsetWidth / 2;
            tooltip.style.left = `${Math.max(half + 8, Math.min(frameBounds.width - half - 8, pointX))}px`;
            const above = pointY >= tooltip.offsetHeight + 14;
            tooltip.style.top = `${above ? pointY - 9 : pointY + 9}px`;
            tooltip.style.transform = above ? 'translate(-50%, -100%)' : 'translateX(-50%)';
            guide.setAttribute('x1', x(index));
            guide.setAttribute('x2', x(index));
            guide.setAttribute('y1', top);
            guide.setAttribute('y2', height - bottom);
            guide.style.display = '';
            highlight.style.display = day.average_spp === null ? 'none' : '';
            if (day.average_spp !== null) {
                highlight.setAttribute('cx', x(index));
                highlight.setAttribute('cy', y(day.average_spp));
            }
        };
        const element = (name, attributes, textValue) => {
            const node = document.createElementNS('http://www.w3.org/2000/svg', name);
            Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
            if (textValue !== undefined) node.textContent = textValue;
            svg.append(node);
            return node;
        };
        element('rect', { x: left, y: top, width: width - left - right, height: height - top - bottom, class: 'spp-history__hit-area' });
        element('rect', { x: left, y: height - bottom, width: width - left - right, height: bottom, class: 'spp-history__axis-hit' });
        [low, (low + high) / 2, high].forEach((value) => {
            element('line', { x1: left, x2: width - right, y1: y(value), y2: y(value), class: 'spp-history__grid' });
            element('text', { x: left - 8, y: y(value) + 4, 'text-anchor': 'end', class: 'spp-history__label' }, number.format(value));
        });
        let segment = [];
        const drawSegment = () => {
            if (segment.length > 1) element('polyline', { points: segment.join(' '), class: 'spp-history__line' });
            segment = [];
        };
        visibleDays.forEach((day, offset) => {
            const index = viewStart + offset;
            if (day.average_spp === null) drawSegment();
            else segment.push(`${x(index)},${y(day.average_spp)}`);
        });
        drawSegment();
        visibleDays.forEach((day, offset) => {
            const index = viewStart + offset;
            if (day.average_spp === null) return;
            element('circle', { cx: x(index), cy: y(day.average_spp), r: visibleDays.length > 60 ? 2.5 : 4, class: 'spp-history__dot' });
        });
        svg.append(guide, highlight);
        svg.addEventListener('pointermove', (event) => {
            if (container.classList.contains('spp-history--dragging')) return;
            const index = dateIndexAt(event.clientX);
            if (index !== selectedIndex || tooltip.hidden) showTooltip(index);
        });
        svg.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            if (event.target.classList.contains('spp-history__axis-hit')) {
                event.preventDefault();
                const initialX = event.clientX;
                const initialCount = viewEnd - viewStart + 1;
                const center = (viewStart + viewEnd) / 2;
                let lastStart = viewStart;
                let lastEnd = viewEnd;
                const onMove = (moveEvent) => {
                    if (moveEvent.pointerId !== event.pointerId) return;
                    const count = clamp(Math.round(initialCount * Math.exp((initialX - moveEvent.clientX) / 300)), 3, days.length);
                    const start = clamp(Math.round(center - (count - 1) / 2), 0, days.length - count);
                    if (start !== lastStart || start + count - 1 !== lastEnd) {
                        lastStart = start;
                        lastEnd = start + count - 1;
                        renderHistory(container, days, start, start + count - 1);
                    }
                };
                const onEnd = (endEvent) => {
                    if (endEvent.pointerId !== event.pointerId) return;
                    window.removeEventListener('pointermove', onMove);
                    window.removeEventListener('pointerup', onEnd);
                    window.removeEventListener('pointercancel', onEnd);
                };
                window.addEventListener('pointermove', onMove);
                window.addEventListener('pointerup', onEnd);
                window.addEventListener('pointercancel', onEnd);
                hideTooltip();
                return;
            }
            event.preventDefault();
            const initialX = event.clientX;
            const count = viewEnd - viewStart + 1;
            const chartWidth = plotWidth * svg.getBoundingClientRect().width / width;
            let lastStart = viewStart;
            let moved = false;
            const onMove = (moveEvent) => {
                if (moveEvent.pointerId !== event.pointerId) return;
                const offset = Math.round((moveEvent.clientX - initialX) * (count - 1) / chartWidth);
                const start = clamp(viewStart - offset, 0, days.length - count);
                if (start !== lastStart) {
                    moved = true;
                    lastStart = start;
                    renderHistory(container, days, start, start + count - 1);
                }
            };
            const onEnd = (endEvent) => {
                if (endEvent.pointerId !== event.pointerId) return;
                window.removeEventListener('pointermove', onMove);
                window.removeEventListener('pointerup', onEnd);
                window.removeEventListener('pointercancel', onEnd);
                container.classList.remove('spp-history--dragging');
                if (!moved && endEvent.type === 'pointerup' && Math.abs(endEvent.clientX - initialX) < 8) {
                    showTooltip(dateIndexAt(endEvent.clientX));
                }
            };
            container.classList.add('spp-history--dragging');
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onEnd);
            window.addEventListener('pointercancel', onEnd);
            hideTooltip();
        });
        svg.addEventListener('wheel', (event) => {
            event.preventDefault();
            zoom(event.deltaY < 0 ? 0.8 : 1.25);
        }, { passive: false });
        svg.addEventListener('pointerleave', hideTooltip);
        svg.addEventListener('focus', () => showTooltip(selectedIndex));
        svg.addEventListener('blur', hideTooltip);
        svg.addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const index = event.key === 'Home' ? viewStart : event.key === 'End' ? viewEnd
                : selectedIndex + (event.key === 'ArrowLeft' ? -1 : 1);
            showTooltip(clamp(index, viewStart, viewEnd));
        });
        const labelInterval = visibleDays.length <= 7 ? 1 : visibleDays.length <= 30 ? 5
            : Math.ceil(visibleDays.length / Math.max(2, Math.floor(plotWidth / 105)));
        visibleDays.forEach((day, offset) => {
            if (offset % labelInterval !== 0 && offset !== visibleDays.length - 1) return;
            if (offset !== visibleDays.length - 1 && offset !== 0 && x(viewEnd) - x(viewStart + offset) < 85) return;
            element('text', { x: x(viewStart + offset), y: height - 8, 'text-anchor': offset === 0 ? 'start' : offset === visibleDays.length - 1 ? 'end' : 'middle', class: 'spp-history__label' }, displayDate(day.date));
        });
        scroll.append(svg);
        frame.append(scroll, tooltip);
        container.append(frame);
        const note = document.createElement('p');
        note.className = 'spp-history__note';
        note.textContent = `Точки: среднее арифметическое всех замеров за день по Москве. Дней с данными: ${visibleDays.filter((day) => day.average_spp !== null).length} из ${visibleDays.length}. Потяните ось дат для масштаба. Зажмите область графика и двигайте мышь, чтобы просматривать даты.`;
        container.append(note);
    }

    function addHistory(row, product) {
        const articleCell = row.cells[0];
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'spp-history-toggle';
        button.textContent = '▸';
        button.setAttribute('aria-label', `Показать график СПП по дням для артикула ${product.article}`);
        button.setAttribute('aria-expanded', 'false');
        articleCell.prepend(button);
        const detailRow = document.createElement('tr');
        detailRow.className = 'spp-history-row';
        detailRow.hidden = true;
        const detailCell = document.createElement('td');
        detailCell.colSpan = 6;
        detailCell.className = 'spp-history';
        detailRow.append(detailCell);
        row.after(detailRow);
        const header = document.createElement('div');
        header.className = 'spp-history__header';
        const heading = document.createElement('strong');
        heading.textContent = `История СПП · ${product.article}`;
        const periodButtons = document.createElement('div');
        periodButtons.className = 'spp-history__periods';
        periodButtons.setAttribute('role', 'group');
        periodButtons.setAttribute('aria-label', 'Период истории СПП');
        const chart = document.createElement('div');
        chart.className = 'spp-history__body';
        header.append(heading, periodButtons);
        detailCell.append(header, chart);
        const cache = new Map();
        let selectedPeriod = '7d';
        let requestId = 0;
        async function selectPeriod(value) {
            selectedPeriod = value;
            periodButtons.querySelectorAll('button').forEach((option) => {
                option.setAttribute('aria-pressed', String(option.dataset.period === value));
            });
            const sequence = ++requestId;
            if (cache.has(value)) {
                renderHistory(chart, cache.get(value));
                return;
            }
            chart.textContent = 'Загрузка графика…';
            const params = new URLSearchParams({ store: product.store_slug, article: product.article, period: value });
            try {
                const response = await fetch(`/api/unit-economics-1c/reports/spp/history?${params}`);
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить историю СПП');
                cache.set(value, data.days);
                if (detailRow.isConnected && sequence === requestId) renderHistory(chart, data.days);
            } catch (error) {
                if (detailRow.isConnected && sequence === requestId) chart.textContent = error.message;
            }
        }
        historyPeriods.forEach((period) => {
            const option = document.createElement('button');
            option.type = 'button';
            option.dataset.period = period.value;
            option.textContent = period.label;
            option.setAttribute('aria-pressed', String(period.value === selectedPeriod));
            option.addEventListener('click', () => selectPeriod(period.value));
            periodButtons.append(option);
        });
        button.addEventListener('click', () => {
            const opening = detailRow.hidden;
            detailRow.hidden = !opening;
            button.setAttribute('aria-expanded', String(opening));
            button.textContent = opening ? '▾' : '▸';
            if (opening && !chart.hasChildNodes()) selectPeriod(selectedPeriod);
        });
    }

    function render(data) {
        sortBy = data.sort_by;
        sortOrder = data.sort_order;
        root.querySelectorAll('[data-spp-sort]').forEach((button) => {
            const active = button.dataset.sppSort === sortBy;
            button.closest('th').setAttribute('aria-sort', active ? (sortOrder === 'desc' ? 'descending' : 'ascending') : 'none');
        });
        const options = data.options;
        setOptions(byId('spp-brand'), options.brands);
        setOptions(byId('spp-manager'), options.managers);
        setOptions(byId('spp-subject'), options.subjects);
        setOptions(byId('spp-article'), options.articles);

        const summary = data.summary;
        const comparable = summary.comparable;
        const badPercent = comparable ? Math.round(summary.problem / comparable * 100) : null;
        const goodPercent = summary.health_percent;
        byId('spp-problem-percent').textContent = badPercent === null ? '—' : `${badPercent}%`;
        byId('spp-healthy-percent').textContent = goodPercent === null ? '—' : `${goodPercent}%`;
        byId('spp-problem-count').textContent = `${summary.problem} из ${comparable}`;
        byId('spp-healthy-count').textContent = `${summary.healthy} из ${comparable}`;
        byId('spp-bar-bad').style.width = `${badPercent || 0}%`;
        byId('spp-bar-good').style.width = `${goodPercent || 0}%`;
        byId('spp-overall').textContent = goodPercent === null ? 'Нет данных' : `${goodPercent}%`;
        byId('spp-missing').textContent = summary.no_data ? `Без сравнения: ${summary.no_data} товаров` : '';
        byId('spp-period-note').textContent = `Сравнение: ${displayDate(data.period.previous_from)} — ${displayDate(data.period.previous_to)}. Торговый оборот за ${displayDate(data.period.date_from)} — ${displayDate(data.period.date_to)}. Допустимое снижение: ${number.format(Number(toleranceValue.value))} п. п.`;

        const tbody = byId('spp-rows');
        tbody.replaceChildren();
        data.rows.forEach((product) => {
            const row = document.createElement('tr');
            cell(row, product.article);
            cell(row, product.name);
            cell(row, product.turnover === null ? '—' : money.format(product.turnover));
            cell(row, product.current_spp === null ? '—' : `${number.format(product.current_spp)}%`);
            cell(row, product.previous_spp === null ? '—' : `${number.format(product.previous_spp)}%`);
            const status = cell(row, '');
            const badge = document.createElement('span');
            badge.className = `spp-status spp-status--${product.status}`;
            badge.textContent = { problem: 'Требует внимания', healthy: 'Без проблем', no_data: 'Нет данных' }[product.status];
            status.append(badge);
            tbody.append(row);
            addHistory(row, product);
        });
        byId('spp-message').textContent = data.total ? '' : 'По выбранным фильтрам товаров нет';
        byId('spp-page-label').textContent = `Страница ${data.page} из ${Math.max(1, Math.ceil(data.total / data.page_size))}`;
        byId('spp-prev').disabled = data.page <= 1;
        byId('spp-next').disabled = data.page * data.page_size >= data.total;
    }

    async function load() {
        const sequence = ++requestNumber;
        const from = isoDate(dateFrom.value);
        const to = isoDate(dateTo.value);
        if (!from || !to) {
            byId('spp-message').textContent = 'Введите даты в формате ДД.ММ.ГГГГ';
            return;
        }
        if (from > to) {
            byId('spp-message').textContent = 'Дата «От» должна быть не позже даты «До»';
            return;
        }
        const params = new URLSearchParams(new FormData(form));
        params.set('date_from', from);
        params.set('date_to', to);
        params.set('page', String(currentPage));
        params.set('sort_by', sortBy);
        params.set('sort_order', sortOrder);
        byId('spp-message').textContent = 'Загрузка…';
        try {
            const response = await fetch(`/api/unit-economics-1c/reports/spp?${params}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить отчёт');
            if (sequence === requestNumber) {
                sessionStorage.setItem(storageKey, JSON.stringify({ from, to }));
                render(data);
            }
        } catch (error) {
            if (sequence === requestNumber) byId('spp-message').textContent = error.message;
        }
    }

    form.addEventListener('submit', (event) => { event.preventDefault(); currentPage = 1; load(); });
    root.querySelectorAll('[data-spp-sort]').forEach((button) => {
        button.addEventListener('click', () => {
            const field = button.dataset.sppSort;
            sortOrder = field === sortBy && sortOrder === 'desc' ? 'asc' : 'desc';
            sortBy = field;
            currentPage = 1;
            load();
        });
    });
    byId('spp-prev').addEventListener('click', () => { currentPage -= 1; load(); });
    byId('spp-next').addEventListener('click', () => { currentPage += 1; load(); });
    load();
})();
