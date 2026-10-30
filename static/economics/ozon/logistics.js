window.OzonLogisticsUI = (() => {
    'use strict';
    const sessions = new WeakMap();
    const fields = [
        ['buyout_percent', 'Выкуп среди оплаченных доставок', '%'],
        ['forward', 'Средняя логистика туда', '₽'],
        ['reverse', 'Средняя логистика обратно', '₽'],
        ['handling', 'Обработка на обратную перевозку', '₽'],
        ['last_mile', 'До места выдачи на один выкуп', '₽'],
        ['extra', 'Прочие расходы доставки на выкуп', '₽'],
        ['markup', 'Дополнительная наценка на доставку', '₽'],
    ];
    const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const inputFormat = new Intl.NumberFormat('en-US', { useGrouping: false, maximumFractionDigits: 2 });
    const amount = value => value == null ? '—' : numbers.format(Number(value));
    const inputValue = value => value == null ? '' : inputFormat.format(Number(value));
    const volumeValue = value => value == null ? '' : String(value);

    function element(tag, className, text) {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function clear(root) {
        window.OzonCalculatorHelp.clear(root);
        const session = sessions.get(root);
        if (session) {
            window.clearTimeout(session.timer);
            if (session.controller) session.controller.abort();
        }
        sessions.delete(root);
    }

    function reset(root, product) {
        clear(root);
        render(root, product);
    }

    function render(root, product) {
        const calculator = root.querySelector('#ue1c-panel-economics > .ue1c-calculator:not(.ue1c-placeholder-calculator)');
        const inputs = calculator && calculator.querySelector('.ue1c-calculator-inputs');
        if (!inputs) return;
        const legacy = inputs.querySelector('[data-calculator-input="logistics"]');
        if (legacy) legacy.closest('.ue1c-calculator-row').hidden = true;
        let session = sessions.get(root);
        if (!session || session.productId !== product.id) {
            clear(root);
            session = { productId: product.id, changed: {}, open: false, sequence: 0 };
            sessions.set(root, session);
        }
        session.source = product.logistics || {};
        session.snapshot = session.source.snapshot || null;
        const snapshot = session.snapshot;
        const old = inputs.querySelector('[data-ozon-logistics]');
        if (old) old.remove();
        const block = element('details', 'ym-logistics ozon-logistics ue1c-calculator-row--expanded');
        block.dataset.ozonLogistics = '';
        block.style.setProperty('--expanded-order', '17');
        block.open = session.open;
        block.ontoggle = () => { session.open = block.open; };
        const summary = element('summary', 'ym-logistics-summary');
        summary.append(element('span', 'ym-logistics-heading', 'Логистика Ozon FBO на один выкуп'));
        summary.append(input(root, session, 'total', 'Логистика Ozon FBO на один выкуп', '₽'));
        summary.append(element('span', 'ym-logistics-toggle', '⌄'));
        const content = element('div', 'ym-logistics-fields');
        const volume = element('label', 'ue1c-calculator-row');
        volume.append(element('span', '', 'Объём товара'), input(root, session, 'volume_l', 'Объём товара, л', 'л'));
        volume.querySelector('input').readOnly = true;
        content.append(volume);
        fields.forEach(([key, label, unit]) => {
            const row = element('label', 'ue1c-calculator-row');
            row.append(element('span', '', label), input(root, session, key, label, unit));
            row.title = tooltip(snapshot, key);
            content.append(row);
        });
        const model = element('div', 'ue1c-calculator-row');
        model.dataset.ozonLogisticsReference = 'modeled';
        model.append(element('span', '', 'Оценка по формуле в дневном снимке'), element('strong', '', amount(snapshot && snapshot.modeled_per_unit) + ' ₽'));
        content.append(model);
        const actual = element('div', 'ue1c-calculator-row');
        actual.dataset.ozonLogisticsReference = 'actual';
        actual.append(element('span', '', 'Исходные начисления на выкуп'), element('strong', '', amount(snapshot && snapshot.actual_per_unit) + ' ₽'));
        content.append(actual);
        const note = element('p', 'ym-logistics-note');
        note.dataset.ozonLogisticsNote = '';
        note.setAttribute('role', 'status');
        block.append(summary, content, note);
        inputs.append(block);
        session.block = block;
        updateNote(session);
        if (Object.keys(session.changed).length) queue(root, session);
    }

    function input(root, session, key, label, unit) {
        const field = element('span', 'ue1c-calculator-field');
        const input = element('input', '');
        input.type = 'number';
        input.min = key === 'volume_l' ? '0.000000001' : key === 'buyout_percent' ? '0' : '-1000000000';
        input.max = key === 'buyout_percent' ? '100' : '1000000000';
        input.step = 'any';
        input.placeholder = '—';
        input.dataset.ozonLogisticsInput = key;
        input.setAttribute('aria-label', label);
        const format = key === 'volume_l' ? volumeValue : inputValue;
        input.value = format(Object.hasOwn(session.changed, key) ? session.changed[key] : original(session, key));
        input.onclick = event => event.stopPropagation();
        input.onkeydown = event => event.stopPropagation();
        input.onblur = () => {
            if (input.value !== '' && input.checkValidity()) input.value = format(input.value);
        };
        input.oninput = () => {
            if (input.value === '' && !input.validity.badInput) {
                delete session.changed[key];
                input.value = format(original(session, key));
            }
            else session.changed[key] = input.value;
            if (key !== 'total') delete session.changed.total;
            queue(root, session);
        };
        field.append(input, element('b', '', unit));
        return field;
    }

    function original(session, key) {
        if (!session.snapshot) return key === 'markup' ? 0 : null;
        if (key === 'volume_l') return Number(session.snapshot.volume_l) > 0 ? session.snapshot.volume_l : null;
        return key === 'total' ? session.snapshot.actual_per_unit : session.snapshot.inputs[key];
    }

    function updateNote(session, message) {
        session.helpMessage = message || '';
        const snapshot = session.snapshot;
        const notes = [];
        if (session.source.error) notes.push('Ошибка последнего обновления.');
        if (snapshot && snapshot.state === 'insufficient') notes.push('Нет оставленных покупателями товаров для распределения расходов.');
        if (message) notes.push(message);
        const note = session.block.querySelector('[data-ozon-logistics-note]');
        note.textContent = notes.join(' ');
        note.hidden = !note.textContent;
        session.block.classList.toggle('is-incomplete', !snapshot || snapshot.state !== 'available' || session.source.stale || Boolean(session.source.error) || Boolean(message));
        session.block.querySelectorAll('[data-ozon-logistics-input], [data-ozon-logistics-reference]').forEach(input => {
            const key = input.dataset.ozonLogisticsInput || input.dataset.ozonLogisticsReference;
            const row = input.closest('label, summary, [data-ozon-logistics-reference]');
            const title = input.getAttribute('aria-label') || row.firstElementChild.firstChild.textContent;
            window.OzonCalculatorHelp.bind(row, () => window.OzonCalculatorHelpContent.logistics(session, key, title));
        });
        window.OzonCalculatorHelp.refresh();
    }

    function tooltip(snapshot, key) {
        if (!snapshot) return 'Нет дневного снимка. Можно задать значение для сценария.';
        const sum = part => amount(snapshot.charges.filter(charge => charge.part === part).reduce((total, charge) => total + Number(charge.amount), 0));
        const formulas = {
            forward: sum('forward') + ' ₽ / ' + snapshot.forward_units + ' оплаченных единиц туда.',
            reverse: sum('reverse') + ' ₽ / ' + snapshot.reverse_units + ' оплаченных единиц обратно.',
            handling: sum('handling') + ' ₽ / ' + snapshot.reverse_units + ' обратных перевозок. 15 ₽ не добавляются автоматически.',
            last_mile: sum('last_mile') + ' ₽ / ' + snapshot.retained_units + ' оставленных покупателями единиц. Включены Ozon и партнёры.',
            extra: sum('extra') + ' ₽ / ' + snapshot.retained_units + ' оставленных покупателями единиц. Упаковка и дополнительные услуги доставки.',
            buyout_percent: '(' + snapshot.received_units + ' получено − ' + snapshot.returned_units + ' возвращено) / ' + snapshot.paid_units + ' единиц с расходами × 100. Отдельный выкуп за 30 дней для логистики.',
            markup: 'Начисления уже включают стоимость маршрута. Дополнительная наценка по умолчанию 0 ₽; поле для сценария.',
        };
        return formulas[key] || '';
    }

    function queue(root, session) {
        const publish = total => root.dispatchEvent(new CustomEvent('ozon-logistics-changed', {
            detail: { productId: session.productId, total },
        }));
        publish(null);
        window.clearTimeout(session.timer);
        if (session.controller) session.controller.abort();
        const sequence = ++session.sequence;
        session.result = null;
        const inputs = [...session.block.querySelectorAll('[data-ozon-logistics-input]')];
        const total = inputs.find(input => input.dataset.ozonLogisticsInput === 'total');
        if (inputs.some(input => !input.checkValidity())) {
            total.value = '';
            updateNote(session, 'Проверьте значения: суммы от −1 млрд до 1 млрд ₽, выкуп от 0 до 100%.');
            return;
        }
        if (!Object.keys(session.changed).length) {
            total.value = inputValue(original(session, 'total'));
            fields.forEach(([key]) => session.block.querySelector('[data-ozon-logistics-input="' + key + '"]').value = inputValue(original(session, key)));
            updateNote(session);
            publish(original(session, 'total'));
            return;
        }
        if (!Object.hasOwn(session.changed, 'total')) total.value = '';
        updateNote(session, 'Пересчитываем логистику…');
        session.timer = window.setTimeout(async () => {
            const controller = new AbortController();
            session.controller = controller;
            const payload = {};
            fields.forEach(([key]) => { payload[key] = Object.hasOwn(session.changed, key) ? session.changed[key] : original(session, key); });
            payload.total = session.changed.total ?? null;
            try {
                const response = await window.fetch('/api/unit-economics-1c/ozon/logistics/calculate', {
                    method: 'POST', signal: controller.signal,
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'fetch' },
                    body: JSON.stringify(payload),
                });
                if (!response.ok) throw new Error('Не удалось пересчитать логистику. Проверьте исходные значения.');
                const result = await response.json();
                if (sessions.get(root) !== session || session.sequence !== sequence) return;
                session.result = result;
                total.value = inputValue(result.total);
                ['forward', 'reverse'].forEach(key => {
                    if (!Object.hasOwn(session.changed, key)) {
                        session.block.querySelector('[data-ozon-logistics-input="' + key + '"]').value =
                            inputValue(result.inputs ? result.inputs[key] : original(session, key));
                    }
                });
                publish(result.total);
                updateNote(session, result.total == null ? 'Недостаточно данных для расчёта или выкуп равен нулю.' : undefined);
            } catch (error) {
                if (error.name !== 'AbortError' && sessions.get(root) === session && session.sequence === sequence) updateNote(session, error.message);
            }
        }, 150);
    }

    return { render, reset, clear };
})();
