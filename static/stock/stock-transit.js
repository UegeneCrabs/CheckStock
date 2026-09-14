(function () {
    'use strict';

    var panel = document.querySelector('[data-stock-transit-panel]');
    if (!panel) return;

    var list = panel.querySelector('[data-stock-transit-list]');
    var status = panel.querySelector('[data-stock-transit-status]');
    var refreshButton = panel.querySelector('[data-stock-transit-refresh]');
    var viewButtons = panel.querySelectorAll('[data-stock-transit-view]');
    var layout = document.getElementById('store-layout');
    var reopenDialog = document.querySelector('[data-stock-transit-reopen-dialog]');
    var reopenForm = document.querySelector('[data-stock-transit-reopen-form]');
    var reopenReason = document.querySelector('[data-stock-transit-reopen-reason]');
    var reopenDescription = document.querySelector('[data-stock-transit-reopen-description]');
    var reopenQuantity = document.querySelector('[data-stock-transit-reopen-quantity]');
    var reopenError = document.querySelector('[data-stock-transit-reopen-error]');
    var reopenSubmit = document.querySelector('[data-stock-transit-reopen-submit]');
    var pathParts = window.location.pathname.split('/').filter(Boolean);
    var storeSlug = pathParts[0] === 'stock' ? pathParts[1] : '';
    var requestNumber = 0;
    var currentView = 'active';
    var reopenContext = null;
    var reopenBusy = false;
    var numberFormat = new Intl.NumberFormat('ru-RU');

    function marketplace() {
        return layout ? layout.getAttribute('data-marketplace') || 'WB' : 'WB';
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function formatDate(value) {
        if (!value) return '—';
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) return value;
        return new Intl.DateTimeFormat('ru-RU', {
            day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit'
        }).format(date);
    }

    function message(text, isError) {
        status.textContent = text || '';
        status.classList.toggle('ff-select-status--bad', Boolean(isError));
    }

    function request(url, options) {
        return fetch(url, options).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok || data.ok === false) {
                    throw new Error(data.error || data.detail || 'Не удалось выполнить действие');
                }
                return data;
            });
        });
    }

    function reloadStocks() {
        if (window.stockTable && window.stockTable.refresh) window.stockTable.refresh();
    }

    function renderItem(batch, item, canReceive) {
        var row = el('div', 'stock-transit-item' + (canReceive ? '' : ' stock-transit-item--readonly'));
        var identity = el('div', 'stock-transit-item-identity');
        identity.appendChild(el('strong', '', item.name || item.to_article));
        identity.appendChild(el('small', '', 'Арт. ' + item.to_article));
        row.appendChild(identity);

        var quantities = el('div', 'stock-transit-item-quantities');
        quantities.appendChild(el('span', '', 'Отправлено ' + numberFormat.format(item.sent_quantity || 0)));
        quantities.appendChild(el('span', '', 'Принято ' + numberFormat.format(item.received_quantity || 0)));
        if (item.cancelled_quantity > 0) {
            quantities.appendChild(el('span', '', 'Возвращено ' + numberFormat.format(item.cancelled_quantity || 0)));
        }
        if (currentView === 'active') {
            quantities.appendChild(el('b', '', 'Осталось ' + numberFormat.format(item.remaining_quantity || 0)));
        }
        row.appendChild(quantities);

        if (canReceive && item.remaining_quantity > 0) {
            var input = el('input', 'select-control stock-transit-quantity');
            input.type = 'number';
            input.min = '0';
            input.max = String(item.remaining_quantity);
            input.step = '1';
            input.value = String(item.remaining_quantity);
            input.setAttribute('aria-label', 'Принять количество для артикула ' + item.to_article);
            input.setAttribute('data-transit-item-id', item.id);
            input.setAttribute('data-transit-max', item.remaining_quantity);
            row.appendChild(input);
        }
        return row;
    }

    function statusLabel(value) {
        return {
            in_transit: 'В пути',
            partial: 'Расхождение / частично',
            received: 'Принято',
            cancelled: 'Отменено'
        }[value] || value;
    }

    function timelineEvent(className, title, meta, note, details) {
        var event = el('div', 'stock-transit-event stock-transit-event--' + className);
        var marker = el('span', 'stock-transit-event-marker');
        marker.setAttribute('aria-hidden', 'true');
        event.appendChild(marker);
        var content = el('div', 'stock-transit-event-content');
        content.appendChild(el('strong', '', title));
        content.appendChild(el('small', '', meta));
        if (details) content.appendChild(el('span', 'stock-transit-event-details', details));
        if (note) content.appendChild(el('span', 'stock-transit-event-note', note));
        event.appendChild(content);
        return event;
    }

    function receiptDetails(receipt) {
        return (receipt.items || []).map(function (item) {
            return item.article + ' × ' + numberFormat.format(item.quantity || 0);
        }).join(', ');
    }

    function renderTimeline(batch) {
        var timeline = el('div', 'stock-transit-timeline');
        timeline.appendChild(timelineEvent(
            'sent',
            'Отправлено ' + numberFormat.format(batch.sent_units || 0) + ' ед.',
            formatDate(batch.sent_at) + ' · ' + (batch.sent_by_name || 'пользователь'),
            batch.note || '',
            ''
        ));
        (batch.receipts || []).forEach(function (receipt) {
            timeline.appendChild(timelineEvent(
                'received',
                'Принято ' + numberFormat.format(receipt.received_units || 0) + ' ед.',
                formatDate(receipt.received_at) + ' · ' + (receipt.user_name || 'пользователь'),
                receipt.note || '',
                receiptDetails(receipt)
            ));
        });
        if (batch.status === 'cancelled') {
            timeline.appendChild(timelineEvent(
                'cancelled',
                'Возвращено на исходный ФФ ' + numberFormat.format(batch.cancelled_units || 0) + ' ед.',
                formatDate(batch.cancelled_at) + ' · ' + (batch.cancelled_by_name || 'пользователь'),
                batch.cancellation_reason || '',
                ''
            ));
        }
        return timeline;
    }

    function receive(batch, card, button) {
        var items = [];
        card.querySelectorAll('[data-transit-item-id]').forEach(function (input) {
            var quantity = Number(input.value);
            var maximum = Number(input.getAttribute('data-transit-max'));
            if (!Number.isInteger(quantity) || quantity < 0 || quantity > maximum) {
                input.classList.add('input-error');
                return;
            }
            input.classList.remove('input-error');
            if (quantity > 0) {
                items.push({ item_id: Number(input.getAttribute('data-transit-item-id')), quantity: quantity });
            }
        });
        if (card.querySelector('.input-error')) {
            message('Проверьте количество принимаемого товара', true);
            return;
        }
        if (!items.length) {
            message('Укажите хотя бы одну принимаемую единицу', true);
            return;
        }
        var note = card.querySelector('[data-transit-receive-note]');
        button.disabled = true;
        message('Принимаем партию №' + batch.id + '...', false);
        request('/stock/' + storeSlug + '/transfers/' + batch.id + '/receive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
            body: JSON.stringify({ items: items, note: note ? note.value.trim() : '' })
        }).then(function (data) {
            message(data.status === 'received' ? 'Партия принята полностью' : 'Часть партии принята, остаток отмечен как расхождение', false);
            reloadStocks();
            return refresh();
        }).catch(function (error) {
            message('Ошибка: ' + error.message, true);
        }).finally(function () { button.disabled = false; });
    }

    function cancel(batch, card, button) {
        var reasonInput = card.querySelector('[data-transit-cancel-reason]');
        var reason = reasonInput ? reasonInput.value.trim() : '';
        if (!reason) {
            message('Для отмены обязательно укажите причину', true);
            if (reasonInput) reasonInput.focus();
            return;
        }
        if (!window.confirm('Отменить остаток партии №' + batch.id + ' и вернуть его на исходный ФФ?')) return;
        button.disabled = true;
        message('Отменяем остаток партии №' + batch.id + '...', false);
        request('/stock/' + storeSlug + '/transfers/' + batch.id + '/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
            body: JSON.stringify({ reason: reason })
        }).then(function () {
            message('Непринятый остаток возвращён на исходный ФФ', false);
            reloadStocks();
            return refresh();
        }).catch(function (error) {
            message('Ошибка: ' + error.message, true);
        }).finally(function () { button.disabled = false; });
    }

    function showReopenError(text) {
        if (!reopenError) return;
        reopenError.textContent = text || '';
        reopenError.hidden = !text;
    }

    function openReopenForm(batch, button) {
        if (
            !reopenDialog || !reopenForm || !reopenReason ||
            !reopenDescription || !reopenQuantity || !reopenSubmit
        ) {
            message('Не удалось открыть форму возврата приёмки', true);
            return;
        }
        reopenContext = { batch: batch, button: button };
        reopenForm.reset();
        showReopenError('');
        reopenDescription.textContent =
            'Партия №' + batch.id + ' вернётся в статус «В пути». ' +
            batch.to_fulfillment + ' · ' + batch.to_marketplace;
        reopenQuantity.textContent = numberFormat.format(batch.received_units || 0) + ' ед.';
        reopenDialog.showModal();
        window.setTimeout(function () { reopenReason.focus(); }, 0);
    }

    function submitReopenForm(event) {
        event.preventDefault();
        if (!reopenContext || reopenBusy) return;
        var reason = reopenReason.value.trim();
        if (!reason) {
            showReopenError('Укажите причину возврата приёмки в путь');
            reopenReason.focus();
            return;
        }

        var context = reopenContext;
        reopenBusy = true;
        context.button.disabled = true;
        reopenSubmit.disabled = true;
        reopenReason.disabled = true;
        showReopenError('');
        message('Возвращаем приёмку партии №' + context.batch.id + ' в путь...', false);
        request('/stock/' + storeSlug + '/transfers/' + context.batch.id + '/reopen', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
            body: JSON.stringify({ reason: reason })
        }).then(function () {
            reopenBusy = false;
            reopenDialog.close();
            message('Приёмка отменена, товар снова числится в пути', false);
            reloadStocks();
            return refresh();
        }).catch(function (error) {
            showReopenError(error.message);
            message('Ошибка: ' + error.message, true);
        }).finally(function () {
            reopenBusy = false;
            context.button.disabled = false;
            reopenSubmit.disabled = false;
            reopenReason.disabled = false;
        });
    }

    function renderBatch(batch) {
        var card = el('article', 'stock-transit-card');
        var head = el('div', 'stock-transit-card-head');
        var title = el('div');
        title.appendChild(el('strong', '', 'Партия №' + batch.id));
        title.appendChild(el('small', '', formatDate(batch.sent_at) + ' · ' + (batch.sent_by_name || 'пользователь')));
        head.appendChild(title);
        head.appendChild(el(
            'span',
            'stock-transit-state stock-transit-state--' + batch.status,
            statusLabel(batch.status)
        ));
        card.appendChild(head);
        card.appendChild(el(
            'div', 'stock-transit-route',
            batch.from_fulfillment + ' · ' + batch.from_marketplace + ' → ' +
            batch.to_fulfillment + ' · ' + batch.to_marketplace
        ));
        if (batch.note && currentView === 'active') card.appendChild(el('p', 'stock-transit-note', batch.note));

        var summary = el('div', 'stock-transit-summary');
        summary.appendChild(el('span', '', 'Отправлено: ' + numberFormat.format(batch.sent_units || 0)));
        summary.appendChild(el('span', '', 'Принято: ' + numberFormat.format(batch.received_units || 0)));
        if (batch.cancelled_units > 0) {
            summary.appendChild(el('span', '', 'Возвращено: ' + numberFormat.format(batch.cancelled_units || 0)));
        }
        summary.appendChild(el(
            'b', '',
            currentView === 'active'
                ? 'В пути: ' + numberFormat.format(batch.remaining_units || 0)
                : (batch.status === 'received' ? 'Закрыто: принято полностью' : 'Закрыто: остаток возвращён')
        ));
        card.appendChild(summary);

        if (currentView === 'history') card.appendChild(renderTimeline(batch));

        var items = el('div', 'stock-transit-items');
        (batch.items || []).forEach(function (item) {
            if (currentView === 'history' || item.remaining_quantity > 0 || item.received_quantity > 0) {
                items.appendChild(renderItem(batch, item, batch.can_receive));
            }
        });
        card.appendChild(items);

        if (batch.can_receive) {
            var receiveActions = el('div', 'stock-transit-actions');
            var note = el('input', 'select-control');
            note.type = 'text';
            note.maxLength = 200;
            note.placeholder = 'Примечание к приёмке (необязательно)';
            note.setAttribute('data-transit-receive-note', '');
            var receiveButton = el('button', 'btn-primary', 'Принять указанное');
            receiveButton.type = 'button';
            receiveButton.addEventListener('click', function () { receive(batch, card, receiveButton); });
            receiveActions.appendChild(note);
            receiveActions.appendChild(receiveButton);
            card.appendChild(receiveActions);
        }

        if (batch.can_reopen) {
            var reopenActions = el(
                'div',
                'stock-transit-actions stock-transit-actions--cancel stock-transit-actions--reopen'
            );
            var reopenButton = el(
                'button',
                'btn-secondary stock-transit-cancel',
                'Вернуть приёмку в путь'
            );
            reopenButton.type = 'button';
            reopenButton.addEventListener('click', function () {
                openReopenForm(batch, reopenButton);
            });
            reopenActions.appendChild(reopenButton);
            card.appendChild(reopenActions);
        }

        if (batch.can_cancel) {
            var cancelActions = el('div', 'stock-transit-actions stock-transit-actions--cancel');
            var reason = el('input', 'select-control');
            reason.type = 'text';
            reason.maxLength = 200;
            reason.placeholder = 'Причина отмены обязательна';
            reason.setAttribute('data-transit-cancel-reason', '');
            var cancelButton = el('button', 'btn-secondary stock-transit-cancel', 'Отменить остаток');
            cancelButton.type = 'button';
            cancelButton.addEventListener('click', function () { cancel(batch, card, cancelButton); });
            cancelActions.appendChild(reason);
            cancelActions.appendChild(cancelButton);
            card.appendChild(cancelActions);
        }
        return card;
    }

    function render(batches) {
        list.innerHTML = '';
        if (!batches.length) {
            list.appendChild(el(
                'div', 'stock-transit-empty',
                currentView === 'history' ? 'Завершённых перемещений пока нет' : 'Активных партий в пути нет'
            ));
            return;
        }
        batches.forEach(function (batch) { list.appendChild(renderBatch(batch)); });
    }

    function refresh() {
        if (!storeSlug) return Promise.resolve();
        var currentRequest = ++requestNumber;
        if (refreshButton) refreshButton.disabled = true;
        message(currentView === 'history' ? 'Загружаем историю...' : 'Загружаем партии...', false);
        return request(
            '/stock/' + storeSlug + '/transfers/in-transit?mp=' + encodeURIComponent(marketplace()) +
            '&view=' + encodeURIComponent(currentView)
        )
            .then(function (data) {
                if (currentRequest !== requestNumber) return;
                render(data.batches || []);
                message('', false);
            })
            .catch(function (error) {
                if (currentRequest !== requestNumber) return;
                list.innerHTML = '';
                list.appendChild(el('div', 'stock-transit-empty stock-transit-empty--error', 'Не удалось загрузить партии'));
                message('Ошибка: ' + error.message, true);
            })
            .finally(function () {
                if (currentRequest === requestNumber && refreshButton) refreshButton.disabled = false;
            });
    }

    viewButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            var nextView = button.getAttribute('data-stock-transit-view');
            if (!nextView || nextView === currentView) return;
            currentView = nextView;
            viewButtons.forEach(function (item) {
                var selected = item.getAttribute('data-stock-transit-view') === currentView;
                item.classList.toggle('is-active', selected);
                item.setAttribute('aria-selected', selected ? 'true' : 'false');
            });
            refresh();
        });
    });
    if (reopenForm) reopenForm.addEventListener('submit', submitReopenForm);
    if (reopenDialog) {
        reopenDialog.querySelectorAll('[data-stock-transit-reopen-close]').forEach(function (button) {
            button.addEventListener('click', function () {
                if (!reopenBusy) reopenDialog.close();
            });
        });
        reopenDialog.addEventListener('cancel', function (event) {
            if (reopenBusy) event.preventDefault();
        });
        reopenDialog.addEventListener('click', function (event) {
            if (event.target === reopenDialog && !reopenBusy) reopenDialog.close();
        });
        reopenDialog.addEventListener('close', function () {
            var trigger = reopenContext && reopenContext.button;
            reopenContext = null;
            showReopenError('');
            if (trigger && trigger.isConnected) trigger.focus();
        });
    }
    if (refreshButton) refreshButton.addEventListener('click', refresh);
    window.stockTransit = { refresh: refresh };
    refresh();
}());
