window.OzonCalculatorUI = (() => {
    'use strict';
    const sessions = new WeakMap();
    const fields = [
        { key: 'other_spend', label: 'Прочая реклама за сегодня, руб', order: 7, unit: '₽', signed: true },
        { key: 'other_drr', label: 'ДРР прочей рекламы', order: 7, signed: true, readOnly: true },
        { key: 'advertising_amount', legacy: 'advertisingRub', label: 'Реклама на штуку, руб', signed: true },
        { key: 'buyout_percent', metric: 'buyoutPercent', label: 'Процент выкупа', percent: true },
        { key: 'click_spend', metric: 'clicksSpend', label: 'Расходы за клики', signed: true },
        { key: 'click_drr', metric: 'clicksDrr', label: 'ДРР за клики', signed: true },
        { key: 'order_spend', metric: 'ordersSpend', label: 'Расходы за заказы', signed: true },
        { key: 'order_drr', metric: 'ordersDrr', label: 'ДРР за заказы', signed: true },
        { key: 'purchase', legacy: 'purchase', label: 'Закупочная стоимость', compactOrder: 4 },
        { key: 'fulfillment', legacy: 'fulfillment', label: 'Затраты на ФФ' },
        { key: 'storage_amount', legacy: 'storageTotal', label: 'Хранение, руб', signed: true },
        { key: 'acquiring_percent', legacy: 'acquiringPercent', label: 'Эквайринг, %', percent: true },
        { key: 'acquiring_amount', legacy: 'acquiringRub', label: 'Эквайринг, руб' },
        { key: 'team_percent', legacy: 'team', label: 'Комиссия компании, %', percent: true },
        { key: 'team_amount', legacy: 'teamRub', label: 'Комиссия компании, руб' },
        { key: 'vat_percent', label: 'Налог НДС, %', percent: true, order: 18 },
        { key: 'vat_amount', legacy: 'vatRub', label: 'Налог НДС, руб' },
        { key: 'usn_percent', label: 'Налог УСН, %', percent: true, order: 20 },
        { key: 'usn_amount', legacy: 'secondaryTaxRub', label: 'Налог УСН, руб' },
    ];
    const inputFormat = new Intl.NumberFormat('en-US', { useGrouping: false, maximumFractionDigits: 2 });
    const numericValue = value => value == null ? null : Number(value);
    const display = value => value == null ? '' : inputFormat.format(Number(value));
    const moneyFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const percentFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const rub = value => value == null ? '—' : moneyFormat.format(Number(value)) + ' ₽';
    const percent = value => value == null ? '—' : percentFormat.format(Number(value));
    const expensePairs = ['acquiring', 'vat', 'usn'];

    function pairModified(session, prefix) {
        return session.modified.has(prefix + '_percent') || session.modified.has(prefix + '_amount');
    }

    async function refreshExpenses(root, session) {
        if (session.expensesController) session.expensesController.abort();
        const controller = new AbortController();
        session.expensesController = controller;
        try {
            const response = await window.fetch('/api/unit-economics-1c/ozon/cabinets/' + encodeURIComponent(session.product.store_slug), {
                signal: controller.signal, cache: 'no-store', headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
            });
            if (!response.ok || response.redirected) throw new Error('Не удалось обновить ставки кабинета. Показаны последние загруженные значения; повторная проверка — при возвращении во вкладку.');
            const record = await response.json();
            if (sessions.get(root) !== session || session.expensesController !== controller) return;
            const expenses = record.values.expenses;
            let changed = false;
            expensePairs.forEach(prefix => {
                const key = prefix + '_percent';
                if (numericValue(session.expenses[key]) !== numericValue(expenses[key]) && !pairModified(session, prefix)) {
                    edit(session, key, expenses[key]);
                    changed = true;
                }
            });
            session.expenses = expenses;
            session.expensesUpdatedAt = record.updated_at;
            session.product.cabinet = { ...session.product.cabinet, expenses };
            session.settingsError = '';
            if (changed) queue(root, session);
            else paint(root, session);
        } catch (error) {
            if (error.name !== 'AbortError' && sessions.get(root) === session && session.expensesController === controller) {
                session.settingsError = error.message;
                paint(root, session);
            }
        }
    }

    function source(product) {
        const data = product.calculator || {};
        const details = product.details || {};
        const expenses = (product.cabinet || {}).expenses || {};
        const price = key => {
            const value = (product.price || {})[key] || {};
            return value.state === 'conflict' ? null : value.amount ?? null;
        };
        return {
            advertising_week: data.advertising_week ?? null,
            seller_price: price('seller_price'), buyer_price: price('black_price'),
            wallet_price: price('green_price'),
            commission_amount: (product.commission || {}).amount ?? null,
            logistics_amount: display(((product.logistics || {}).snapshot || {}).actual_per_unit) || null,
            orders_count: data.orders_count ?? null, other_spend: data.other_spend ?? null,
            other_breakdown: data.other_breakdown ?? null,
            turnover: data.turnover ?? null, buyout_percent: data.buyout_percent ?? null,
            click_spend: data.click_spend ?? null, order_spend: data.order_spend ?? null,
            click_drr: (data.clicks || {}).drr ?? null, order_drr: (data.orders || {}).drr ?? null,
            purchase: details.purchase_cost ?? null, fulfillment: details.fulfillment_cost ?? null,
            storage_amount: display(((product.storage || {}).snapshot || {}).per_unit) || null,
            team_percent: details.team_commission_percent ?? null,
            acquiring_percent: expenses.acquiring_percent ?? null,
            vat_percent: expenses.vat_percent ?? null, usn_percent: expenses.usn_percent ?? null,
        };
    }

    function clear(root) {
        window.OzonCalculatorHelp.clear(root);
        const session = sessions.get(root);
        if (session) {
            window.clearTimeout(session.timer);
            window.clearTimeout(session.refreshTimer);
            if (session.controller) session.controller.abort();
            if (session.expensesController) session.expensesController.abort();
            root.removeEventListener('ozon-prices-changed', session.pricesChanged);
            root.removeEventListener('ozon-prices-pending', session.pricesPending);
            root.removeEventListener('ozon-logistics-changed', session.logisticsChanged);
            window.removeEventListener('focus', session.refresh);
            window.removeEventListener('storage', session.settingsChanged);
            document.removeEventListener('visibilitychange', session.refresh);
        }
        sessions.delete(root);
    }

    function reset(root, product) {
        clear(root);
        if (window.OzonAdvertisingUI) window.OzonAdvertisingUI.renderCalculator(root, product);
        render(root, product);
    }

    function edit(session, key, value) {
        const next = { field: key, value };
        if (session.edits.length && session.edits[session.edits.length - 1].field === key) session.edits[session.edits.length - 1] = next;
        else session.edits.push(next);
    }

    function render(root, product) {
        const calculator = root.querySelector('#ue1c-panel-economics > .ue1c-calculator:not(.ue1c-placeholder-calculator)');
        if (!calculator) return;
        const container = calculator.querySelector('.ue1c-calculator-inputs');
        let session = sessions.get(root);
        if (!session || session.productId !== product.id) {
            clear(root);
            session = { productId: product.id, product, values: source(product), expenses: { ...(product.cabinet || {}).expenses },
                edits: [], draft: {}, invalid: new Set(), modified: new Set(), sequence: 0, initialized: false, changed: false };
            session.pricesChanged = event => {
                if (event.detail.productId !== session.productId) return;
                const prices = event.detail.values;
                session.pricePending = false;
                if (event.detail.modified) session.changed = true;
                edit(session, 'seller_price', prices.seller_price);
                edit(session, 'buyer_price', prices.black_price);
                edit(session, 'wallet_price', prices.green_price);
                edit(session, 'commission_amount', prices.commission_amount);
                queue(root, session);
            };
            session.pricesPending = event => {
                if (event.detail.productId !== session.productId) return;
                session.pricePending = true;
                paint(root, session);
            };
            session.logisticsChanged = event => {
                if (event.detail.productId !== session.productId) return;
                edit(session, 'logistics_amount', display(event.detail.total) || null);
                session.changed = true;
                queue(root, session);
            };
            root.addEventListener('ozon-prices-changed', session.pricesChanged);
            root.addEventListener('ozon-prices-pending', session.pricesPending);
            root.addEventListener('ozon-logistics-changed', session.logisticsChanged);
            sessions.set(root, session);
            session.refresh = () => {
                if (document.visibilityState === 'hidden') return;
                window.clearTimeout(session.refreshTimer);
                session.refreshTimer = window.setTimeout(() => refreshExpenses(root, session), 100);
            };
            session.settingsChanged = event => {
                if (event.key !== 'checkstock.ozon.cabinet-updated' || !event.newValue) return;
                try {
                    if (JSON.parse(event.newValue).store === session.product.store_slug) session.refresh();
                } catch {}
            };
            window.addEventListener('focus', session.refresh);
            window.addEventListener('storage', session.settingsChanged);
            document.addEventListener('visibilitychange', session.refresh);
            session.refresh();
        }
        session.product = product;
        product.cabinet = { ...product.cabinet, expenses: session.expenses };
        const totalDrr = container.querySelector('[data-calculator-input="drr"]');
        const totalDrrRow = totalDrr.closest('.ue1c-calculator-row');
        totalDrrRow.hidden = false;
        totalDrrRow.firstElementChild.textContent = 'Общий ДРР с выкупом';
        totalDrrRow.style.setProperty('--compact-order', '2');
        totalDrrRow.style.setProperty('--expanded-order', '4');
        totalDrr.dataset.ozonTotalDrr = '';
        totalDrr.readOnly = true;
        totalDrr.disabled = false;
        totalDrr.placeholder = '—';
        totalDrr.removeAttribute('min');
        totalDrr.removeAttribute('max');
        totalDrr.setAttribute('aria-label', 'Общий ДРР с выкупом');
        fields.forEach(spec => {
            let input;
            if (spec.metric) input = container.querySelector('[data-ozon-metric-row="' + spec.metric + '"] input');
            else if (spec.legacy) input = container.querySelector('[data-calculator-input="' + spec.legacy + '"]');
            else input = container.querySelector('[data-ozon-scenario-input="' + spec.key + '"]');
            if (!input) {
                const row = document.createElement('label');
                row.className = 'ue1c-calculator-row ue1c-calculator-row--expanded';
                row.style.setProperty('--expanded-order', String(spec.order));
                const caption = document.createElement('span');
                caption.textContent = spec.label;
                const field = document.createElement('span');
                field.className = 'ue1c-calculator-field';
                input = document.createElement('input');
                const unit = document.createElement('b');
                unit.textContent = spec.unit || '%';
                field.append(input, unit);
                row.append(caption, field);
                container.append(row);
            }
            const row = input.closest('.ue1c-calculator-row');
            if (spec.compactOrder != null) row.style.setProperty('--compact-order', String(spec.compactOrder));
            if (spec.key === 'advertising_amount') {
                row.hidden = false;
                row.firstElementChild.textContent = spec.label;
                row.style.setProperty('--expanded-order', '7');
            }
            if (spec.key === 'vat_amount') row.style.setProperty('--expanded-order', '19');
            if (spec.key === 'usn_amount') row.style.setProperty('--expanded-order', '21');
            input.dataset.ozonScenarioInput = spec.key;
            input.type = 'number';
            input.min = spec.signed ? '-1000000000000' : '0';
            input.max = spec.percent ? '100' : '1000000000000';
            input.step = 'any';
            input.disabled = false;
            input.readOnly = Boolean(spec.readOnly);
            input.placeholder = '—';
            input.setAttribute('aria-label', spec.label);
            if (spec.readOnly) {
                input.oninput = null;
                return;
            }
            input.oninput = () => {
                session.changed = true;
                session.modified.add(spec.key);
                session.draft[spec.key] = input.value;
                if (!input.checkValidity()) session.invalid.add(spec.key);
                else {
                    session.invalid.delete(spec.key);
                    edit(session, spec.key, input.value === '' ? null : input.value);
                }
                queue(root, session);
            };
        });
        let breakdown = container.querySelector('[data-ozon-other-breakdown]');
        if (!breakdown) {
            breakdown = document.createElement('details');
            breakdown.className = 'ue1c-calculator-row ue1c-calculator-row--expanded ozon-advertising-breakdown';
            breakdown.style.setProperty('--expanded-order', '7');
            breakdown.dataset.ozonOtherBreakdown = '';
            const summary = document.createElement('summary');
            summary.textContent = 'Состав прочей рекламы';
            breakdown.append(summary, document.createElement('ul'));
            container.append(breakdown);
        }
        let note = container.querySelector('[data-ozon-scenario-note]');
        if (!note) {
            note = document.createElement('p');
            note.className = 'ozon-scenario-note';
            note.dataset.ozonScenarioNote = '';
            note.setAttribute('role', 'status');
            container.append(note);
        }
        paint(root, session);
        if (!session.initialized || session.edits.length) queue(root, session);
    }

    function paint(root, session) {
        paintTotalDrr(root, session);
        root.querySelectorAll('[data-ozon-scenario-input]').forEach(input => {
            const key = input.dataset.ozonScenarioInput;
            const unavailable = input.readOnly && (session.pending || session.pricePending || !session.initialized || session.error || session.invalid.size);
            input.value = unavailable ? '' : Object.hasOwn(session.draft, key) ? session.draft[key] : display(session.values[key]);
            window.OzonCalculatorHelp.bind(input.closest('.ue1c-calculator-row'), () =>
                window.OzonCalculatorHelpContent.calculator(session, key, input.getAttribute('aria-label')));
        });
        const breakdown = root.querySelector('[data-ozon-other-breakdown] ul');
        const waiting = session.pending || session.pricePending || !session.initialized;
        const lines = session.error || session.invalid.size ? ['Проверьте введённые значения.'] : waiting ? ['Пересчитываем…'] :
            window.OzonAdvertisingUI.otherBreakdownLines(session.values.other_breakdown);
        breakdown.replaceChildren(...lines.map(text => {
            const item = document.createElement('li');
            item.textContent = text;
            return item;
        }));
        const note = root.querySelector('[data-ozon-scenario-note]');
        note.textContent = session.error || session.settingsError || '';
        note.hidden = !note.textContent;
        note.classList.toggle('is-error', Boolean(session.error || session.settingsError));
        paintResult(root, session);
        window.OzonCalculatorHelp.refresh();
        root.dispatchEvent(new CustomEvent('ozon-calculator-changed', { detail: { productId: session.productId } }));
    }

    function paintTotalDrr(root, session) {
        const input = root.querySelector('[data-ozon-total-drr]');
        if (!input) return;
        const values = session.values;
        const waiting = session.pending || session.pricePending || !session.initialized;
        const unavailable = waiting || session.error || session.invalid.size;
        input.value = unavailable ? '' : display(values.total_drr);
        window.OzonCalculatorHelp.bind(input.closest('.ue1c-calculator-row'), () =>
            window.OzonCalculatorHelpContent.calculator(session, 'total_drr', 'Общий ДРР с выкупом'));
    }

    function paintResult(root, session) {
        const node = root.querySelector('#ue1c-price-metrics');
        if (!node) return;
        const result = session.values.result || {};
        const waiting = session.pending || session.pricePending || !session.initialized;
        const unavailable = waiting || session.error || session.invalid.size;
        const margin = unavailable ? null : result.margin;
        const roi = unavailable ? null : result.roi;
        Array.from(node.childNodes).forEach(item => {
            if (item.nodeType !== 1 || !item.hasAttribute('data-ozon-result')) item.remove();
        });
        [['Чистая прибыль на штуку', margin, rub, 'margin'], ['ROI', roi, value => value == null ? '—' : percent(value) + '%', 'roi']].forEach(([label, value, format, key]) => {
            const row = node.querySelector('[data-ozon-result="' + key + '"]') || document.createElement('div');
            row.dataset.ozonResult = key;
            if (!row.parentNode) {
                const title = document.createElement('span');
                title.textContent = label;
                row.append(title, document.createElement('strong'));
                node.append(row);
            }
            const amount = row.querySelector('strong');
            amount.textContent = format(value);
            amount.className = value == null ? '' : Number(value) < 0 ? 'is-negative' : 'is-positive';
            window.OzonCalculatorHelp.bind(row, () => window.OzonCalculatorHelpContent.calculator(session, key, label));
        });
        const labels = window.OzonEconomicsUI.marginLabels;
        const missing = (result.missing || []).map(key => labels[key] || key);
        const status = document.createElement('p');
        status.className = 'ozon-scenario-note';
        status.textContent = session.error || (waiting ? 'Пересчитываем…' : missing.length ? 'Не хватает: ' + missing.join(', ') + '.'
            : '');
        if (status.textContent) node.append(status);
        node.removeAttribute('title');
    }

    function queue(root, session) {
        window.clearTimeout(session.timer);
        if (session.controller) session.controller.abort();
        const sequence = ++session.sequence;
        session.error = session.invalid.size ? 'Проверьте диапазоны: выкуп и ставки от 0 до 100%; отрицательные суммы допустимы для рекламы и корректировок хранения.' : '';
        session.pending = !session.invalid.size;
        paint(root, session);
        if (session.invalid.size) return;
        session.timer = window.setTimeout(async () => {
            session.controller = new AbortController();
            try {
                const response = await window.fetch('/api/unit-economics-1c/ozon/calculator/calculate', {
                    method: 'POST', signal: session.controller.signal,
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'fetch' },
                    body: JSON.stringify({ values: session.values, edits: session.edits }),
                });
                if (!response.ok) throw new Error('Не удалось пересчитать. Проверьте суммы, ставки и базы: ненулевой ДРР требует выкупа больше нуля, налог и комиссии должны соответствовать цене.');
                const result = await response.json();
                if (sessions.get(root) !== session || sequence !== session.sequence) return;
                session.values = result;
                session.edits = [];
                session.draft = {};
                session.initialized = true;
                session.pending = false;
                paint(root, session);
            } catch (error) {
                if (error.name !== 'AbortError' && sessions.get(root) === session && sequence === session.sequence) {
                    session.error = error.message;
                    session.pending = false;
                    paint(root, session);
                }
            }
        }, 150);
    }

    function snapshot(root) {
        const session = sessions.get(root);
        if (!session || !session.initialized || session.pending || session.pricePending || session.error || session.invalid.size || session.edits.length) return null;
        return { productId: session.productId, values: structuredClone(session.values) };
    }

    return { render, reset, clear, snapshot };
})();
