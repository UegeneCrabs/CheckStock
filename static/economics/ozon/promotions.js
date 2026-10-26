window.OzonPromotionsUI = (() => {
    'use strict';
    const sessions = new WeakMap();
    const dates = new Intl.DateTimeFormat('ru-RU', {
        day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
        timeZone: 'Europe/Moscow',
    });
    const errors = {
        ozon_promotions_access_denied: 'Ключ Ozon не даёт доступ к акциям. Проверьте права Seller API.',
        ozon_promotions_credentials_missing: 'Для кабинета не настроен ключ Seller API Ozon.',
        forbidden: 'Нет доступа к этому кабинету.',
        not_found: 'Товар не найден в каталоге кабинета Ozon.',
    };
    const issues = {
        access_denied: 'Ozon не разрешил прочитать товары этой акции.',
        unavailable: 'Не удалось проверить участие товара в этой акции.',
        incomplete: 'Ozon вернул неполный список товаров. Участие пока неизвестно.',
        timeout: 'Проверка этой акции не завершилась за отведённое время.',
    };

    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }

    function date(value) {
        if (!value) return '—';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? '—' : dates.format(parsed);
    }

    function money(value) {
        if (!value || value.amount === null || value.amount === '' || value.amount === undefined) return '—';
        const amount = Number(value.amount);
        if (!Number.isFinite(amount)) return '—';
        try {
            return new Intl.NumberFormat('ru-RU', {
                style: 'currency', currency: value.currency, maximumFractionDigits: 2,
            }).format(amount);
        } catch (_) {
            return amount.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ' + value.currency;
        }
    }

    function init(root) {
        if (root.querySelector('#ue1c-panel-promotions')) return;
        const tab = element('button', 'Акции', 'ue1c-drawer-tab');
        tab.type = 'button';
        tab.id = 'ue1c-tab-promotions';
        tab.dataset.detailTab = 'promotions';
        tab.setAttribute('role', 'tab');
        tab.setAttribute('aria-selected', 'false');
        tab.setAttribute('aria-controls', 'ue1c-panel-promotions');
        root.querySelector('[data-detail-tab="params"]').before(tab);
        const panel = element('section', undefined, 'ue1c-tab-panel ozon-promotions');
        panel.id = 'ue1c-panel-promotions';
        panel.hidden = true;
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', tab.id);
        root.querySelector('#ue1c-panel-params').before(panel);
    }

    function clear(root) {
        const session = sessions.get(root);
        if (session) session.controller.abort();
        sessions.delete(root);
        const panel = root.querySelector('#ue1c-panel-promotions');
        if (panel) {
            panel.replaceChildren();
            panel.removeAttribute('aria-busy');
        }
    }

    function render(root, product) {
        const key = product.store_slug + ':' + product.article;
        if (sessions.get(root)?.key === key) return;
        clear(root);
        const session = { key, product, controller: new AbortController(), started: false };
        sessions.set(root, session);
    }

    function select(root, tabName) {
        const panel = root.querySelector('#ue1c-panel-promotions');
        if (!panel) return;
        panel.hidden = tabName !== 'promotions';
        const session = sessions.get(root);
        if (!panel.hidden && session && !session.started) load(root, session);
    }

    function retry(root, session) {
        const button = element('button', 'Повторить', 'ue1c-calculator-reset');
        button.type = 'button';
        button.onclick = () => load(root, session);
        return button;
    }

    async function load(root, session) {
        if (sessions.get(root) !== session) return;
        session.started = true;
        const panel = root.querySelector('#ue1c-panel-promotions');
        panel.replaceChildren(element('p', 'Проверяем акции и участие товара в Ozon…', 'ozon-promotions-note'));
        panel.setAttribute('aria-busy', 'true');
        try {
            const query = new URLSearchParams({ store: session.product.store_slug, article: session.product.article });
            const response = await fetch('/api/unit-economics-1c/ozon/promotions?' + query, {
                headers: { Accept: 'application/json' }, signal: session.controller.signal,
            });
            const result = await response.json();
            if (sessions.get(root) !== session) return;
            if (!response.ok) throw new Error(errors[result.detail] || 'Не удалось загрузить акции Ozon.');
            if (!Array.isArray(result.items) || typeof result.complete !== 'boolean') throw new Error('Некорректный ответ об акциях Ozon.');
            panel.replaceChildren();
            const heading = element('div', undefined, 'ozon-promotions-heading');
            heading.append(element('h3', 'Акции Ozon'), element('span', 'Только просмотр', 'ozon-promotions-note'));
            panel.append(heading);
            if (!result.complete) {
                const warning = element('div', undefined, 'ozon-promotions-warning');
                warning.append(element('p', 'Часть акций не удалось проверить. Список может быть неполным.'), retry(root, session));
                panel.append(warning);
            }
            const groups = [
                ['participating', 'Товар участвует'],
                ['candidate', 'Доступны для товара'],
                ['unknown', 'Не удалось проверить'],
            ];
            for (const [status, title] of groups) {
                const items = result.items.filter(item => item.status === status);
                if (!items.length) continue;
                const group = element('section', undefined, 'ozon-promotions-group');
                group.append(element('h4', title + ' · ' + items.length));
                items.forEach(item => group.append(card(item)));
                panel.append(group);
            }
            if (!result.items.length && result.complete) {
                panel.append(element('p', 'Для этого товара Ozon не вернул доступных акций или текущего участия.', 'ozon-promotions-note'));
            }
            panel.append(element('p', 'Проверено: ' + date(result.checked_at) + ' МСК. Данные кэшируются на 5 минут.', 'ozon-promotions-note'));
        } catch (error) {
            if (sessions.get(root) !== session || error.name === 'AbortError') return;
            panel.replaceChildren(element('p', error.message || 'Не удалось загрузить акции Ozon.', 'ozon-promotions-warning'), retry(root, session));
        } finally {
            if (sessions.get(root) === session) panel.removeAttribute('aria-busy');
        }
    }

    function card(item) {
        const action = item.action;
        const box = element('article', undefined, 'ozon-promotion-card');
        box.append(element('h5', action.title), element('p', date(action.date_start) + ' — ' + date(action.date_end) + ' МСК', 'ozon-promotion-dates'));
        if (action.is_voucher_action) box.append(element('span', 'По промокоду', 'ozon-promotion-badge'));
        if (item.issue) box.append(element('p', issues[item.issue] || issues.unavailable, 'ozon-promotions-warning'));
        const product = item.product;
        if (product) {
            const grid = element('dl', undefined, 'ozon-promotion-prices');
            function field(label, value, hint) {
                const pair = element('div');
                const name = element('dt', label);
                if (hint) name.title = hint;
                pair.append(name, element('dd', value));
                grid.append(pair);
            }
            field('Цена по акции', money(product.action_price));
            field('Максимальная цена акции', money(product.max_action_price), 'Максимальная цена товара по акции, которую вернул Ozon.');
            field('Минимальная цена после акций', money(product.min_seller_price), 'Минимальная цена товара после применения акций (min_seller_price). Это не нижняя граница цены для вступления в конкретную акцию.');
            field('Цена без скидки', money(product.price));
            if (product.price_min_elastic || product.price_max_elastic) {
                field('Цена для мин. бустинга', money(product.price_min_elastic));
                field('Цена для макс. бустинга', money(product.price_max_elastic));
            }
            if (product.min_stock !== null && product.min_stock !== undefined) field('Минимум единиц в акции', product.min_stock.toLocaleString('ru-RU'));
            if (product.stock !== null && product.stock !== undefined) field('Единиц в акции', product.stock.toLocaleString('ru-RU'));
            box.append(grid);
            if (product.is_quarantined) box.append(element('p', 'Товар в карантине Ozon.', 'ozon-promotions-warning'));
            if (product.alert_max_action_price_failed) box.append(element('p', 'Цена выше рекомендуемой Ozon: товар может быть исключён из акции.', 'ozon-promotions-warning'));
        }
        if (action.freeze_date) box.append(element('p', 'Ограничение изменений с ' + date(action.freeze_date) + ' МСК.', 'ozon-promotions-note'));
        return box;
    }

    return { init, render, select, clear };
})();
