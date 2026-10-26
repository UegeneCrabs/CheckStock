window.OzonPricesUI = (() => {
    'use strict';
    const fields = [
        ['base_price', 'Цена поставщика'],
        ['seller_price', 'Цена с учетом акции'],
        ['black_price', 'Цена с СПП'],
        ['green_price', 'Цена с Кошельком'],
    ];
    const discounts = [
        ['spp_percent', 'Скидка СПП, %', 'Скидка от цены с учётом акции.'],
        ['wallet_percent', 'Скидка с Ozon Кошельком, %', 'Скидка от цены с СПП.'],
    ];
    const sessions = new WeakMap();
    const inputFormat = new Intl.NumberFormat('en-US', { useGrouping: false, maximumFractionDigits: 2 });
    const dates = new Intl.DateTimeFormat('ru-RU', {
        timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
    const states = {
        missing: 'Нет данных', conflict: 'Расхождение между акциями', error: 'Ошибка обновления',
    };

    function element(tag, className, text) {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function displayed(value) {
        return value == null ? '' : inputFormat.format(Number(value));
    }

    function clear(root) {
        window.OzonCalculatorHelp.clear(root);
        const session = sessions.get(root);
        if (session) {
            if (window.OzonCategoriesUI) window.OzonCategoriesUI.clear(session);
            window.clearTimeout(session.timer);
            if (session.controller) session.controller.abort();
        }
        sessions.delete(root);
    }

    function reset(root, product) {
        clear(root);
        render(root, product);
    }

    function bind(root, session, input, key) {
        input.dataset.ozonCalculatorInput = key;
        input.value = Object.hasOwn(session.draft, key) ? session.draft[key] : displayed(session.values[key]);
        input.oninput = () => {
            session.modified = true;
            if (fields.some(([field]) => field === key)) {
                session.helpPriceAnchor = key;
                session.helpPriceManual = key;
            } else if (key === 'spp_percent' || key === 'wallet_percent') {
                session.helpPriceAnchor = key === 'spp_percent' ? 'seller_price' : 'black_price';
                session.helpPriceManual = null;
                session.helpDiscounts.add(key);
            } else session.helpCommissionBasis = key;
            if (key.startsWith('commission_') && window.OzonCategoriesUI) window.OzonCategoriesUI.manual(root, session);
            session.draft[key] = input.value;
            if (!input.checkValidity()) {
                session.invalid.add(key);
            } else {
                session.invalid.delete(key);
                const edit = { field: key, value: input.value === '' ? null : input.value };
                if (session.edits.length && session.edits[session.edits.length - 1].field === key) {
                    session.edits[session.edits.length - 1] = edit;
                } else {
                    session.edits.push(edit);
                }
            }
            queue(root, session);
        };
    }

    function render(root, product) {
        const calculator = root.querySelector('#ue1c-panel-economics > .ue1c-calculator:not(.ue1c-placeholder-calculator)');
        if (!calculator) return;
        const inputs = calculator.querySelector('.ue1c-calculator-inputs');
        if (!inputs) return;
        ['retail', 'spp', 'client', 'walletPercent', 'wallet'].forEach(key => {
            const input = calculator.querySelector('[data-calculator-input="' + key + '"]');
            if (input) input.closest('.ue1c-calculator-row').hidden = true;
        });
        let session = sessions.get(root);
        if (!session || session.productId !== product.id) {
            clear(root);
            const commission = product.commission || {};
            const values = { commission_percent: commission.percent ?? null, commission_amount: commission.amount ?? null };
            fields.forEach(([key]) => {
                const price = (product.price || {})[key] || {};
                values[key] = price.state === 'conflict' ? null : price.amount ?? null;
            });
            session = { productId: product.id, values, draft: {}, edits: [], invalid: new Set(), initialized: false, sequence: 0, helpDiscounts: new Set() };
            sessions.set(root, session);
        }
        session.helpProduct = product;
        calculator.querySelectorAll('[data-ozon-price-row], [data-ozon-calculator-status], .ozon-prices').forEach(row => row.remove());
        fields.forEach(([key, label], index) => {
            const price = (product.price || {})[key] || {};
            const { row, input } = numericRow(label, key, index - fields.length - discounts.length);
            input.dataset.ozonPriceInput = key;
            const status = states[price.state] || (price.stale ? 'Данные устарели' : '');
            const notes = [label];
            if (key === 'seller_price') notes.push('Без дополнительной скидки Ozon. Эта цена определяет диапазон комиссии: до 100 ₽ — 17%, свыше 100 до 300 ₽ — 23%, кроме тарифов отдельных категорий.');
            if (status) notes.push(status);
            if (price.received_at) notes.push('Получено из API: ' + dates.format(new Date(price.received_at)) + ' МСК.');
            notes.push('Цены связаны через текущие скидки. Обновление из API каждый час. Правки действуют только в открытой карточке.');
            if (key === 'black_price' || key === 'green_price') notes.push('Витринная цена API может отличаться от цены в вашем аккаунте Ozon.');
            row.title = notes.join('\n');
            row.classList.toggle('has-source-issue', Boolean(status));
            input.nextElementSibling.textContent = !price.currency || price.currency === 'RUB' ? '₽' : price.currency;
            bind(root, session, input, key);
            inputs.append(row);
        });
        discounts.forEach(([key, label, note], index) => {
            const { row, input } = numericRow(label, key, index - discounts.length);
            row.classList.add('ue1c-calculator-row--expanded');
            input.removeAttribute('min');
            input.max = '100';
            input.step = '0.00000001';
            input.nextElementSibling.textContent = '%';
            row.title = note + ' Изменение процента пересчитывает цены ниже по цепочке.';
            bind(root, session, input, key);
            inputs.append(row);
        });
        const status = element('div', 'ozon-calculator-status');
        status.dataset.ozonCalculatorStatus = '';
        status.setAttribute('role', 'status');
        status.hidden = true;
        inputs.append(status);
        renderCommission(root, product, session);
        if (window.OzonCategoriesUI) window.OzonCategoriesUI.render(root, product, session, () => queue(root, session));
        bindHelp(root, session);
        if (!session.initialized || session.edits.length) queue(root, session);
    }

    function bindHelp(root, session) {
        root.querySelectorAll('[data-ozon-calculator-input]').forEach(input => {
            window.OzonCalculatorHelp.bind(input.closest('.ue1c-calculator-row'), () =>
                window.OzonCalculatorHelpContent.prices(session.helpProduct, session, input.dataset.ozonCalculatorInput, input.getAttribute('aria-label')));
        });
        window.OzonCalculatorHelp.refresh();
    }

    function numericRow(label, key, order) {
        const row = element('label', 'ue1c-calculator-row ozon-price-row');
        row.dataset.ozonPriceRow = key;
        row.style.setProperty('--compact-order', String(order));
        row.style.setProperty('--expanded-order', String(order));
        const field = element('span', 'ue1c-calculator-field');
        const input = element('input', '');
        input.type = 'number';
        input.min = '0';
        input.step = '0.01';
        input.placeholder = '—';
        input.setAttribute('aria-label', label);
        field.append(input, element('b', '', '₽'));
        row.append(element('span', '', label), field);
        return { row, input };
    }

    function renderCommission(root, product, session) {
        const source = product.commission || {};
        [['commission', 'commission_percent', 'Комиссия Ozon FBO, %'],
            ['commissionRub', 'commission_amount', 'Комиссия Ozon, руб']].forEach(([key, field, label]) => {
            const input = root.querySelector('[data-calculator-input="' + key + '"]');
            const row = input.closest('.ue1c-calculator-row');
            row.classList.add('ue1c-calculator-row--expanded');
            row.style.setProperty('--expanded-order', '8');
            row.firstElementChild.textContent = label;
            const notes = [key === 'commission' ? 'Комиссия за продажу FBO. Исходная ставка загружается раз в день. При изменении цены диапазон определяется ценой с учётом акции: до 100 ₽ — 17%, свыше 100 до 300 ₽ — 23%, с учётом исключений выбранной категории. Выше 300 ₽ возвращается обычная ставка.'
                : 'Цена с учётом акции × комиссия Ozon FBO / 100.'];
            if (source.received_at) notes.push('Получено из API: ' + dates.format(new Date(source.received_at)) + ' МСК.');
            if (source.state === 'missing') notes.push('Комиссия из API ещё не загружена.');
            if (source.state === 'error') notes.push('Ошибка обновления. Показана последняя известная ставка.');
            if (source.stale) notes.push('Ставка из предыдущей выгрузки.');
            notes.push('Правки действуют только в открытой карточке.');
            row.title = notes.join('\n');
            input.disabled = false;
            row.dataset.ozonCommissionApiTitle = row.title;
            input.readOnly = false;
            input.placeholder = '—';
            input.step = key === 'commission' ? '0.00000001' : '0.01';
            input.setAttribute('aria-label', label);
            bind(root, session, input, field);
        });
    }

    function queue(root, session) {
        session.helpPending = true;
        session.helpError = '';
        bindHelp(root, session);
        root.dispatchEvent(new CustomEvent('ozon-prices-pending', { detail: { productId: session.productId } }));
        window.clearTimeout(session.timer);
        if (session.controller) session.controller.abort();
        const sequence = ++session.sequence;
        const status = root.querySelector('[data-ozon-calculator-status]');
        if (status) status.hidden = true;
        root.querySelectorAll('[data-ozon-calculator-input]').forEach(input => input.removeAttribute('aria-busy'));
        if (session.invalid.size) return;
        root.querySelectorAll('[data-ozon-calculator-input]').forEach(input => input.setAttribute('aria-busy', 'true'));
        session.timer = window.setTimeout(async () => {
            const controller = new AbortController();
            session.controller = controller;
            try {
                const response = await window.fetch('/api/unit-economics-1c/ozon/prices/calculate', {
                    method: 'POST', signal: controller.signal,
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'fetch' },
                    body: JSON.stringify({ values: session.values, initialize: !session.initialized, edits: session.edits,
                        tariff_id: session.tariffId || null, product_tariff_id: session.productTariffId || null }),
                });
                if (!response.ok) throw new Error('Не удалось пересчитать цены. Проверьте значения: скидка до 100%, комиссия в рублях не больше цены с акциями.');
                const result = await response.json();
                if (sessions.get(root) !== session || sequence !== session.sequence) return;
                session.values = result;
                session.edits = [];
                session.draft = {};
                session.initialized = true;
                session.helpPending = false;
                if (window.OzonCategoriesUI) window.OzonCategoriesUI.refresh(root, session);
                root.dispatchEvent(new CustomEvent('ozon-prices-changed', { detail: { productId: session.productId, values: result, modified: Boolean(session.modified || session.tariffId) } }));
                root.querySelectorAll('[data-ozon-calculator-input]').forEach(input => {
                    input.value = displayed(result[input.dataset.ozonCalculatorInput]);
                });
                bindHelp(root, session);
            } catch (error) {
                if (error.name !== 'AbortError' && sessions.get(root) === session && sequence === session.sequence) {
                    session.helpError = error.message;
                    session.helpPending = false;
                    bindHelp(root, session);
                    const currentStatus = root.querySelector('[data-ozon-calculator-status]');
                    if (currentStatus) {
                        currentStatus.textContent = error.message;
                        currentStatus.hidden = false;
                    }
                }
            } finally {
                if (sequence === session.sequence) {
                    root.querySelectorAll('[data-ozon-calculator-input]').forEach(input => input.removeAttribute('aria-busy'));
                }
            }
        }, 120);
    }

    return { render, reset, clear };
})();
