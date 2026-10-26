(function () {
    'use strict';

    var panel = document.querySelector('[data-stock-transit-panel]');
    if (!panel) return;

    var list = panel.querySelector('[data-stock-transit-list]');
    var status = panel.querySelector('[data-stock-transit-status]');
    var refreshButton = panel.querySelector('[data-stock-transit-refresh]');
    var viewButtons = panel.querySelectorAll('[data-stock-transit-view]');
    var filters = panel.querySelector('[data-stock-transit-filters]');
    var shortageFilter = panel.querySelector('[name="shortage_only"]');
    var filterCount = panel.querySelector('[data-stock-transit-count]');
    var expansionButtons = panel.querySelectorAll('[data-stock-transit-expand]');
    var currentData = null;
    var layout = document.getElementById('store-layout');
    var reopenDialog = document.querySelector('[data-stock-transit-reopen-dialog]');
    var reopenForm = document.querySelector('[data-stock-transit-reopen-form]');
    var reopenReason = document.querySelector('[data-stock-transit-reopen-reason]');
    var reopenDescription = document.querySelector('[data-stock-transit-reopen-description]');
    var reopenQuantity = document.querySelector('[data-stock-transit-reopen-quantity]');
    var reopenEffect = document.querySelector('[data-stock-transit-reopen-effect]');
    var reopenError = document.querySelector('[data-stock-transit-reopen-error]');
    var reopenSubmit = document.querySelector('[data-stock-transit-reopen-submit]');
    var pathParts = window.location.pathname.split('/').filter(Boolean);
    var storeSlug = pathParts[0] === 'stock' ? pathParts[1] : '';
    var requestNumber = 0;
    var currentView = 'active';
    var supplyExpansion = new Map();
    var supplyTableFilters = new Map();
    var reopenContext = null;
    var reopenBusy = false;
    var numberFormat = new Intl.NumberFormat('ru-RU');
    var wbSupplyStatuses = new Set(['2', '3', '4', '6']);
    var fboErrors = {
        fbo_invalid_fulfillment: 'ФФ не найден. Обновите список и выберите ФФ заново.',
        fbo_fulfillment_required: 'Выберите ФФ и дождитесь проверки остатков.',
        fbo_fulfillment_locked: 'Поставка уже проведена. Изменить ФФ списания нельзя.',
        fbo_invalid_request: 'Не удалось определить запрос. Обновите страницу и повторите.',
        fbo_request_conflict: 'Данные изменились во время операции. Обновите движения и повторите.',
        fbo_supply_missing: 'Поставка не найдена в текущем снимке.',
        fbo_supply_unconfirmed: 'Статус поставки не подтверждён. Обновите поставки FBO.',
        fbo_invalid_link: 'Состав выбранных отгрузок должен точно совпадать с поставкой.',
        fbo_invalid_return: 'Возврат доступен только после подтверждённой отмены поставки.',
        fbo_manual_disabled: 'FBO закрывается автоматически по статусу поставки маркетплейса.',
    };

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
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            timeZone: 'Europe/Moscow',
        }).format(date);
    }

    function message(text, isError) {
        status.textContent = text || '';
        status.classList.toggle('ff-select-status--bad', Boolean(isError));
    }

    function request(url, options) {
        return window.CheckStockMutation.fetch(url, options).then(function (response) {
            return response
                .json()
                .catch(function () {
                    return {};
                })
                .then(function (data) {
                    if (!response.ok || data.ok === false) {
                        if (fboErrors[data.code || data.error]) throw new Error(fboErrors[data.code || data.error]);
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
            quantities.appendChild(
                el('span', '', 'Возвращено ' + numberFormat.format(item.cancelled_quantity || 0)),
            );
        }
        if (currentView === 'active') {
            quantities.appendChild(
                el('b', '', 'Осталось ' + numberFormat.format(item.remaining_quantity || 0)),
            );
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
        return (
            {
                in_transit: 'В пути',
                partial: 'Расхождение / частично',
                received: 'Принято',
                cancelled: 'Отменено',
            }[value] || value
        );
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
        return (receipt.items || [])
            .map(function (item) {
                return item.article + ' × ' + numberFormat.format(item.quantity || 0);
            })
            .join(', ');
    }

    function renderTimeline(batch) {
        var timeline = el('div', 'stock-transit-timeline');
        timeline.appendChild(
            timelineEvent(
                'sent',
                'Отправлено ' + numberFormat.format(batch.sent_units || 0) + ' ед.',
                formatDate(batch.sent_at) + ' · ' + (batch.sent_by_name || 'пользователь'),
                batch.note || '',
                '',
            ),
        );
        (batch.receipts || []).forEach(function (receipt) {
            timeline.appendChild(
                timelineEvent(
                    'received',
                    'Принято ' + numberFormat.format(receipt.received_units || 0) + ' ед.',
                    formatDate(receipt.received_at) + ' · ' + (receipt.user_name || 'пользователь'),
                    receipt.note || '',
                    receiptDetails(receipt),
                ),
            );
        });
        if (batch.status === 'cancelled') {
            timeline.appendChild(
                timelineEvent(
                    'cancelled',
                    'Возвращено на исходный ФФ ' + numberFormat.format(batch.cancelled_units || 0) + ' ед.',
                    formatDate(batch.cancelled_at) + ' · ' + (batch.cancelled_by_name || 'пользователь'),
                    batch.cancellation_reason || '',
                    '',
                ),
            );
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
                items.push({
                    item_id: Number(input.getAttribute('data-transit-item-id')),
                    quantity: quantity,
                });
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
            body: JSON.stringify({ items: items, note: note ? note.value.trim() : '' }),
        })
            .then(function (data) {
                message(
                    data.status === 'received'
                        ? 'Партия принята полностью'
                        : 'Часть партии принята, остаток отмечен как расхождение',
                    false,
                );
                reloadStocks();
                return refresh();
            })
            .catch(function (error) {
                message('Ошибка: ' + error.message, true);
            })
            .finally(function () {
                button.disabled = false;
            });
    }

    function cancel(batch, card, button) {
        var reasonInput = card.querySelector('[data-transit-cancel-reason]');
        var reason = reasonInput ? reasonInput.value.trim() : '';
        if (!reason) {
            message('Для отмены обязательно укажите причину', true);
            if (reasonInput) reasonInput.focus();
            return;
        }
        if (!window.confirm('Отменить остаток партии №' + batch.id + ' и вернуть его на исходный ФФ?'))
            return;
        button.disabled = true;
        message('Отменяем остаток партии №' + batch.id + '...', false);
        request('/stock/' + storeSlug + '/transfers/' + batch.id + '/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
            body: JSON.stringify({ reason: reason }),
        })
            .then(function () {
                message('Непринятый остаток возвращён на исходный ФФ', false);
                reloadStocks();
                return refresh();
            })
            .catch(function (error) {
                message('Ошибка: ' + error.message, true);
            })
            .finally(function () {
                button.disabled = false;
            });
    }

    function showReopenError(text) {
        if (!reopenError) return;
        reopenError.textContent = text || '';
        reopenError.hidden = !text;
    }

    function openReopenForm(batch, button) {
        if (
            !reopenDialog ||
            !reopenForm ||
            !reopenReason ||
            !reopenDescription ||
            !reopenQuantity ||
            !reopenSubmit
        ) {
            message('Не удалось открыть форму возврата приёмки', true);
            return;
        }
        reopenContext = { batch: batch, button: button };
        reopenForm.reset();
        showReopenError('');
        reopenDescription.textContent =
            'Партия №' +
            batch.id +
            ' вернётся в статус «В пути». ' +
            batch.to_fulfillment +
            ' · ' +
            batch.to_marketplace;
        reopenQuantity.textContent = numberFormat.format(batch.received_units || 0) + ' ед.';
        if (reopenEffect) {
            reopenEffect.textContent =
                batch.kind === 'fbo_shipment'
                    ? 'Вернётся в путь без изменения остатков ФФ и FBO'
                    : 'Со склада назначения будет снято';
        }
        reopenDialog.showModal();
        window.setTimeout(function () {
            reopenReason.focus();
        }, 0);
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
            body: JSON.stringify({ reason: reason }),
        })
            .then(function () {
                reopenBusy = false;
                reopenDialog.close();
                message('Приёмка отменена, товар снова числится в пути', false);
                reloadStocks();
                return refresh();
            })
            .catch(function (error) {
                showReopenError(error.message);
                message('Ошибка: ' + error.message, true);
            })
            .finally(function () {
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
        var isFbo = batch.kind === 'fbo_shipment';
        title.appendChild(
            el('strong', '', (isFbo ? 'Отгрузка на FBO' : 'Перемещение между ФФ') + ' · партия №' + batch.id),
        );
        title.appendChild(
            el('small', '', formatDate(batch.sent_at) + ' · ' + (batch.sent_by_name || 'пользователь')),
        );
        head.appendChild(title);
        head.appendChild(
            el('span', 'stock-transit-state stock-transit-state--' + batch.status, statusLabel(batch.status)),
        );
        card.appendChild(head);
        card.appendChild(
            el(
                'div',
                'stock-transit-route',
                batch.from_fulfillment +
                    ' · ' +
                    batch.from_marketplace +
                    ' → ' +
                    batch.to_fulfillment +
                    ' · ' +
                    batch.to_marketplace,
            ),
        );
        if (batch.note && currentView === 'active')
            card.appendChild(el('p', 'stock-transit-note', batch.note));
        if (isFbo) {
            card.appendChild(
                el(
                    'p',
                    'stock-transit-note',
                    'Ранее созданная отгрузка. Свяжите её с поставкой маркетплейса, чтобы статус обновлялся автоматически.',
                ),
            );
        }

        var summary = el('div', 'stock-transit-summary');
        summary.appendChild(el('span', '', 'Отправлено: ' + numberFormat.format(batch.sent_units || 0)));
        summary.appendChild(el('span', '', 'Принято: ' + numberFormat.format(batch.received_units || 0)));
        if (batch.cancelled_units > 0) {
            summary.appendChild(
                el('span', '', 'Возвращено: ' + numberFormat.format(batch.cancelled_units || 0)),
            );
        }
        summary.appendChild(
            el(
                'b',
                '',
                currentView === 'active'
                    ? 'В пути: ' + numberFormat.format(batch.remaining_units || 0)
                    : batch.status === 'received'
                      ? 'Закрыто: принято полностью'
                      : 'Закрыто: остаток возвращён',
            ),
        );
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
            receiveButton.addEventListener('click', function () {
                receive(batch, card, receiveButton);
            });
            receiveActions.appendChild(note);
            receiveActions.appendChild(receiveButton);
            card.appendChild(receiveActions);
        }

        if (batch.can_reopen) {
            var reopenActions = el(
                'div',
                'stock-transit-actions stock-transit-actions--cancel stock-transit-actions--reopen',
            );
            var reopenButton = el('button', 'btn-secondary stock-transit-cancel', 'Вернуть приёмку в путь');
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
            cancelButton.addEventListener('click', function () {
                cancel(batch, card, cancelButton);
            });
            cancelActions.appendChild(reason);
            cancelActions.appendChild(cancelButton);
            card.appendChild(cancelActions);
        }
        return card;
    }

    function supplyAction(movement, action, button, batchIds, fulfillment) {
        var mp = marketplace();
        var controls = button.closest('.stock-fbo-card').querySelectorAll('[data-fbo-action-control]');
        controls.forEach(function (control) { control.disabled = true; });
        button.disabled = true;
        var payload = { marketplace: mp, supply_key: movement.supply.key,
            action: action, batch_ids: batchIds || [] };
        if (fulfillment !== undefined) payload.fulfillment = fulfillment;
        return request('/stock/' + storeSlug + '/fbo-stock?supply=' +
            encodeURIComponent(movement.supply.key) + '&mp=' + encodeURIComponent(mp), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
            body: JSON.stringify(payload),
        }).then(function (data) {
            if (action !== 'select_fulfillment') reloadStocks();
            return refresh().then(function () {
                var issue = data.movement.issue;
                var shortages = data.movement.shortages || [];
                if (shortages.length) message('Остатков на выбранном ФФ не хватает. Подробности — внутри поставки.', true);
                else if (action === 'select_fulfillment') message(data.movement.fulfillment ?
                    'ФФ выбран. ' + (data.movement.can_post ? 'Остатков хватает, поставку можно провести.' :
                        'Проведение пока недоступно: проверьте сообщение внутри поставки.') : 'Выбор ФФ сброшен.', false);
                else message(issue ? 'Поставка не проведена. Проверьте сообщение внутри.' :
                    'Движение поставки обновлено.', Boolean(issue));
            });
        }).catch(function (error) {
            if (button.tagName === 'SELECT') button.value = movement.fulfillment || '';
            message(error.message, true);
        }).finally(function () {
            controls.forEach(function (control) { control.disabled = false; });
            button.disabled = false;
        });
    }

    function supplyIdentity(supply) {
        return storeSlug + '|' + marketplace() + '|' + supply.key;
    }

    function renderFulfillment(movement) {
        var section = el('div', 'stock-fbo-fulfillment');
        var supply = movement.supply;
        if (movement.can_select_fulfillment) {
            var label = el('label');
            label.appendChild(el('span', '', 'ФФ для поставки №' + (supply.number || supply.supply_id)));
            var select = el('select');
            select.setAttribute('data-fbo-action-control', '');
            var empty = el('option', '', 'Выберите ФФ');
            empty.value = '';
            select.appendChild(empty);
            var fulfillments = currentData ? currentData.fbo.fulfillments || [] : [];
            fulfillments.forEach(function (name) {
                var option = el('option', '', name);
                option.value = name;
                select.appendChild(option);
            });
            select.value = movement.fulfillment || '';
            select.addEventListener('change', function () {
                supplyAction(movement, 'select_fulfillment', select, [], select.value);
            });
            label.appendChild(select);
            section.appendChild(label);
            if (!fulfillments.length) section.appendChild(el('p', '', 'В справочнике пока нет ФФ.'));
        } else if (movement.fulfillment) {
            section.appendChild(el('strong', '', 'ФФ: ' + movement.fulfillment));
        }
        if (movement.can_post) {
            var post = el('button', 'btn-primary', 'Провести');
            post.type = 'button';
            post.setAttribute('data-fbo-action-control', '');
            post.addEventListener('click', function () {
                supplyAction(movement, 'post', post, [], movement.fulfillment);
            });
            section.appendChild(post);
        }
        if (movement.shortages && movement.shortages.length) {
            var errors = el('div', 'stock-fbo-shortage-errors');
            errors.setAttribute('role', 'alert');
            errors.appendChild(el('strong', '', 'Проверка ФФ: ' + (movement.fulfillment || movement.checked_fulfillment)));
            movement.shortages.forEach(function (shortage) {
                errors.appendChild(el('p', '', 'Товар ' + shortage.article + ': не хватает для проведения поставки ' +
                    numberFormat.format(shortage.missing) + ' шт.'));
            });
            if (!movement.fulfillment) errors.appendChild(el('p', '', 'Выбор ФФ сброшен. Выберите ФФ заново.'));
            section.appendChild(errors);
        }
        return section;
    }

    function supplyRows(movement) {
        var rows = new Map();
        (movement.supply.items || []).forEach(function (item, index) {
            var key = item.article || item.sku || item.barcode || String(index);
            var row = rows.get(key);
            if (!row) {
                row = { article: item.article, name: item.name, barcodes: new Set(), quantity: 0,
                    accepted: 0, allocations: [] };
                rows.set(key, row);
            }
            if (item.barcode) row.barcodes.add(item.barcode);
            if (!row.name) row.name = item.name;
            row.quantity += item.quantity;
            row.accepted = row.accepted == null || item.accepted_quantity == null ? null :
                row.accepted + item.accepted_quantity;
        });
        movement.allocations.forEach(function (part) {
            var row = rows.get(part.article);
            if (!row) {
                row = { article: part.article, name: part.name, barcodes: new Set(), quantity: 0,
                    accepted: null, allocations: [] };
                if (part.barcode) row.barcodes.add(part.barcode);
                rows.set(part.article, row);
            }
            row.allocations.push(part);
        });
        return Array.from(rows.values());
    }

    function renderSupplyBody(movement, rows, legacy) {
        var supply = movement.supply;
        var body = el('div', 'inbound-card-body');
        var meta = [];
        if (supply.campaign_name) meta.push(supply.campaign_name);
        if (supply.checked_at) meta.push('Проверено: ' + formatDate(supply.checked_at));
        if (movement.posted_at) meta.push('Проведена: ' + formatDate(movement.posted_at));
        if (meta.length) body.appendChild(el('p', 'inbound-card-note', meta.join(' · ')));
        if (supply.warning) body.appendChild(el('p', 'inbound-card-warning', supply.warning));
        if (supply.note) body.appendChild(el('p', 'inbound-card-note', supply.note));
        if (movement.state === 'historical') body.appendChild(el('p', 'inbound-card-note',
            'Историческая поставка: завершена до включения автоматического учёта. Повторного списания нет.'));
        body.appendChild(renderFulfillment(movement));
        var scroll = el('div', 'inbound-table-scroll stock-fbo-table-scroll');
        scroll.tabIndex = 0;
        scroll.setAttribute('role', 'region');
        scroll.setAttribute('aria-label', 'Товары поставки №' + (supply.number || supply.supply_id));
        var table = el('table', 'inbound-table stock-fbo-table');
        var tableKey = supplyIdentity(supply);
        table._tfFilters = supplyTableFilters.get(tableKey) || {};
        table.addEventListener('tablefilterchange', function () { supplyTableFilters.set(tableKey, table._tfFilters); });
        var head = el('thead');
        var headers = el('tr');
        ['Артикул', 'Баркод', 'Название', 'Списание по ФФ', 'В поставке', 'Принято', 'В пути', 'Не хватает, шт.'].forEach(function (label, index) {
            var th = el('th', index >= 4 ? 'inbound-num' : '', label);
            th.scope = 'col';
            th.setAttribute('data-filter-column', String(index));
            if (index >= 4) th.setAttribute('data-filter-type', 'number');
            headers.appendChild(th);
        });
        head.appendChild(headers);
        table.appendChild(head);
        var tbody = el('tbody');
        var shortages = new Map((movement.shortages || []).map(function (item) { return [item.article, item]; }));
        rows.forEach(function (item) {
            var shortage = shortages.get(item.article);
            var checked = movement.stock_checked;
            var missing = checked ? (shortage ? shortage.missing : 0) : null;
            var row = el('tr', missing > 0 ? 'stock-fbo-row--shortage' : '');
            row.appendChild(el('td', 'stock-fbo-code', item.article || '—'));
            row.appendChild(el('td', 'stock-fbo-code', Array.from(item.barcodes).join(', ') || '—'));
            row.appendChild(el('td', 'stock-fbo-name', item.name || '—'));
            var sources = el('td', 'stock-fbo-sources');
            var allocated = 0;
            item.allocations.forEach(function (part) {
                allocated += part.quantity;
                var source = el('span', 'stock-fbo-source');
                source.appendChild(el('span', '', part.fulfillment));
                source.appendChild(el('b', '', numberFormat.format(part.quantity)));
                sources.appendChild(source);
            });
            if (!item.allocations.length) sources.textContent = '—';
            row.appendChild(sources);
            row.appendChild(el('td', 'inbound-num', numberFormat.format(item.quantity)));
            row.appendChild(el('td', 'inbound-num', item.accepted == null ? '—' : numberFormat.format(item.accepted)));
            row.appendChild(el('td', 'inbound-num stock-fbo-in-transit',
                numberFormat.format(movement.state === 'active' ? allocated : 0)));
            var availability = el('td', 'inbound-num stock-fbo-shortage', missing == null ? '—' : numberFormat.format(missing));
            availability.title = shortage ? 'Нужно: ' + numberFormat.format(shortage.required) +
                ', доступно с учётом уже списанного: ' + numberFormat.format(shortage.available) :
                missing == null ? 'Выберите ФФ для проверки остатков.' : 'Нехватки нет';
            row.appendChild(availability);
            tbody.appendChild(row);
        });
        if (!rows.length) {
            var empty = el('tr', 'empty-row');
            var cell = el('td', '', 'Состав поставки пока не получен');
            cell.colSpan = 8;
            empty.appendChild(cell);
            tbody.appendChild(empty);
        }
        table.appendChild(tbody);
        scroll.appendChild(table);
        body.appendChild(scroll);
        if (window.CheckStockTableFilter) window.CheckStockTableFilter.refresh(table);
        body.appendChild(el('p', 'inbound-card-note stock-fbo-table-hint',
            'Нехватка проверяется только на выбранном ФФ и площадке с учётом уже списанного для этой поставки. До выбора ФФ — прочерк. 0 — нехватки нет. В пути — проведённое количество до статуса «Принято».'));
        if (movement.issue) body.appendChild(renderSupplyIssue(movement, legacy));
        return body;
    }

    function renderSupply(movement, legacy) {
        var supply = movement.supply;
        var rows = supplyRows(movement);
        var card = el('article', 'inbound-card stock-fbo-card');
        var details = el('details', 'inbound-card stock-fbo-details');
        var key = supplyIdentity(supply);
        details.setAttribute('data-fbo-supply-key', key);
        details.open = supplyExpansion.get(key) === true;
        var head = el('summary');
        var title = el('div');
        title.appendChild(el('strong', 'inbound-card-title', 'Поставка №' + (supply.number || supply.supply_id)));
        var mpLabel = { WB: 'Wildberries', OZON: 'Ozon', 'YANDEX MARKET': 'Яндекс Маркет' };
        title.appendChild(el('small', '', (mpLabel[marketplace()] || marketplace()) + ' · FBO · Позиций: ' + rows.length));
        head.appendChild(title);
        var route = el('div', 'inbound-card-route');
        route.appendChild(el('span', '', supply.warehouse || 'Склад не указан'));
        if (supply.transit_warehouse) route.appendChild(el('small', '', 'Через: ' + supply.transit_warehouse));
        if (supply.planned_at) route.appendChild(el('small', '', 'План: ' + formatDate(supply.planned_at)));
        else if (supply.created_at) route.appendChild(el('small', '', 'Создана: ' + formatDate(supply.created_at)));
        head.appendChild(route);
        var state = el('div');
        var stage = supply.unavailable ? 'unknown' : supply.stage;
        state.appendChild(el('span', 'inbound-badge inbound-badge--' + stage, supply.status_label || supply.status));
        state.appendChild(el('small', 'stock-fbo-processing', movement.processed ? 'Обработанная' : 'Необработанная'));
        if (movement.fulfillment) state.appendChild(el('small', '', 'ФФ: ' + movement.fulfillment));
        else if (movement.allocations.length) state.appendChild(el('small', '', 'ФФ: ' +
            Array.from(new Set(movement.allocations.map(function (row) { return row.fulfillment; }))).join(', ')));
        if (movement.issue) state.appendChild(el('small', 'stock-fbo-needs-action', 'Требует внимания'));
        else if (movement.state === 'historical') state.appendChild(el('small', '', 'Историческая поставка'));
        else state.appendChild(el('small', '', movement.posted_at ? 'Проведена' : 'Ещё не проведена'));
        head.appendChild(state);
        var summary = el('div', 'inbound-card-numbers');
        summary.appendChild(el('strong', '', numberFormat.format(supply.quantity || 0) + ' шт. в поставке'));
        var allocated = movement.allocations.reduce(function (sum, row) { return sum + row.quantity; }, 0);
        summary.appendChild(el('small', '', 'Списано с ФФ: ' + numberFormat.format(allocated)));
        summary.appendChild(el('span', 'stock-fbo-in-transit',
            'В пути: ' + numberFormat.format(movement.state === 'active' ? allocated : 0)));
        head.appendChild(summary);
        var toggle = el('span', 'stock-fbo-toggle');
        toggle.appendChild(el('span', 'stock-fbo-show', 'Показать товары'));
        toggle.appendChild(el('span', 'stock-fbo-hide', 'Скрыть товары'));
        var chevron = el('span', 'inbound-chevron', '›');
        chevron.setAttribute('aria-hidden', 'true');
        toggle.appendChild(chevron);
        head.appendChild(toggle);
        details.appendChild(head);
        var body = null;
        if (details.open) {
            body = renderSupplyBody(movement, rows, legacy);
            details.appendChild(body);
        }
        details.addEventListener('toggle', function () {
            if (!details.isConnected) return;
            supplyExpansion.set(key, details.open);
            if (details.open && !body) {
                body = renderSupplyBody(movement, rows, legacy);
                details.appendChild(body);
            }
        });
        card.appendChild(details);
        return card;
    }

    function renderSupplyIssue(movement, legacy) {
        var supply = movement.supply;
        var alert = el('div', movement.issue === 'shortage' ? 'stock-fbo-actions' : 'stock-fbo-alert');
        alert.setAttribute('role', 'alert');
        var labels = {
            shortage: movement.shortages.length ? 'Проведение недоступно: на выбранном ФФ не хватает остатков.' : 'Проверьте ФФ перед проведением поставки.',
            legacy_review: 'Есть ранее списанные отгрузки. Свяжите их с поставкой, чтобы избежать повторного списания.',
            unconfirmed: 'Данные поставки не подтверждены. Проверка ФФ использует сохранённый состав. Проведение недоступно до подтверждения данных.',
            cancelled_review: 'Поставка отменена площадкой. Подтвердите возврат товара на ФФ после фактического возврата.',
        };
        alert.appendChild(el('strong', '', labels[movement.issue] || movement.issue));
        if (movement.issue === 'cancelled_review') {
            var returned = el('button', 'btn-secondary', 'Товар вернулся — вернуть на ФФ');
            returned.type = 'button';
            returned.addEventListener('click', function () { supplyAction(movement, 'return', returned); });
            alert.appendChild(returned);
        }
        if (movement.issue === 'legacy_review') {
            var selectors = [];
            var articles = new Set((supply.items || []).map(function (row) { return row.article; }));
            legacy.filter(function (batch) { return batch.items.some(function (row) { return articles.has(row.article); }); })
                .forEach(function (batch) {
                    var label = el('label', 'stock-fbo-link');
                    var check = el('input');
                    check.type = 'checkbox';
                    check.value = batch.id;
                    selectors.push(check);
                    label.appendChild(check);
                    label.appendChild(el('span', '', 'Отгрузка №' + batch.id + ' · ' + batch.fulfillment + ' · ' +
                        batch.items.map(function (row) { return row.article + ' × ' + numberFormat.format(row.quantity); }).join(', ')));
                    alert.appendChild(label);
                });
            if (selectors.length) {
                var link = el('button', 'btn-secondary', 'Учесть выбранные отгрузки');
                link.type = 'button';
                link.addEventListener('click', function () {
                    var ids = selectors.filter(function (input) { return input.checked; })
                        .map(function (input) { return Number(input.value); });
                    if (!ids.length) { message('Выберите отгрузки для этой поставки.', true); return; }
                    supplyAction(movement, 'link', link, ids);
                });
                alert.appendChild(link);
            }
        }
        return alert;
    }

    function filterValue(name) {
        return filters ? filters.elements.namedItem(name).value.trim() : '';
    }

    function filterDate(value) {
        if (!value) return '';
        if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        var parts = new Intl.DateTimeFormat('en-CA', {
            year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'Europe/Moscow',
        }).formatToParts(date);
        var part = function (type) { return parts.find(function (item) { return item.type === type; }).value; };
        return part('year') + '-' + part('month') + '-' + part('day');
    }

    function matchesFilters(date, numbers, destination, state) {
        var from = filterValue('date_from');
        var to = filterValue('date_to');
        var day = filterDate(date);
        if ((from || to) && !day) return false;
        if (from && day < from || to && day > to) return false;
        var term = filterValue('number').toLocaleLowerCase('ru');
        if (term && !numbers.some(function (value) { return String(value || '').toLocaleLowerCase('ru').includes(term); })) return false;
        return (!filterValue('destination') || filterValue('destination') === destination) &&
            (!filterValue('status') || filterValue('status') === state);
    }

    function fillFilter(name, values, emptyLabel) {
        if (!filters) return;
        var select = filters.elements.namedItem(name);
        var selected = select.value;
        if (selected && !values.has(selected)) values.set(selected, select.selectedOptions[0].textContent);
        select.replaceChildren(el('option', '', emptyLabel));
        select.options[0].value = '';
        Array.from(values).sort(function (a, b) { return a[1].localeCompare(b[1], 'ru'); }).forEach(function (entry) {
            var option = el('option', '', entry[1]);
            option.value = entry[0];
            select.appendChild(option);
        });
        select.value = selected;
    }

    function displayedSupplies(fbo) {
        return (fbo.movements || []).filter(function (movement) {
            return marketplace() !== 'WB' || wbSupplyStatuses.has(movement.supply.status);
        });
    }

    function compareSupplies(a, b) {
        var processed = Number(Boolean(a.processed)) - Number(Boolean(b.processed));
        if (processed) return processed;
        var today = Date.parse(filterDate(new Date().toISOString()));
        var first = filterDate(a.supply.planned_at);
        var second = filterDate(b.supply.planned_at);
        var distanceA = first ? Math.abs(Date.parse(first) - today) : Infinity;
        var distanceB = second ? Math.abs(Date.parse(second) - today) : Infinity;
        return distanceA - distanceB || first.localeCompare(second) || a.supply.key.localeCompare(b.supply.key);
    }

    function filterOptions(batches, fbo) {
        var destinations = new Map();
        var states = new Map();
        displayedSupplies(fbo).forEach(function (movement) {
            var supply = movement.supply;
            if (supply.warehouse) destinations.set(supply.warehouse, supply.warehouse);
            states.set('supply:' + supply.status, supply.status_label || supply.status);
        });
        var linked = new Set(fbo.linked_batch_ids || []);
        batches.filter(function (batch) { return !linked.has(batch.id); }).forEach(function (batch) {
            if (batch.to_fulfillment) destinations.set(batch.to_fulfillment, batch.to_fulfillment);
            states.set('batch:' + batch.status, 'Перемещение · ' + statusLabel(batch.status));
        });
        fillFilter('destination', destinations, 'Все направления');
        fillFilter('status', states, 'Все статусы');
    }

    function renderFiltered() {
        if (currentData) render(currentData.batches, currentData.fbo);
    }

    function render(batches, fbo) {
        list.querySelectorAll('[data-fbo-supply-key]').forEach(function (details) {
            supplyExpansion.set(details.getAttribute('data-fbo-supply-key'), details.open);
        });
        list.innerHTML = '';
        var linked = new Set(fbo.linked_batch_ids || []);
        batches = batches.filter(function (batch) { return !linked.has(batch.id); });
        var supplies = displayedSupplies(fbo).filter(function (movement) {
            var closed = !movement.issue && ['accepted', 'historical', 'cancelled'].includes(movement.state);
            return currentView === 'history' ? closed : !closed;
        }).sort(compareSupplies);
        var total = supplies.length + batches.length;
        var onlyShortages = shortageFilter && shortageFilter.checked;
        supplies = supplies.filter(function (movement) {
            var supply = movement.supply;
            return (!onlyShortages || (movement.shortages || []).some(function (item) { return item.missing > 0; })) &&
                matchesFilters(supply.planned_at || supply.created_at,
                [supply.number, supply.supply_id, supply.order_id], supply.warehouse, 'supply:' + supply.status);
        });
        batches = batches.filter(function (batch) {
            return !onlyShortages && matchesFilters(batch.sent_at, [batch.id], batch.to_fulfillment, 'batch:' + batch.status);
        });
        var invalidDates = filterValue('date_from') && filterValue('date_to') && filterValue('date_from') > filterValue('date_to');
        if (filterCount) filterCount.textContent = invalidDates ? 'Дата «от» должна быть не позже даты «до».' :
            'Показано: ' + numberFormat.format(supplies.length + batches.length) + ' из ' + numberFormat.format(total);
        expansionButtons.forEach(function (button) { button.disabled = !supplies.length; });
        supplies.forEach(function (movement) { list.appendChild(renderSupply(movement, fbo.legacy || [])); });
        if (!batches.length && !supplies.length) {
            list.appendChild(
                el(
                    'div',
                    'stock-transit-empty',
                    total ? 'По выбранным фильтрам поставок и перемещений нет' : currentView === 'history'
                        ? 'Завершённых движений пока нет'
                        : 'Активных партий в пути нет',
                ),
            );
            return;
        }
        batches.forEach(function (batch) {
            list.appendChild(renderBatch(batch));
        });
    }

    function refresh() {
        if (!storeSlug) return Promise.resolve();
        var currentRequest = ++requestNumber;
        currentData = null;
        if (filterCount) filterCount.textContent = '';
        expansionButtons.forEach(function (button) { button.disabled = true; });
        if (refreshButton) refreshButton.disabled = true;
        message(currentView === 'history' ? 'Загружаем историю...' : 'Загружаем партии...', false);
        return Promise.all([request(
            '/stock/' +
                storeSlug +
                '/transfers/in-transit?mp=' +
                encodeURIComponent(marketplace()) +
                '&view=' +
                encodeURIComponent(currentView),
        ), request('/stock/' + storeSlug + '/fbo-stock?mp=' + encodeURIComponent(marketplace()))])
            .then(function (data) {
                if (currentRequest !== requestNumber) return;
                currentData = { batches: data[0].batches || [], fbo: data[1] };
                filterOptions(currentData.batches, currentData.fbo);
                renderFiltered();
                message('', false);
            })
            .catch(function (error) {
                if (currentRequest !== requestNumber) return;
                list.innerHTML = '';
                list.appendChild(
                    el(
                        'div',
                        'stock-transit-empty stock-transit-empty--error',
                        'Не удалось загрузить партии',
                    ),
                );
                message('Ошибка: ' + error.message, true);
            })
            .finally(function () {
                if (currentRequest === requestNumber && refreshButton) refreshButton.disabled = false;
            });
    }

    expansionButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            var open = button.getAttribute('data-stock-transit-expand') === 'true';
            list.querySelectorAll('[data-fbo-supply-key]').forEach(function (details) {
                supplyExpansion.set(details.getAttribute('data-fbo-supply-key'), open);
                details.open = open;
            });
        });
    });
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
    if (filters) {
        filters.addEventListener('submit', function (event) { event.preventDefault(); renderFiltered(); });
        filters.addEventListener('input', renderFiltered);
        filters.addEventListener('change', renderFiltered);
        filters.addEventListener('reset', function (event) {
            event.preventDefault();
            Array.from(filters.elements).forEach(function (field) {
                if (field.type === 'checkbox') field.checked = false;
                else if (field.name) field.value = '';
            });
            renderFiltered();
        });
    }
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
})();
