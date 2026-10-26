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
        if (session) session.controller.abort();
        sessions.delete(root);
    }

    async function render(root, product, display) {
        const previous = sessions.get(root);
        if (previous?.productId === product.id) {
            previous.display = display;
            if (previous.rows) display(previous.rows);
            return;
        }
        clear(root);
        const session = { productId: product.id, controller: new AbortController(), display };
        sessions.set(root, session);
        let status = root.querySelector('[data-ozon-history-status]');
        if (!status) {
            status = document.createElement('p');
            status.className = 'ue1c-calculator-note';
            status.dataset.ozonHistoryStatus = '';
            status.setAttribute('role', 'status');
            root.querySelector('#ue1c-chart-wrap').after(status);
        }
        status.replaceChildren();
        status.hidden = true;
        display([], 'Загружаем динамику Ozon…');
        try {
            const query = new URLSearchParams({ store: product.store_slug, article: product.article });
            const response = await fetch('/api/unit-economics-1c/ozon/history?' + query, {
                signal: session.controller.signal, headers: { Accept: 'application/json' },
            });
            if (!response.ok) throw new Error('History unavailable');
            const result = await response.json();
            if (!Array.isArray(result.days)) throw new Error('Invalid history');
            if (sessions.get(root) !== session) return;
            session.rows = result.days.map(row => ({ ...row, label: dateLabel(row.date) }));
            session.display(session.rows);
        } catch (error) {
            if (sessions.get(root) !== session || error.name === 'AbortError') return;
            session.display([], 'Не удалось загрузить динамику Ozon.');
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'ue1c-calculator-reset';
            retry.textContent = 'Повторить загрузку графика';
            retry.onclick = () => { clear(root); render(root, product, display); };
            status.replaceChildren(retry);
            status.hidden = false;
        }
    }

    return { render, clear, note };
})();
