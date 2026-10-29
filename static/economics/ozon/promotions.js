window.OzonPromotionsUI = (() => {
    'use strict';
    const sessions = new WeakMap();
    const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const inputFormat = new Intl.NumberFormat('en-US', { useGrouping: false, maximumFractionDigits: 2 });
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
        window.OzonPromotionsTimeline.clear(root);
        const session = sessions.get(root);
        if (session) {
            session.controller.abort();
            session.rows.forEach(cancel);
            root.removeEventListener('ozon-calculator-changed', session.changed);
            window.removeEventListener('storage', session.boostChanged);
            window.removeEventListener('focus', session.focus);
        }
        sessions.delete(root);
        const panel = root.querySelector('#ue1c-panel-promotions');
        if (panel) {
            panel.replaceChildren();
            panel.removeAttribute('aria-busy');
        }
    }

    function render(root, product) {
        const key = product.store_slug + ':' + product.article;
        if (sessions.get(root)?.key === key) {
            sessions.get(root).product = product;
            return;
        }
        clear(root);
        const session = { key, product, controller: new AbortController(), started: false, rows: new Map() };
        session.changed = event => {
            if (event.detail.productId === session.product.id) refresh(root, session);
        };
        session.boostChanged = event => {
            if (event.key === 'checkstock.ozon.boost-updated') refresh(root, session, true);
        };
        session.focus = () => refresh(root, session, true);
        sessions.set(root, session);
        root.addEventListener('ozon-calculator-changed', session.changed);
        window.addEventListener('storage', session.boostChanged);
        window.addEventListener('focus', session.focus);
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

    function currentPrice(root, session) {
        const prices = window.OzonPricesUI?.snapshot(root);
        if (prices && prices.productId === session.product.id) return prices.values.seller_price;
        const source = session.product.price?.seller_price;
        return source?.state === 'conflict' ? null : source?.amount ?? null;
    }

    function resetPrices(root, session) {
        if (sessions.get(root) !== session) return;
        const price = currentPrice(root, session);
        session.rows.forEach(row => {
            row.draft = undefined;
            row.currency = session.product.price?.seller_price?.currency || 'RUB';
            row.input.value = price == null ? '' : inputFormat.format(Number(price));
            schedule(root, session, row);
        });
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
            const reset = element('button', 'Сбросить', 'ue1c-calculator-reset');
            reset.type = 'button';
            reset.title = 'Вернуть цены всех акций к текущей цене товара.';
            reset.onclick = () => resetPrices(root, session);
            heading.append(element('h3', 'Акции Ozon'), reset);
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
            const previous = session.rows;
            previous.forEach(cancel);
            session.rows = new Map();
            session.contextKey = null;
            for (const [status, title] of groups) {
                const items = result.items.filter(item => item.status === status);
                if (!items.length) continue;
                const group = element('section', undefined, 'ozon-promotions-group');
                group.append(element('h4', title + ' · ' + items.length));
                items.forEach(item => group.append(card(root, session, item, previous.get(item.action.id))));
                panel.append(group);
            }
            if (!result.items.length && result.complete) {
                panel.append(element('p', 'Для этого товара Ozon не вернул доступных акций или текущего участия.', 'ozon-promotions-note'));
            }
            reset.disabled = !session.rows.size;
            const timeline = element('section', undefined, 'ozon-promotions-timeline');
            panel.append(timeline);
            window.OzonPromotionsTimeline.render(root, timeline, result.items);
            refresh(root, session);
        } catch (error) {
            if (sessions.get(root) !== session || error.name === 'AbortError') return;
            panel.replaceChildren(element('p', error.message || 'Не удалось загрузить акции Ozon.', 'ozon-promotions-warning'), retry(root, session));
        } finally {
            if (sessions.get(root) === session) panel.removeAttribute('aria-busy');
        }
    }

    function field(box, label, value) {
        const pair = element('div', undefined, 'ozon-promotion-field');
        pair.append(element('span', label, 'ozon-promotion-label'), value);
        box.append(pair);
    }

    function amount(value) {
        return value?.amount == null ? null : Number(value.amount);
    }

    function card(root, session, item, previous) {
        const action = item.action;
        const box = element('article', undefined, 'ozon-promotion-card');
        const title = element('h5', action.title);
        title.title = action.title + '\n' + date(action.date_start) + ' — ' + date(action.date_end) + ' МСК' +
            (action.freeze_date ? '\nОграничение изменений с ' + date(action.freeze_date) + ' МСК.' : '');
        const identity = element('div', undefined, 'ozon-promotion-identity');
        identity.append(title, element('p', window.OzonPromotionsTimeline.period(action), 'ozon-promotion-period'));
        box.append(identity);
        if (item.issue) box.append(element('p', issues[item.issue] || issues.unavailable, 'ozon-promotions-warning'));
        const product = item.product;
        if (!product) {
            box.classList.add('ozon-promotion-card--unknown');
            return box;
        }
        const minimum = product.price_min_elastic;
        const maximum = product.price_max_elastic;
        const fixed = minimum && maximum && minimum.currency === maximum.currency && amount(minimum) === amount(maximum);
        box.classList.toggle('ozon-promotion-card--fixed', Boolean(fixed));
        const source = session.product.price?.seller_price;
        const currency = source?.currency || 'RUB';
        const input = element('input', undefined, 'ozon-promotion-price');
        input.type = 'number';
        input.min = '0';
        input.max = '1000000000000';
        input.step = '0.01';
        input.placeholder = '—';
        input.dataset.promotionPrice = String(action.id);
        const initial = currentPrice(root, session);
        input.value = previous?.draft ?? (initial == null ? '' : inputFormat.format(Number(initial)));
        const priceLabel = fixed ? 'Текущая цена' : 'Цена по акции';
        input.setAttribute('aria-label', priceLabel + ' — ' + action.title);
        const boost = element('strong', '—');
        boost.title = 'Рассчитывается по текущей цене и общим настройкам бустинга Ozon.';
        const priceField = element('label', undefined, 'ozon-promotion-field');
        priceField.append(element('span', priceLabel, 'ozon-promotion-label'), input);
        box.append(priceField);
        field(box, fixed ? 'Цена для бустинга' : 'Цена для мин. бустинга', element('strong', money(minimum)));
        if (fixed) {
            const placeholder = element('div', undefined, 'ozon-promotion-placeholder');
            placeholder.setAttribute('aria-hidden', 'true');
            box.append(placeholder);
        } else field(box, 'Цена для макс. бустинга', element('strong', money(maximum)));
        const margin = element('strong', '—');
        const roi = element('strong', '—');
        field(box, 'Маржа', margin);
        field(box, 'ROI', roi);
        field(box, 'Бустинг', boost);
        const note = element('p', undefined, 'ozon-promotion-calculation-note');
        note.hidden = true;
        note.setAttribute('role', 'status');
        box.append(note);
        const row = { item, input, margin, roi, boost, note, currency, sequence: 0, draft: previous?.draft };
        session.rows.set(action.id, row);
        input.oninput = () => {
            row.draft = input.value;
            schedule(root, session, row);
        };
        input.onblur = () => {
            if (input.value !== '' && input.checkValidity()) input.value = inputFormat.format(Number(input.value));
        };
        return box;
    }

    function cancel(row) {
        window.clearTimeout(row.timer);
        if (row.controller) row.controller.abort();
        row.sequence++;
    }

    function calculationContext(root, session) {
        const calculator = window.OzonCalculatorUI?.snapshot(root);
        const prices = window.OzonPricesUI?.snapshot(root);
        if (!calculator || !prices || calculator.productId !== session.product.id || prices.productId !== session.product.id) return null;
        const { productId, ...request } = prices;
        return { values: calculator.values, prices: request };
    }

    function refresh(root, session, force = false) {
        const context = calculationContext(root, session);
        const key = context && JSON.stringify(context);
        if (!force && key && key === session.contextKey) return;
        session.contextKey = key;
        session.rows.forEach(row => schedule(root, session, row));
    }

    function paintMetric(node, value, suffix) {
        const amount = value == null || value === '' ? null : Number(value);
        const known = amount !== null && Number.isFinite(amount);
        node.textContent = known ? numbers.format(amount) + suffix : '—';
        node.classList.toggle('is-positive', known && amount > 0);
        node.classList.toggle('is-negative', known && amount <= 0);
    }

    function schedule(root, session, row) {
        cancel(row);
        paintMetric(row.margin, null, ' ₽');
        paintMetric(row.roi, null, '%');
        row.boost.textContent = '—';
        row.input.classList.remove('is-eligible', 'is-ineligible');
        row.input.removeAttribute('title');
        row.input.removeAttribute('aria-invalid');
        row.note.textContent = '';
        row.note.hidden = true;
        const context = calculationContext(root, session);
        if (context && row.draft === undefined) {
            const price = context.prices.values.seller_price;
            row.input.value = price == null ? '' : inputFormat.format(Number(price));
        }
        if (!row.input.checkValidity()) {
            row.input.setAttribute('aria-invalid', 'true');
            row.note.textContent = 'Введите неотрицательную цену с точностью до копеек.';
            row.note.hidden = false;
            return;
        }
        if (!context) {
            row.note.textContent = 'Ожидаем актуальные параметры калькулятора.';
            row.note.hidden = false;
            return;
        }
        if (row.currency !== 'RUB') {
            row.note.textContent = 'Расчёт доступен для цен в рублях.';
            row.note.hidden = false;
            return;
        }
        const sequence = row.sequence;
        row.margin.textContent = '…';
        row.roi.textContent = '…';
        row.boost.textContent = '…';
        row.timer = window.setTimeout(async () => {
            const controller = new AbortController();
            row.controller = controller;
            try {
                const response = await fetch('/api/unit-economics-1c/ozon/promotions/calculate', {
                    method: 'POST', signal: controller.signal,
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'fetch' },
                    body: JSON.stringify({ ...context, product: row.item.product, price: row.input.value === '' ? null : row.input.value, currency: row.currency }),
                });
                if (!response.ok || response.redirected) throw new Error('Не удалось рассчитать. Проверьте цену и параметры калькулятора.');
                const result = await response.json();
                if (sessions.get(root) !== session || row.sequence !== sequence) return;
                paintMetric(row.margin, result.result.margin, ' ₽');
                paintMetric(row.roi, result.result.roi, '%');
                row.boost.textContent = result.boost_percent == null ? '—' : numbers.format(Number(result.boost_percent)) + '%';
                row.input.classList.toggle('is-eligible', result.eligible === true);
                row.input.classList.toggle('is-ineligible', result.eligible === false);
                row.input.title = result.eligible === true ? 'Цена соответствует условию акции.' : result.eligible === false ? 'Цена не соответствует условию акции.' : 'Недостаточно данных для проверки цены.';
                const labels = window.OzonEconomicsUI?.marginLabels || {};
                const missing = (result.result.missing || []).map(key => labels[key] || key);
                row.note.textContent = missing.length ? 'Неполный расчёт: ' + missing.join(', ') + '.' : '';
                row.note.hidden = !row.note.textContent;
            } catch (error) {
                if (error.name === 'AbortError' || sessions.get(root) !== session || row.sequence !== sequence) return;
                paintMetric(row.margin, null, ' ₽');
                paintMetric(row.roi, null, '%');
                row.boost.textContent = '—';
                row.note.textContent = error.message || 'Не удалось рассчитать.';
                row.note.hidden = false;
            }
        }, 150);
    }

    return { init, render, select, clear };
})();
