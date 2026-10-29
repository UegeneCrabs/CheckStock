window.OzonPromotionsTimeline = (() => {
    'use strict';
    const sessions = new WeakMap();
    const dayMilliseconds = 86400000;
    const partsFormat = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    });
    const shortDates = new Intl.DateTimeFormat('ru-RU', {
        timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
    });
    const axisDates = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', day: '2-digit', month: 'short' });
    const axisMonths = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', month: 'short', year: 'numeric' });
    const axisYears = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', year: 'numeric' });
    const rangeDates = new Intl.DateTimeFormat('ru-RU', { timeZone: 'UTC', day: '2-digit', month: '2-digit', year: 'numeric' });
    const statuses = {
        participating: 'Товар участвует', candidate: 'Доступна для товара', unknown: 'Участие не проверено',
    };

    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }

    function parsed(value) {
        if (!value) return null;
        const date = new Date(value);
        return Number.isFinite(date.getTime()) ? date : null;
    }

    function point(value) {
        const date = parsed(value);
        if (!date) return null;
        const parts = {};
        partsFormat.formatToParts(date).forEach(part => { if (part.type !== 'literal') parts[part.type] = Number(part.value); });
        return Date.UTC(parts.year, parts.month - 1, parts.day) / dayMilliseconds +
            (parts.hour * 3600 + parts.minute * 60 + parts.second) / 86400;
    }

    function period(action) {
        const start = parsed(action.date_start);
        const end = parsed(action.date_end);
        if (start && end) return shortDates.format(start) + ' — ' + shortDates.format(end);
        if (start) return 'С ' + shortDates.format(start) + ' · окончание не указано';
        if (end) return 'До ' + shortDates.format(end) + ' · начало не указано';
        return 'Сроки не указаны';
    }

    function defaultRange() {
        const today = Math.floor(point(new Date()));
        return { start: today - 7, end: today + 35 };
    }

    function fullRange(items) {
        const today = Math.floor(point(new Date()));
        let start = today, end = today + 1;
        items.forEach(item => {
            const first = point(item.action.date_start), last = point(item.action.date_end);
            if (first !== null && last !== null && last < first) return;
            [first, last].forEach(value => {
                if (value !== null) { start = Math.min(start, Math.floor(value)); end = Math.max(end, Math.ceil(value)); }
            });
        });
        return { start: start - 2, end: Math.max(end + 2, start + 5) };
    }

    function render(root, container, items) {
        let session = sessions.get(root);
        if (!session) {
            session = { ...defaultRange(), mode: 'today' };
            sessions.set(root, session);
        }
        session.container = container;
        session.items = items;
        if (session.mode === 'all') Object.assign(session, fullRange(items));
        container.hidden = !items.length;
        draw(root, session);
    }

    function button(root, session, text, label, change) {
        const node = element('button', text, 'ozon-gantt-control');
        node.type = 'button';
        node.setAttribute('aria-label', label);
        node.onclick = () => {
            if (sessions.get(root) !== session) return;
            change();
            draw(root, session);
        };
        return node;
    }

    function draw(root, session) {
        const { container, start, end } = session;
        const span = end - start;
        const today = point(new Date());
        const position = value => Math.max(0, Math.min(100, (value - start) / span * 100));
        const heading = element('div', undefined, 'ozon-gantt-heading');
        heading.append(element('h4', 'Календарь акций'));
        const controls = element('div', undefined, 'ozon-gantt-controls');
        controls.append(
            button(root, session, '←', 'Предыдущий период', () => {
                session.start -= span; session.end -= span; session.mode = 'custom';
            }),
            button(root, session, 'Сегодня', 'Показать период вокруг сегодня', () => {
                Object.assign(session, defaultRange()); session.mode = 'today';
            }),
            button(root, session, '→', 'Следующий период', () => {
                session.start += span; session.end += span; session.mode = 'custom';
            }),
            button(root, session, 'Все даты', 'Показать все известные даты акций', () => {
                Object.assign(session, fullRange(session.items)); session.mode = 'all';
            }),
        );
        heading.append(controls);
        const range = element('p', rangeDates.format(new Date(start * dayMilliseconds)) + ' — ' +
            rangeDates.format(new Date((end - 1) * dayMilliseconds)) + ' · МСК', 'ozon-gantt-range');
        const legend = element('div', undefined, 'ozon-gantt-legend');
        Object.entries(statuses).forEach(([status, label]) => legend.append(element('span', label, 'is-' + status)));
        const scroll = element('div', undefined, 'ozon-gantt-scroll');
        const grid = element('div', undefined, 'ozon-gantt-grid');
        grid.setAttribute('role', 'table');
        grid.setAttribute('aria-label', 'Диаграмма Ганта акций, даты по Москве');
        const header = element('div', undefined, 'ozon-gantt-row ozon-gantt-row--axis');
        header.setAttribute('role', 'row');
        const title = element('div', 'Акция', 'ozon-gantt-name');
        title.setAttribute('role', 'columnheader');
        const axis = element('div', undefined, 'ozon-gantt-axis');
        axis.setAttribute('role', 'columnheader');
        axis.setAttribute('aria-label', 'Шкала дат');
        const format = span > 3650 ? axisYears : span > 180 ? axisMonths : axisDates;
        for (let index = 0; index <= 6; index++) {
            const tick = element('span', format.format(new Date(Math.floor(start + span * index / 6) * dayMilliseconds)), 'ozon-gantt-tick');
            tick.style.left = index / 6 * 100 + '%';
            axis.append(tick);
        }
        header.append(title, axis);
        grid.append(header);
        const items = session.items.map((item, index) => ({ item, index, start: point(item.action.date_start), end: point(item.action.date_end) }));
        items.sort((a, b) => (a.start ?? a.end ?? Infinity) - (b.start ?? b.end ?? Infinity) || a.index - b.index);
        items.forEach(value => {
            const { item } = value;
            const row = element('div', undefined, 'ozon-gantt-row');
            row.setAttribute('role', 'row');
            const name = element('div', undefined, 'ozon-gantt-name');
            name.setAttribute('role', 'rowheader');
            name.append(element('strong', item.action.title), element('span', period(item.action)));
            const track = element('div', undefined, 'ozon-gantt-track');
            track.setAttribute('role', 'cell');
            const status = Object.hasOwn(statuses, item.status) ? item.status : 'unknown';
            const description = item.action.title + '. ' + period(item.action) + '. ' + statuses[status] + '.';
            track.setAttribute('aria-label', description);
            let message = '';
            if (value.start === null && value.end === null) message = 'Сроки не указаны';
            else if (value.start !== null && value.end !== null && value.end < value.start) message = 'Некорректные сроки';
            else if (value.end !== null && value.end < start) message = 'Раньше выбранного периода';
            else if (value.start !== null && value.start >= end) message = 'Позже выбранного периода';
            if (message) track.append(element('span', message, 'ozon-gantt-empty'));
            else {
                const left = value.start === null ? 0 : position(value.start);
                const right = value.end === null ? 100 : position(value.end);
                const bar = element('div', undefined, 'ozon-gantt-bar is-' + status);
                bar.style.left = left + '%';
                bar.style.width = Math.max(0, right - left) + '%';
                bar.classList.toggle('is-open-start', value.start === null || value.start < start);
                bar.classList.toggle('is-open-end', value.end === null || value.end > end);
                bar.title = description;
                bar.tabIndex = 0;
                bar.setAttribute('aria-label', description);
                track.append(bar);
            }
            if (today >= start && today < end) {
                const marker = element('span', undefined, 'ozon-gantt-today');
                marker.style.left = position(today) + '%';
                marker.title = 'Сегодня';
                marker.setAttribute('aria-hidden', 'true');
                track.append(marker);
            }
            row.append(name, track);
            grid.append(row);
        });
        if (today >= start && today < end) {
            const marker = element('span', 'Сегодня', 'ozon-gantt-today-label');
            marker.style.left = position(today) + '%';
            axis.append(marker);
        }
        scroll.append(grid);
        container.replaceChildren(heading, range, legend, scroll);
    }

    function clear(root) {
        sessions.delete(root);
    }

    return { render, clear, period };
})();
