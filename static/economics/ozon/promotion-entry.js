window.OzonPromotionEntryUI = (() => {
    const sessions = new WeakMap();
    const base = '/api/unit-economics-1c/ozon/promotions/entry';
    const pending = new Set(['checking', 'sending', 'verifying', 'uncertain']);
    const states = {
        prepared: 'Условия проверены. Подтвердите отправку цены.', blocked: 'Вступление недоступно.',
        checking: 'Повторно проверяем условия перед отправкой…', sending: 'Отправляем цену в Ozon…',
        verifying: 'Проверяем цену и участие в Ozon…', uncertain: 'Результат отправки пока не подтверждён.',
        succeeded: 'Участие и цена подтверждены Ozon.', rejected: 'Ozon отклонил вступление.',
        changed: 'Цена или условия изменились. Нужна новая проверка.', expired: 'Срок проверки истёк.',
        already_participating: 'Товар уже участвует в этой акции.', failed: 'Операция не отправлена.',
    };
    const issues = {
        action_unavailable: 'Акция больше не доступна для товара.', not_candidate: 'Товар не входит в список кандидатов.',
        incomplete: 'Недостаточно подтверждённых данных Ozon для вступления.', stock_unknown: 'Не удалось подтвердить остатки.',
        no_stock: 'У товара нет доступных остатков.', quarantined: 'Товар в карантине или архиве.',
        frozen: 'Изменение участия уже заморожено.', ended: 'Акция завершилась.',
        price_unavailable: 'Не удалось получить фактическую предельную цену.', currency: 'Валюта цены не соответствует условиям.',
        price_limit: 'Цена выше допустимого предела акции.', stock_required: 'Для акции-промокода укажите количество.',
        stock_limit: 'Количество меньше минимального для акции.', stock_unsupported: 'Для этой акции количество не передаётся.',
        access_denied: 'Ключ Seller API не разрешает эту операцию.', unavailable: 'Не удалось проверить данные Ozon.',
        busy: 'Для товара уже выполняется операция. Проверьте журнал.', expired: 'Повторите проверку условий.',
        changed: 'Проверьте обновлённые данные перед новым подтверждением.', rejected: 'Изменение не принято Ozon.',
        write_uncertain: 'Повторная отправка заблокирована. Доступна только проверка результата.',
        verify_failed: 'Цена или участие ещё не подтверждены. Повторная запись не выполняется.',
        interrupted: 'Проверка прервана до отправки. Можно начать заново.',
    };
    const dates = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Moscow' });
    const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    function node(tag, text, className) {
        const item = document.createElement(tag);
        if (text !== undefined) item.textContent = text;
        if (className) item.className = className;
        return item;
    }
    function button(text, action) {
        const item = node('button', text, 'ue1c-calculator-reset');
        item.type = 'button'; item.onclick = action;
        return item;
    }
    function money(value) {
        return value?.amount == null ? '—' : numbers.format(Number(value.amount)) + ' ' + value.currency;
    }
    function clear(root) {
        const session = sessions.get(root);
        if (!session) return;
        session.controller.abort();
        window.clearTimeout(session.timer);
        if (session.dialog?.open) session.dialog.close();
        session.dialog?.remove();
        sessions.delete(root);
    }
    function render(root, product, completed) {
        const key = product.store_slug + ':' + product.article;
        if (sessions.get(root)?.key === key) {
            Object.assign(sessions.get(root), { product, completed });
            return;
        }
        clear(root);
        sessions.set(root, { key, product, completed, controller: new AbortController(), applied: new Set(), operations: new Map() });
    }
    async function request(url, payload, signal) {
        let response;
        let result;
        try {
            response = await fetch(url, {
                method: payload === undefined ? 'GET' : 'POST', signal,
                headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: payload === undefined ? undefined : JSON.stringify(payload),
            });
            result = await response.json();
        } catch (error) {
            if (error.name === 'AbortError') throw error;
            throw new Error('Не удалось получить ответ сервера. Проверьте результат в журнале операции.');
        }
        if (!response.ok || response.redirected) {
            const text = response.status === 409 ? 'Для товара уже выполняется операция. Проверьте журнал.' :
                response.status === 403 ? 'Нет доступа к кабинету.' : response.status === 422 ?
                    'Проверьте цену, количество и параметры калькулятора.' : 'Не удалось выполнить запрос. Обновите журнал операции.';
            throw new Error(text);
        }
        return result;
    }
    function path(operation, suffix) {
        return base + '/' + encodeURIComponent(operation.id) + '/' + suffix + '?store=' + encodeURIComponent(operation.store);
    }
    function remember(root, session, operation, apply = false) {
        if (sessions.get(root) !== session) return;
        const previous = session.operations.get(operation.id);
        if (previous && Date.parse(previous.updated_at) > Date.parse(operation.updated_at)) return;
        session.operations.set(operation.id, operation);
        historyRows(root, session);
        if (apply && operation.state === 'succeeded' && !session.applied.has(operation.id)) {
            session.applied.add(operation.id);
            session.completed(operation);
        }
    }
    function historyRows(root, session) {
        if (!session.history) return;
        const operations = [...session.operations.values()].sort((a, b) =>
            Number(pending.has(b.state)) - Number(pending.has(a.state)) || b.created_at.localeCompare(a.created_at)).slice(0, 10);
        session.history.replaceChildren(node('h4', 'Журнал вступления'));
        if (!operations.length) session.history.append(node('p', 'Операций пока нет.', 'ozon-promotions-note'));
        operations.forEach(operation => {
            const item = node('div', undefined, 'ozon-entry-history-row');
            const action = operation.evidence?.promotions.items.find(item => item.action.id === operation.action_id)?.action;
            item.append(node('span', dates.format(new Date(operation.created_at)) + ' · ' + (action?.title || 'Акция ' + operation.action_id) + ' · ' + money(operation.price)),
                button(states[operation.state] || operation.state, () => show(root, session, operation)));
            session.history.append(item);
        });
    }
    async function history(root, container) {
        const session = sessions.get(root);
        if (!session) return;
        const box = node('section', undefined, 'ozon-entry-history');
        container.append(box); session.history = box;
        box.append(node('p', 'Загружаем журнал…'));
        try {
            const query = new URLSearchParams({ store: session.product.store_slug, article: session.product.article });
            const result = await request(base + '?' + query, undefined, session.controller.signal);
            if (sessions.get(root) !== session) return;
            result.items.forEach(item => {
                const previous = session.operations.get(item.id);
                if (!previous || Date.parse(previous.updated_at) <= Date.parse(item.updated_at)) session.operations.set(item.id, item);
            });
            historyRows(root, session);
        } catch (error) {
            if (sessions.get(root) !== session || error.name === 'AbortError') return;
            box.replaceChildren(node('p', error.message), button('Обновить журнал', () => { box.remove(); history(root, container); }));
        }
    }
    function bind(root, identity, row, context) {
        if (row.item.status !== 'candidate') return;
        identity.append(button('Вступить', () => {
            const session = sessions.get(root);
            const calculation = context();
            if (!session) return;
            if (!calculation || !row.input.checkValidity() || Number(row.input.value) <= 0) {
                row.note.hidden = false;
                row.note.textContent = 'Укажите положительную цену и дождитесь параметров калькулятора.';
                return;
            }
            const stock = node('input');
            stock.type = 'number'; stock.min = String(Math.max(1, row.item.product.min_stock || 1)); stock.step = '1';
            stock.setAttribute('aria-label', 'Количество для акции-промокода');
            const voucher = row.item.action.is_voucher_action === true;
            if (voucher) stock.value = stock.min;
            const draft = { row, context, stock, voucher };
            preview(root, session, draft);
        }));
    }
    function dialog(session) {
        if (session.dialog?.open) session.dialog.close();
        session.dialog?.remove();
        window.clearTimeout(session.timer);
        const box = node('dialog', undefined, 'ozon-entry-dialog');
        box.setAttribute('aria-labelledby', 'ozon-entry-title');
        box.addEventListener('close', () => window.clearTimeout(session.timer));
        document.body.append(box); session.dialog = box;
        return box;
    }
    async function preview(root, session, draft) {
        const box = dialog(session);
        box.append(node('p', 'Проверяем свежие условия, цену и остатки Ozon…'), button('Закрыть', () => box.close()));
        box.showModal();
        try {
            const context = draft.context();
            if (!context) throw new Error('Параметры калькулятора ещё не готовы.');
            const operation = await request(base + '/preview', {
                ...context, store: session.product.store_slug, article: session.product.article,
                action_id: draft.row.item.action.id, price: draft.row.input.value, currency: draft.row.currency,
                stock: draft.voucher && draft.stock.value ? Number(draft.stock.value) : null,
            }, session.controller.signal);
            if (sessions.get(root) !== session || session.dialog !== box || !box.open) return;
            remember(root, session, operation);
            details(root, session, box, operation, draft);
        } catch (error) {
            if (sessions.get(root) !== session || session.dialog !== box || error.name === 'AbortError') return;
            box.replaceChildren(node('p', error.message), button('Закрыть', () => box.close()));
        }
    }
    function details(root, session, box, operation, draft) {
        const evidence = operation.observed || operation.evidence;
        const item = evidence?.promotions.items.find(item => item.action.id === operation.action_id);
        const title = node('h3', 'Вступление в акцию'); title.id = 'ozon-entry-title';
        const status = node('p', states[operation.state] || operation.state, 'ozon-entry-status');
        status.setAttribute('role', 'status');
        box.replaceChildren(title, node('p', (session.product.store_name || operation.store) + ' · ' + operation.article + ' · ' + (evidence?.identity.name || session.product.name || '')));
        if (item) {
            box.append(node('h4', item.action.title), node('p', window.OzonPromotionsTimeline.period(item.action)));
            if (item.product) {
                box.append(node('p', 'Предел цены акции: ' + money(item.product.max_action_price) +
                    (item.product.price_min_elastic ? ' · Для минимального бустинга: ' + money(item.product.price_min_elastic) : '')));
                if (item.action.is_voucher_action) box.append(node('p', 'Минимальное количество: ' + (item.product.min_stock ?? 'неизвестно')));
            }
            if (item.action.freeze_date) box.append(node('p', 'Ограничение изменений с ' + dates.format(new Date(item.action.freeze_date)) + ' МСК'));
        }
        box.append(node('p', (operation.observed ? 'Цена на момент проверки: ' : 'Фактическая предельная цена: ') + money(operation.current_price)),
            node('p', 'Новая цена для отправки: ' + money(operation.price), 'ozon-entry-new-price'));
        if (operation.observed?.prices.price) {
            const actual = operation.observed.prices.price;
            box.append(node('p', 'Предельная цена при последней сверке: ' + money({amount:actual.price, currency:actual.currency_code})));
        }
        if (operation.stock != null) box.append(node('p', 'Количество в акции: ' + operation.stock));
        const result = operation.calculation;
        if (result) {
            const metrics = node('div', undefined, 'ozon-entry-metrics');
            [['Маржа', result.result.margin, ' ₽'], ['ROI', result.result.roi, '%'], ['Бустинг', result.boost_percent, '%']].forEach(([label, value, suffix]) => {
                metrics.append(node('span', label + ': ' + (value == null ? '—' : numbers.format(Number(value)) + suffix),
                    value == null ? '' : Number(value) > 0 ? 'is-positive' : 'is-negative'));
            });
            box.append(metrics);
            if (result.result.missing?.length) box.append(node('p', 'Расчёт неполный: ' + result.result.missing.map(key => window.OzonEconomicsUI?.marginLabels?.[key] || key).join(', ') + '.', 'ozon-promotions-warning'));
        }
        if (item?.action.is_voucher_action !== true) {
            box.append(node('p', 'Ozon изменит единую предельную цену товара сразу. Это может изменить участие и бустинг в других акциях. После окончания акции прежняя цена автоматически не восстановится.', 'ozon-promotions-warning'));
            const other = evidence?.promotions.items.filter(item => item.action.id !== operation.action_id) || [];
            other.forEach(item => {
                const limit = item.product?.price_min_elastic || item.product?.max_action_price;
                const effect = !limit || limit.currency !== operation.price.currency ? 'условие цены неизвестно' : Number(operation.price.amount) <= Number(limit.amount) ? 'новая цена соответствует пределу' : 'новая цена выше предела';
                box.append(node('p', item.action.title + ' · ' + (item.status === 'participating' ? 'участвует' : item.status === 'candidate' ? 'кандидат' : 'неизвестно') + ' · ' + effect, 'ozon-promotions-note'));
            });
        }
        if (evidence && !evidence.promotions.complete) box.append(node('p', 'Влияние на часть других акций не удалось подтвердить.', 'ozon-promotions-warning'));
        box.append(status);
        if (operation.issue) box.append(node('p', issues[operation.issue] || operation.issue, 'ozon-promotions-warning'));
        if (operation.reason) box.append(node('p', operation.reason, 'ozon-promotions-warning'));
        (operation.warnings || []).forEach(text => box.append(node('p', text, 'ozon-promotions-warning')));
        if (pending.has(operation.state)) box.append(node('p', 'Закрытие окна не отменит отправку. Операция сохранена в журнале.'));
        const actions = node('div', undefined, 'ozon-entry-actions');
        if (operation.state === 'prepared') {
            const confirm = button('Подтвердить цену ' + money(operation.price) + ' и вступить', async () => {
                confirm.disabled = true;
                try {
                    const result = await request(path(operation, 'confirm'), { confirmed: true });
                    if (sessions.get(root) !== session) return;
                    remember(root, session, result, true);
                    if (session.dialog === box && box.open) { details(root, session, box, result); poll(root, session, box, result, 0); }
                } catch (error) {
                    if (session.dialog !== box || !box.open) return;
                    status.textContent = error.message;
                    actions.replaceChildren(button('Проверить результат', () => check(root, session, box, operation)), button('Закрыть', () => box.close()));
                }
            });
            actions.append(confirm);
        } else if (pending.has(operation.state)) actions.append(button('Проверить результат', () => check(root, session, box, operation)));
        else if (draft && operation.state !== 'succeeded' && operation.state !== 'already_participating') {
            if (draft.voucher || operation.issue === 'stock_required') {
                draft.voucher = true;
                const label = node('label', 'Количество для акции-промокода: ');
                label.append(draft.stock); box.append(label);
            }
            actions.append(button('Проверить заново', () => preview(root, session, draft)));
        }
        if (draft?.voucher && operation.state === 'prepared') {
            const label = node('label', 'Количество для акции-промокода: ');
            label.append(draft.stock); box.append(label);
            draft.stock.oninput = () => {
                const confirm = actions.querySelector('button');
                if (confirm) confirm.disabled = true;
                status.textContent = 'Количество изменено. Проверьте условия заново.';
            };
            actions.append(button('Проверить количество заново', () => preview(root, session, draft)));
        }
        actions.append(button('Закрыть', () => box.close())); box.append(actions);
        box.append(node('p', 'Операция: ' + operation.id, 'ozon-promotions-note'));
    }
    function show(root, session, operation) {
        const box = dialog(session); details(root, session, box, operation); box.showModal();
        if (pending.has(operation.state)) check(root, session, box, operation);
    }
    async function check(root, session, box, operation, attempt = 0) {
        window.clearTimeout(session.timer);
        if (session.checking) return;
        session.checking = true;
        try {
            const result = await request(path(operation, 'check'), {}, session.controller.signal);
            if (sessions.get(root) !== session) return;
            remember(root, session, result, true);
            if (session.dialog !== box || !box.open) return;
            details(root, session, box, result); poll(root, session, box, result, attempt);
        } catch (error) {
            if (error.name !== 'AbortError' && session.dialog === box && box.open) box.append(node('p', error.message, 'ozon-promotions-warning'));
        } finally { session.checking = false; }
    }
    function poll(root, session, box, operation, attempt) {
        if (!pending.has(operation.state) || attempt >= 12) return;
        session.timer = window.setTimeout(() => check(root, session, box, operation, attempt + 1), 5000);
    }
    return { render, bind, history, clear };
})();
