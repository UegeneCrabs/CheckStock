window.OzonHistoryUI = (() => {
    'use strict';
    const sessions = new WeakMap();
    const dates = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit' });
    const dateLabel = value => dates.format(new Date(value + 'T12:00:00Z'));

    function note(row, field) {
        const buyout = row.buyout || {};
        const period = field === 'drr' ? ' · выкуп за 30 дней: ' +
            (buyout.percent == null ? 'нет снимка с рассчитанным выкупом' : buyout.percent + '%') +
            (buyout.period_from && buyout.period_to ? ' (' + dateLabel(buyout.period_from) + '–' + dateLabel(buyout.period_to) + ')' : '') : '';
        return ((row.partial_fields || []).includes(field) ? ' · неполные данные' : '') +
            ((row.stale_fields || []).includes(field) ? ' · обновление не подтверждено' : '') + period;
    }

    function clear(root) {
        const session = sessions.get(root);
        if (session) {
            session.controller?.abort();
            session.periods.remove();
            session.heading.textContent = session.originalTitle;
            session.chart.setAttribute('aria-label', session.originalChartLabel);
            session.chartWrap.removeAttribute('aria-busy');
            session.status.replaceChildren();
            session.status.hidden = true;
        }
        sessions.delete(root);
    }

    async function render(root, product, display) {
        const previous = sessions.get(root);
        if (previous?.productId === product.id) {
            previous.display = display;
            display(previous.rows || [], previous.message, previous.days);
            return;
        }
        clear(root);
        const chartWrap = root.querySelector('#ue1c-chart-wrap');
        const heading = chartWrap.closest('section').querySelector('h3');
        const chart = root.querySelector('#ue1c-chart');
        let status = root.querySelector('[data-ozon-history-status]');
        if (!status) {
            status = document.createElement('p');
            status.className = 'ue1c-calculator-note';
            status.dataset.ozonHistoryStatus = '';
            status.setAttribute('role', 'status');
            chartWrap.after(status);
        }
        const periods = document.createElement('div');
        periods.className = 'ozon-history-periods';
        periods.setAttribute('role', 'group');
        periods.setAttribute('aria-label', 'Период графика Ozon');
        const session = {
            productId: product.id, display, days: 14, cache: new Map(),
            periods, heading, originalTitle: heading.textContent, chart,
            originalChartLabel: chart.getAttribute('aria-label'), chartWrap, status,
        };
        sessions.set(root, session);
        [7, 14, 30].forEach(days => {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = days + ' дней';
            button.dataset.ozonHistoryDays = String(days);
            button.onclick = () => {
                if (session.days === days) return;
                session.days = days;
                load(root, product, session);
            };
            periods.append(button);
        });
        heading.after(periods);
        return load(root, product, session);
    }

    async function load(root, product, session) {
        session.controller?.abort();
        const controller = new AbortController();
        session.controller = controller;
        const { days, status } = session;
        session.periods.querySelectorAll('button').forEach(button => {
            button.setAttribute('aria-pressed', String(Number(button.dataset.ozonHistoryDays) === days));
        });
        session.heading.textContent = 'Динамика за ' + days + ' дней';
        session.chart.setAttribute('aria-label', 'Динамика товара за ' + days + ' дней');
        status.replaceChildren();
        status.hidden = true;
        session.rows = session.cache.get(days);
        session.message = session.rows ? undefined : 'Загружаем динамику Ozon…';
        session.display(session.rows || [], session.message, days);
        session.chartWrap.setAttribute('aria-busy', String(!session.rows));
        if (session.rows) return;
        try {
            const query = new URLSearchParams({ store: product.store_slug, article: product.article, days });
            const response = await fetch('/api/unit-economics-1c/ozon/history?' + query, {
                signal: controller.signal, headers: { Accept: 'application/json' },
            });
            if (!response.ok) throw new Error('History unavailable');
            const result = await response.json();
            if (!Array.isArray(result.days)) throw new Error('Invalid history');
            if (sessions.get(root) !== session || session.controller !== controller) return;
            session.rows = result.days.map(row => ({ ...row, label: dateLabel(row.date) }));
            session.cache.set(days, session.rows);
            session.message = undefined;
            session.display(session.rows, undefined, days);
        } catch (error) {
            if (sessions.get(root) !== session || session.controller !== controller || error.name === 'AbortError') return;
            session.message = 'Не удалось загрузить динамику Ozon.';
            session.display([], session.message, days);
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'ue1c-calculator-reset';
            retry.textContent = 'Повторить загрузку графика';
            retry.onclick = () => load(root, product, session);
            status.replaceChildren(retry);
            status.hidden = false;
        } finally {
            if (sessions.get(root) === session && session.controller === controller) {
                session.chartWrap.setAttribute('aria-busy', 'false');
            }
        }
    }

    return { render, clear, note };
})();
