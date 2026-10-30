window.OzonPriceSaveUI = (() => {
    'use strict';
    const sessions = new WeakMap();
    const base = '/api/unit-economics-1c/ozon/prices';
    const pending = new Set(['checking', 'sending', 'verifying', 'uncertain']);
    const states = {
        prepared: 'Цена готова к отправке.', blocked: 'Отправка недоступна.',
        checking: 'Проверяем условия перед отправкой…', sending: 'Отправляем цену в Ozon…',
        verifying: 'Проверяем применение цены…', uncertain: 'Результат пока не подтверждён.',
        succeeded: 'Цена подтверждена Ozon.', rejected: 'Ozon отклонил изменение.',
        changed: 'Условия изменились. Рассчитайте цену заново.', expired: 'Срок подтверждения истёк.',
        unchanged: 'Такая предельная цена уже установлена.', failed: 'Цена не отправлена.',
    };
    const issues = {
        incomplete: 'Не удалось полностью проверить акции товара.',
        price_unavailable: 'Недостаточно данных для расчёта предельной цены. Попробуйте ввести её напрямую.',
        currency: 'Сохранение доступно для цен в рублях.',
        min_price: 'Новая цена ниже ограничения для акций. Измените ограничение в Ozon и повторите проверку.',
        old_price: 'Новая цена должна быть ниже зачёркнутой. Измените зачёркнутую цену в Ozon и повторите проверку.',
        quarantined: 'Товар находится в архиве или карантине.', frozen: 'Участие в акции заморожено: повышение цены недоступно.',
        rate_limit: 'Достигнут лимит изменений цены. Повторите позже: Ozon разрешает до 10 изменений товара в час.',
        access_denied: 'Нет ключа Seller API или ключ не разрешает операцию.',
        unavailable: 'Не удалось получить данные Ozon.', expired: 'Подготовьте новое подтверждение.',
        changed: 'Текущая цена или условия отличаются от показанных при подтверждении.',
        rejected: 'Изменение не принято Ozon.', write_uncertain: 'Повторная отправка заблокирована. Проверяем фактическую цену.',
        verify_failed: 'Не удалось подтвердить цену и обновить данные. Доступна проверка без повторной отправки.',
        interrupted: 'Операция прервалась до отправки. Подготовьте новое подтверждение.',
    };
    const labels = { base_price: 'Предельная цена без акций', seller_price: 'Цена с учётом акции', black_price: 'Цена с СПП', green_price: 'Цена с Кошельком' };
    const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const dates = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Moscow' });
    function node(tag, text, className) {
        const item = document.createElement(tag);
        if (text !== undefined) item.textContent = text;
        if (className) item.className = className;
        return item;
    }
    function button(text, action, className = 'ue1c-calculator-reset') {
        const item = node('button', text, className);
        item.type = 'button'; item.onclick = action;
        return item;
    }
    function money(value, currency = 'RUB') {
        return value == null ? '—' : numbers.format(Number(value)) + (currency === 'RUB' ? ' ₽' : ' ' + currency);
    }
    function active(root, session) {
        return root.isConnected && sessions.get(root) === session;
    }
    function clear(root) {
        const session = sessions.get(root);
        if (!session) return;
        session.controller.abort(); window.clearTimeout(session.timer);
        root.removeEventListener('ozon-prices-pending', session.changed);
        root.removeEventListener('ozon-prices-changed', session.changed);
        if (session.dialog?.open) session.dialog.close();
        session.dialog?.remove(); session.history?.remove();
        sessions.delete(root);
    }
    function updateButton(root, session) {
        const save = root.querySelector('#ue1c-save-price');
        if (!save) return;
        const snapshot = window.OzonPricesUI.snapshot(root);
        const field = snapshot?.target_field || 'base_price';
        const amount = snapshot ? Number(snapshot.values[field]) : NaN;
        save.disabled = !session.canEdit || session.busy || !Number.isFinite(amount) || amount <= 0;
        save.textContent = session.busy ? 'Проверяем…' : 'Сохранить цену';
        save.setAttribute('aria-busy', String(Boolean(session.busy)));
    }
    function render(root, product, options) {
        let session = sessions.get(root);
        if (session?.product.id !== product.id) {
            clear(root);
            session = { product, ...options, controller: new AbortController(), operations: new Map(), applied: new Set(), polls: 0 };
            sessions.set(root, session);
            session.changed = () => updateButton(root, session);
            root.addEventListener('ozon-prices-pending', session.changed);
            root.addEventListener('ozon-prices-changed', session.changed);
            const history = node('details', undefined, 'ozon-entry-history ozon-price-save-history');
            const historyContent = node('div', undefined, 'ozon-price-save-history-content');
            historyContent.append(node('p', 'Загружаем журнал…', 'ozon-promotions-note'));
            history.append(node('summary', 'Журнал изменения цен'), historyContent);
            root.querySelector('#ue1c-panel-economics > .ue1c-calculator:not(.ue1c-placeholder-calculator)').after(history);
            session.history = history;
            session.historyContent = historyContent;
            loadHistory(root, session);
        } else Object.assign(session, { product, ...options });
        updateButton(root, session);
    }
    async function request(url, payload, signal) {
        let response, result;
        try {
            response = await fetch(url, {
                method: payload === undefined ? 'GET' : 'POST', signal,
                headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: payload === undefined ? undefined : JSON.stringify(payload),
            });
            result = await response.json();
        } catch (error) {
            if (error.name === 'AbortError') throw error;
            throw new Error('Ответ сервера не получен. Обновите журнал и проверьте результат операции.');
        }
        if (!response.ok || response.redirected) throw new Error(
            response.status === 409 ? 'Для товара уже выполняется изменение цены или вступление в акцию. Проверьте журналы.' :
            response.status === 403 ? 'Нет доступа к кабинету.' : response.status === 422 ? 'Проверьте положительную цену с точностью до копейки.' :
            issues[result.detail] || 'Не удалось выполнить запрос. Обновите журнал операции.');
        return result;
    }
    function path(operation, suffix) {
        return base + '/' + encodeURIComponent(operation.id) + '/' + suffix + '?store=' + encodeURIComponent(operation.store);
    }
    function remember(root, session, operation, apply = false) {
        if (!active(root, session)) return;
        const previous = session.operations.get(operation.id);
        if (previous && Date.parse(previous.updated_at) > Date.parse(operation.updated_at)) return;
        session.operations.set(operation.id, operation);
        historyRows(root, session);
        if (apply && operation.state === 'succeeded' && !session.applied.has(operation.id)) {
            session.applied.add(operation.id); session.completed(operation);
        }
    }
    function historyRows(root, session) {
        session.historyContent.replaceChildren(button('Обновить журнал', () => loadHistory(root, session)));
        const operations = [...session.operations.values()].sort((a, b) =>
            Number(pending.has(b.state)) - Number(pending.has(a.state)) || b.created_at.localeCompare(a.created_at)).slice(0, 10);
        if (!operations.length) session.historyContent.append(node('p', 'Операций пока нет.', 'ozon-promotions-note'));
        operations.forEach(operation => {
            const row = node('div', undefined, 'ozon-entry-history-row');
            const information = node('div', undefined, 'ozon-price-save-history-info');
            information.append(node('span', dates.format(new Date(operation.sent_at || operation.created_at)) + ' · ' +
                (operation.user_name || 'Автор не указан'), 'ozon-price-save-history-meta'),
                node('strong', submission(operation), operation.sent_at ? '' : 'ozon-price-save-history-unsent'));
            row.append(information,
                button(states[operation.state] || operation.state, () => show(root, session, operation)));
            session.historyContent.append(row);
        });
    }
    function submission(operation) {
        const amount = operation.plan?.price;
        const currency = operation.plan?.currency;
        if (!operation.sent_at) return amount == null ? 'Не отправлено' : 'Не отправлено · Цена к отправке: ' + money(amount, currency);
        return (operation.state === 'succeeded' ? 'Отправлено в Ozon: ' : 'Попытка отправки в Ozon: ') + money(amount, currency);
    }
    async function loadHistory(root, session) {
        try {
            const query = new URLSearchParams({ store: session.product.store_slug, article: session.product.article });
            const result = await request(base + '?' + query, undefined, session.controller.signal);
            if (!active(root, session)) return;
            result.items.forEach(item => remember(root, session, item));
            historyRows(root, session);
            const running = result.items.find(item => pending.has(item.state));
            if (running) watch(root, session, running);
        } catch (error) {
            if (!active(root, session) || error.name === 'AbortError') return;
            session.historyContent.replaceChildren(node('p', error.message), button('Обновить журнал', () => loadHistory(root, session)));
        }
    }
    function dialog(root, session) {
        if (session.dialog?.open) session.dialog.close();
        session.dialog?.remove();
        const box = node('dialog', undefined, 'ue1c-modal ozon-price-save-dialog');
        box.setAttribute('aria-labelledby', 'ozon-price-save-title');
        box.addEventListener('keydown', event => { if (event.key === 'Escape') event.stopPropagation(); });
        root.append(box); session.dialog = box;
        return box;
    }
    function shell(root, session, box, titleText) {
        const header = node('header', undefined, 'ue1c-modal-head');
        const heading = node('div');
        const title = node('h2', titleText); title.id = 'ozon-price-save-title';
        const product = node('p');
        product.append(document.createTextNode((session.product.store_name || session.product.store_slug) + ' · Арт. '),
            node('strong', session.product.article), document.createTextNode(' · ' + (session.product.name || '')));
        heading.append(title, product);
        const close = button('×', () => box.close(), 'ue1c-close');
        close.setAttribute('aria-label', 'Закрыть подтверждение');
        header.append(heading, close);
        const body = node('div', undefined, 'ue1c-price-confirm-body');
        const actions = node('footer', undefined, 'ue1c-modal-foot');
        box.replaceChildren(header, body, actions);
        return { body, actions };
    }
    function message(root, session, box, text, error = false) {
        const { body, actions } = shell(root, session, box, 'Изменение цены Ozon');
        const status = node('p', text, error ? 'ue1c-price-confirm-warning' : 'ozon-price-save-status');
        status.setAttribute('role', error ? 'alert' : 'status');
        body.append(status);
        actions.append(button(error ? 'Закрыть' : 'Отмена', () => box.close(), 'ue1c-modal-tool'));
    }
    function comparisonRow(grid, label, previous, next, currency) {
        const changed = previous != null && next != null && Number(previous) !== Number(next);
        grid.append(node('span', label), node('span', money(previous, currency), changed ? 'is-old' : ''),
            node('span', '→ ' + money(next, currency), 'is-new'));
    }
    async function preview(root) {
        const session = sessions.get(root);
        const snapshot = window.OzonPricesUI.snapshot(root);
        if (!session?.canEdit || session.busy || !snapshot || snapshot.productId !== session.product.id) return;
        const field = snapshot.target_field || 'base_price';
        const target = snapshot.values[field];
        if (target == null || Number(target) <= 0) return;
        const box = dialog(root, session);
        message(root, session, box, 'Проверяем свежую цену, ограничения и акции Ozon…');
        box.showModal(); session.busy = true; updateButton(root, session);
        try {
            const operation = await request(base + '/preview', {
                store: session.product.store_slug, article: session.product.article, target_field: field, target_price: target,
            }, session.controller.signal);
            if (!active(root, session)) return;
            remember(root, session, operation);
            if (session.dialog === box && box.open) details(root, session, box, operation);
        } catch (error) {
            if (active(root, session) && session.dialog === box && error.name !== 'AbortError')
                message(root, session, box, error.message, true);
        } finally {
            if (active(root, session)) { session.busy = false; updateButton(root, session); }
        }
    }
    function show(root, session, operation) {
        const box = dialog(root, session);
        details(root, session, box, operation); box.showModal();
        if (pending.has(operation.state)) watch(root, session, operation);
    }
    function details(root, session, box, operation) {
        const expanded = box.querySelector('.ozon-price-save-conditions')?.open === true;
        const displayState = operation.state === 'prepared' && Date.parse(operation.expires_at) <= Date.now()
            ? 'expired' : operation.state;
        const { body, actions } = shell(root, session, box,
            displayState === 'prepared' ? 'Подтвердите изменение цены' : 'Изменение цены Ozon');
        box.dataset.state = displayState;
        body.append(node('p', 'Автор: ' + (operation.user_name || 'Автор не указан'), 'ozon-price-save-note'));
        if (operation.sent_at) body.append(node('p', submission(operation) + ' · ' + dates.format(new Date(operation.sent_at)) + ' МСК', 'ozon-price-save-note'));
        if (displayState !== 'prepared') {
            const status = node('p', states[displayState] || displayState, 'ozon-price-save-status');
            status.setAttribute('role', 'status'); body.append(status);
        }
        if (operation.plan) {
            const plan = operation.plan;
            body.append(node('strong', labels[operation.target_field] + ': ' + money(operation.target_price, plan.currency), 'ue1c-price-confirm-target'));
            const grid = node('div', undefined, 'ue1c-price-confirm-grid');
            grid.setAttribute('role', 'group'); grid.setAttribute('aria-label', 'Текущие и новые цены Ozon');
            comparisonRow(grid, labels.base_price, plan.previous_price, plan.price, plan.currency);
            ['seller_price', 'black_price', 'green_price'].forEach(field => {
                comparisonRow(grid, labels[field], plan.previous_prices?.[field]?.amount ?? null, plan.prediction[field], plan.currency);
            });
            body.append(grid, node('p', 'Участие в акциях может измениться. Цены с учётом акции, СПП и Кошелька — прогноз.', 'ozon-price-save-note'));
            const conditions = node('details', undefined, 'ozon-price-save-conditions'); conditions.open = expanded;
            conditions.append(node('summary', 'Ограничения и акции'));
            const limits = node('dl', undefined, 'ozon-price-save-limits');
            limits.append(node('dt', 'Минимальная цена'), node('dd', money(plan.min_price, plan.currency)),
                node('dt', 'Зачёркнутая цена'), node('dd', Number(plan.old_price) === 0 ? 'Не задана' : money(plan.old_price, plan.currency)));
            conditions.append(limits, node('p', 'Минимальная и зачёркнутая цены сохраняются. Окончательные цены покупателя определяет Ozon.'));
            if (plan.impacts.length) {
                const list = node('ul');
                plan.impacts.forEach(item => list.append(node('li', item.title + ': ' +
                    (item.expected_participation == null ? 'определяется условиями Ozon' : item.expected_participation ? 'цена подходит' : 'цена выше предела') +
                    (item.current_status === 'participating' ? ' · сейчас участвует' : ''))));
                conditions.append(list);
            }
            body.append(conditions);
        }
        if (operation.observed) body.append(node('p', 'Проверено в Ozon: ' + money(operation.observed.prices.price.price, operation.observed.prices.price.currency_code), 'ozon-price-save-note'));
        if (operation.issue) {
            const warning = node('p', issues[operation.issue] || operation.issue, 'ue1c-price-confirm-warning');
            warning.setAttribute('role', 'alert'); body.append(warning);
        }
        operation.errors.forEach(error => body.append(node('p', error.code + (error.message ? ': ' + error.message : ''), 'ue1c-price-confirm-warning')));
        actions.append(button(displayState === 'prepared' ? 'Отмена' : 'Закрыть', () => box.close(), 'ue1c-modal-tool'));
        if (displayState === 'prepared') {
            body.append(node('p', 'Подтверждение до ' + dates.format(new Date(operation.expires_at)) + ' МСК', 'ozon-price-save-expiry'));
            actions.append(button('Подтвердить и отправить', async function () {
                this.disabled = true;
                try {
                    const result = await request(path(operation, 'confirm'), { confirmed: true }, session.controller.signal);
                    if (!active(root, session)) return;
                    remember(root, session, result, true);
                    if (session.dialog === box && box.open) details(root, session, box, result);
                    session.polls = 0; watch(root, session, result);
                } catch (error) {
                    if (active(root, session) && session.dialog === box && box.open && error.name !== 'AbortError') {
                        body.append(node('p', error.message, 'ue1c-price-confirm-warning'));
                        this.disabled = false;
                    }
                }
            }, 'ue1c-primary-button'));
        }
        if (pending.has(operation.state)) actions.append(button('Проверить результат', async function () {
            this.disabled = true;
            await check(root, session, operation);
            this.disabled = false;
        }, 'ue1c-primary-button'));
        box.dataset.operationId = operation.id;
    }
    async function check(root, session, operation) {
        try {
            const result = await request(path(operation, 'check'), {}, session.controller.signal);
            if (!active(root, session)) return null;
            remember(root, session, result, true);
            const box = session.dialog;
            if (box?.open && box.dataset.operationId === result.id) details(root, session, box, result);
            return result;
        } catch (error) {
            if (active(root, session) && error.name !== 'AbortError') {
                const box = session.dialog;
                if (box?.open && box.dataset.operationId === operation.id) box.querySelector('.ue1c-price-confirm-body').append(node('p', error.message, 'ue1c-price-confirm-warning'));
            }
            return null;
        }
    }
    function watch(root, session, operation) {
        window.clearTimeout(session.timer);
        if (!pending.has(operation.state) || session.polls >= 12) return;
        session.timer = window.setTimeout(async () => {
            if (!active(root, session)) return;
            session.polls += 1;
            const result = await check(root, session, operation);
            if (active(root, session) && result) watch(root, session, result);
        }, 3000);
    }
    return { render, clear, preview };
})();
