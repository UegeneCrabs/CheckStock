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
    const routeFields = [
        ['origin', 'Кластер отправки', 'Выберите кластер'],
        ['destination', 'Кластер доставки', 'Выберите кластер'],
        ['return_cluster', 'Кластер возврата', 'В кластер доставки'],
    ];
    const volumeMessages = {
        volume_missing: 'Укажите положительный объём для оценки.',
        price_missing: 'Для выбора тарифа нужна цена с учётом акции.',
        route_missing: 'Для оценки по объёму выберите кластеры отправки и доставки.',
        route_unavailable: 'В справочнике нет тарифа для выбранной прямой или обратной перевозки.',
        tariff_unavailable: 'Нет действующей тарифной сетки. Оценка по объёму недоступна.',
    };
    const volumeActive = session => ['volume_l', ...routeFields.map(([key]) => key)].some(key => Object.hasOwn(session.changed, key));

    function element(tag, className, text) {
        const node = document.createElement(tag);
        node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function clear(root) {
        const session = sessions.get(root);
        if (session) {
            window.clearTimeout(session.timer);
            if (session.controller) session.controller.abort();
            if (session.tariffsController) session.tariffsController.abort();
            root.removeEventListener('ozon-prices-changed', session.pricesChanged);
            root.removeEventListener('ozon-prices-pending', session.pricesPending);
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
            const price = (product.price || {}).seller_price || {};
            session.sellerPrice = price.state === 'conflict' ? null : price.amount ?? null;
            session.pricesChanged = event => {
                if (event.detail.productId !== session.productId) return;
                session.sellerPrice = event.detail.values.seller_price;
                if (volumeActive(session)) queue(root, session);
            };
            session.pricesPending = event => {
                if (event.detail.productId !== session.productId || !volumeActive(session)) return;
                session.sellerPrice = null;
                queue(root, session);
            };
            root.addEventListener('ozon-prices-changed', session.pricesChanged);
            root.addEventListener('ozon-prices-pending', session.pricesPending);
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
        volume.title = 'Исходный объём упаковки Ozon: ' + volumeValue(original(session, 'volume_l')) +
            ' л. Правка включает оценку для выбранного маршрута. Габариты в Ozon и начисления не меняются.';
        content.append(volume);
        routeFields.forEach(([key, label, placeholder]) => {
            const row = element('label', 'ue1c-calculator-row');
            const select = element('select', '');
            select.dataset.ozonLogisticsRoute = key;
            select.setAttribute('aria-label', label);
            const empty = element('option', '', placeholder);
            empty.value = '';
            select.append(empty);
            select.onchange = () => {
                session.changed[key] = select.value || null;
                delete session.changed.total;
                delete session.changed.forward;
                delete session.changed.reverse;
                queue(root, session);
            };
            row.append(element('span', '', label), select);
            content.append(row);
        });
        const tariffNote = element('p', 'ym-logistics-note');
        tariffNote.dataset.ozonVolumeNote = '';
        content.append(tariffNote);
        fields.forEach(([key, label, unit]) => {
            const row = element('label', 'ue1c-calculator-row');
            row.append(element('span', '', label), input(root, session, key, label, unit));
            row.title = tooltip(snapshot, key);
            content.append(row);
        });
        const model = element('div', 'ue1c-calculator-row');
        model.append(element('span', '', 'Оценка по формуле в дневном снимке'), element('strong', '', amount(snapshot && snapshot.modeled_per_unit) + ' ₽'));
        content.append(model);
        const actual = element('div', 'ue1c-calculator-row');
        actual.append(element('span', '', 'Исходные начисления на выкуп'), element('strong', '', amount(snapshot && snapshot.actual_per_unit) + ' ₽'));
        content.append(actual);
        const note = element('p', 'ym-logistics-note');
        note.dataset.ozonLogisticsNote = '';
        note.setAttribute('role', 'status');
        block.append(summary, content, note);
        inputs.append(block);
        session.block = block;
        populateRoutes(session);
        if (!session.tariffs && !session.tariffsController) loadTariffs(root, session);
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
            if (key === 'volume_l') {
                delete session.changed.forward;
                delete session.changed.reverse;
            }
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

    function populateRoutes(session) {
        routeFields.forEach(([key]) => {
            const select = session.block.querySelector('[data-ozon-logistics-route="' + key + '"]');
            const choices = session.tariffs ? session.tariffs[key === 'origin' ? 'origins' : 'destinations'] : [];
            while (select.options.length > 1) select.remove(1);
            choices.forEach(value => {
                const option = element('option', '', value);
                option.value = value;
                select.append(option);
            });
            select.value = session.changed[key] || '';
            select.disabled = !session.tariffs || !session.tariffs.available;
        });
        const note = session.block.querySelector('[data-ozon-volume-note]');
        note.textContent = session.tariffError || (session.tariffs && !session.tariffs.available
            ? volumeMessages.tariff_unavailable
            : '');
        note.hidden = !note.textContent;
    }

    async function loadTariffs(root, session) {
        const controller = new AbortController();
        session.tariffsController = controller;
        try {
            const response = await window.fetch('/api/unit-economics-1c/ozon/logistics/tariffs', {
                signal: controller.signal, headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
            });
            if (!response.ok || response.redirected) throw new Error('Не удалось загрузить тарифы. Оценка по объёму недоступна; повторная загрузка — при открытии карточки.');
            const tariffs = await response.json();
            if (sessions.get(root) !== session) return;
            session.tariffs = tariffs;
        } catch (error) {
            if (error.name === 'AbortError' || sessions.get(root) !== session) return;
            session.tariffError = error.message;
        }
        populateRoutes(session);
    }

    function updateNote(session, message) {
        const snapshot = session.snapshot;
        const notes = [];
        ['forward', 'reverse'].forEach(key => {
            const field = session.block.querySelector('[data-ozon-logistics-input="' + key + '"]');
            const row = field.closest('.ue1c-calculator-row');
            const direction = key === 'forward' ? 'туда' : 'обратно';
            const modeled = volumeActive(session);
            const manual = Object.hasOwn(session.changed, key);
            const label = modeled ? (manual ? 'Логистика ' + direction + ' вручную' : 'Тариф логистики ' + direction)
                : 'Средняя логистика ' + direction;
            row.firstElementChild.textContent = label;
            field.setAttribute('aria-label', label);
            row.title = modeled ? (manual ? 'Сумма задана вручную. Изменение объёма или маршрута возвращает тариф.'
                : 'Тариф выбранного маршрута по объёму и цене с учётом акции. Исходные начисления показаны отдельно.')
                : tooltip(snapshot, key);
        });
        if (session.source.error) notes.push('Ошибка последнего обновления.');
        if (snapshot && snapshot.state === 'insufficient') notes.push('Нет оставленных покупателями товаров для распределения расходов.');
        if (message) notes.push(message);
        if (volumeActive(session)) {
            notes.push('Оценка по объёму, а не фактические начисления.');
            const estimate = session.result && session.result.volume;
            if (estimate && estimate.state === 'available') {
                notes.push('Диапазон тарифа: свыше ' + volumeValue(estimate.lower_l) +
                    (estimate.upper_l == null ? ' л.' : ' до ' + volumeValue(estimate.upper_l) + ' л включительно.'));
                if (Object.hasOwn(session.changed, 'forward') || Object.hasOwn(session.changed, 'reverse')) notes.push('Ручные суммы перевозки имеют приоритет; новая правка объёма или маршрута возвращает тариф.');
            }
        }
        const note = session.block.querySelector('[data-ozon-logistics-note]');
        note.textContent = notes.join(' ');
        note.hidden = !note.textContent;
        session.block.classList.toggle('is-incomplete', !snapshot || snapshot.state !== 'available' || session.source.stale || Boolean(session.source.error) || Boolean(message));
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
        if (volumeActive(session)) {
            ['forward', 'reverse'].forEach(key => {
                if (!Object.hasOwn(session.changed, key)) {
                    session.block.querySelector('[data-ozon-logistics-input="' + key + '"]').value = '';
                }
            });
        }
        if (inputs.some(input => !input.checkValidity())) {
            total.value = '';
            updateNote(session, 'Проверьте значения: объём больше нуля, суммы от −1 млрд до 1 млрд ₽, выкуп от 0 до 100%.');
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
            if (volumeActive(session)) {
                payload.volume = {
                    volume_l: Object.hasOwn(session.changed, 'volume_l') ? session.changed.volume_l : original(session, 'volume_l'),
                    seller_price: session.sellerPrice,
                    origin: session.changed.origin ?? null,
                    destination: session.changed.destination ?? null,
                    return_cluster: session.changed.return_cluster ?? null,
                    forward_manual: Object.hasOwn(session.changed, 'forward'),
                    reverse_manual: Object.hasOwn(session.changed, 'reverse'),
                };
            }
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
                updateNote(session, result.manual ? undefined : result.volume && volumeMessages[result.volume.state] ||
                    (result.total == null ? 'Недостаточно данных для расчёта или выкуп равен нулю.' : undefined));
            } catch (error) {
                if (error.name !== 'AbortError' && sessions.get(root) === session && session.sequence === sequence) updateNote(session, error.message);
            }
        }, 150);
    }

    return { render, reset, clear };
})();
