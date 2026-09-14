(function () {
    'use strict';

    var root = document.querySelector('[data-supply-planner]');
    if (!root) return;

    var editable = root.getAttribute('data-editable') === '1';
    var wbRows = root.querySelector('[data-wb-supply-rows]');
    var wbRefresh = root.querySelector('[data-wb-refresh]');
    var wbFilter = root.querySelector('[data-wb-store-filter]');
    var wbDateFrom = root.querySelector('[data-wb-date-from]');
    var wbDateTo = root.querySelector('[data-wb-date-to]');
    var wbDateSort = root.querySelector('[data-wb-date-sort]');
    var wbSummary = root.querySelector('[data-wb-summary]');
    var wbUpdated = root.querySelector('[data-wb-updated]');
    var wbMessage = root.querySelector('[data-wb-message]');
    var manualRows = root.querySelector('[data-manual-supply-rows]');
    var manualForm = root.querySelector('[data-manual-supply-form]');
    var manualSubmit = root.querySelector('[data-manual-submit]');
    var manualCancel = root.querySelector('[data-manual-cancel]');
    var manualStatus = root.querySelector('[data-manual-form-status]');
    var manualReadonly = root.querySelector('[data-manual-readonly]');
    var wbSupplies = [];
    var manualSupplies = [];
    var manualLoaded = false;

    var escapeHtml = window.CheckStockUI.escapeHtml;

    function errorMessage(payload, fallback) {
        if (!payload) return fallback;
        if (payload.error) return payload.error;
        if (typeof payload.detail === 'string') return payload.detail;
        if (Array.isArray(payload.detail) && payload.detail.length) {
            return payload.detail
                .map(function (item) {
                    return item.msg || 'Некорректное значение';
                })
                .join('; ');
        }
        return fallback;
    }

    function request(url, options) {
        options = options || {};
        options.headers = Object.assign(
            {
                Accept: 'application/json',
                'X-Requested-With': 'fetch',
            },
            options.headers || {},
        );
        return fetch(url, options).then(function (response) {
            return response
                .json()
                .catch(function () {
                    return {};
                })
                .then(function (payload) {
                    if (!response.ok) throw new Error(errorMessage(payload, 'Ошибка запроса'));
                    return payload;
                });
        });
    }

    function dateValue(value) {
        var date = new Date(value || '');
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function formatDate(value) {
        var date = dateValue(value);
        if (!date) return 'Дата не указана';
        return new Intl.DateTimeFormat('ru-RU', {
            timeZone: 'Europe/Moscow',
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        }).format(date);
    }

    function isUrgent(value) {
        var date = dateValue(value);
        return Boolean(date && date.getTime() <= Date.now() + 36 * 60 * 60 * 1000);
    }

    function urgentLabel(value) {
        var date = dateValue(value);
        if (!date || !isUrgent(value)) return '';
        return date.getTime() < Date.now() ? 'Дата прошла' : 'Менее 36 часов';
    }

    function dateCell(value) {
        var label = urgentLabel(value);
        return window.CheckStockUI.render('stock/supplies/date-cell-2', {
            value: formatDate(value),
            content: label ? window.CheckStockUI.render('stock/supplies/date-cell', { label: label }) : '',
        });
    }

    function setMessage(node, message, isError) {
        node.textContent = message || '';
        node.hidden = !message;
        node.classList.toggle('is-error', Boolean(isError));
    }

    function selectTab(name) {
        root.querySelectorAll('[data-supply-tab]').forEach(function (button) {
            var active = button.getAttribute('data-supply-tab') === name;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        root.querySelectorAll('[data-supply-panel]').forEach(function (panel) {
            panel.hidden = panel.getAttribute('data-supply-panel') !== name;
        });
        if (name === 'manual' && !manualLoaded) loadManual();
    }

    root.querySelectorAll('[data-supply-tab]').forEach(function (button) {
        button.addEventListener('click', function () {
            selectTab(button.getAttribute('data-supply-tab'));
        });
    });

    function wbDestination(row) {
        var transit = row.transit_warehouse_name || '';
        var warehouse = row.warehouse_name || '';
        if (transit && warehouse) return transit + ' → ' + warehouse;
        return warehouse || transit || 'Не передан в списке WB';
    }

    function validateWbRange() {
        if (!wbDateFrom.value || !wbDateTo.value) return 'Укажите обе даты диапазона';
        if (!wbDateFrom.checkValidity() || !wbDateTo.checkValidity()) {
            return 'Даты можно выбирать только в пределах 3 месяцев от текущей даты';
        }
        if (wbDateFrom.value > wbDateTo.value) return 'Дата начала не может быть позже даты окончания';
        return '';
    }

    function renderWb(rows) {
        rows = (rows || []).slice().sort(function (left, right) {
            var difference =
                (dateValue(left.supply_date) || new Date(8640000000000000)) -
                (dateValue(right.supply_date) || new Date(8640000000000000));
            return wbDateSort.value === 'desc' ? -difference : difference;
        });
        if (!rows.length) {
            wbRows.innerHTML = window.CheckStockUI.render('stock/supplies/render-wb');
            return;
        }
        wbRows.innerHTML = rows
            .map(function (row) {
                var supplyId = row.supply_id || row.preorder_id || '—';
                return window.CheckStockUI.render('stock/supplies/render-wb-2', {
                    content: isUrgent(row.supply_date) ? 'is-urgent' : '',
                    supply_date: dateCell(row.supply_date),
                    store_name: row.store_name,
                    row: wbDestination(row),
                    content_2: row.supply_type || 'Не указан',
                    supplyId: supplyId,
                });
            })
            .join('');
    }

    function loadWb() {
        var rangeError = validateWbRange();
        if (rangeError) {
            setMessage(wbMessage, rangeError, true);
            return;
        }
        wbRefresh.disabled = true;
        wbRefresh.textContent = 'Загрузка…';
        wbRows.innerHTML = window.CheckStockUI.render('stock/supplies/load-wb');
        setMessage(wbMessage, '', false);
        var query = new URLSearchParams({
            date_from: wbDateFrom.value,
            date_to: wbDateTo.value,
        });
        if (wbFilter.value) query.set('store', wbFilter.value);
        request('/stock/planning/wb?' + query.toString())
            .then(function (payload) {
                wbSupplies = payload.supplies || [];
                var urgent = wbSupplies.filter(function (row) {
                    return isUrgent(row.supply_date);
                }).length;
                wbSummary.textContent =
                    wbSupplies.length + ' запланировано' + (urgent ? ' · ' + urgent + ' срочно' : '');
                wbUpdated.textContent =
                    'Период ' +
                    payload.date_from +
                    ' — ' +
                    payload.date_to +
                    ' · обновлено ' +
                    formatDate(payload.fetched_at);
                renderWb(wbSupplies);
                if (payload.errors && payload.errors.length) {
                    setMessage(
                        wbMessage,
                        payload.errors
                            .map(function (entry) {
                                return entry.store_name + ': ' + entry.error;
                            })
                            .join(' · '),
                        false,
                    );
                }
            })
            .catch(function (error) {
                wbSummary.textContent = 'Не удалось загрузить данные';
                wbRows.innerHTML = window.CheckStockUI.render('stock/supplies/load-wb-2');
                setMessage(wbMessage, error.message, true);
            })
            .finally(function () {
                wbRefresh.disabled = false;
                wbRefresh.textContent = 'Обновить из WB';
            });
    }

    wbRefresh.addEventListener('click', loadWb);
    wbFilter.addEventListener('change', loadWb);
    wbDateSort.addEventListener('change', function () {
        renderWb(wbSupplies);
    });

    function renderManual() {
        manualSupplies.sort(function (left, right) {
            return (
                (dateValue(left.delivery_at) || new Date(8640000000000000)) -
                (dateValue(right.delivery_at) || new Date(8640000000000000))
            );
        });
        if (!manualSupplies.length) {
            manualRows.innerHTML = window.CheckStockUI.render('stock/supplies/render-manual');
            return;
        }
        manualRows.innerHTML = manualSupplies
            .map(function (row) {
                var classes = [];
                if (isUrgent(row.delivery_at)) classes.push('is-urgent');
                if (row.ready) classes.push('is-ready');
                var disabled = editable ? '' : ' disabled';
                var actions = editable
                    ? window.CheckStockUI.render('stock/supplies/actions', { id: row.id, id_2: row.id })
                    : '—';
                return window.CheckStockUI.render('stock/supplies/render-manual-2', {
                    content: classes.join(' '),
                    delivery_at: dateCell(row.delivery_at),
                    content_2: row.store_name || row.store_slug,
                    origin: row.origin,
                    destination: row.destination,
                    supply_type: row.supply_type,
                    note: row.note,
                    id: row.id,
                    content_3: row.ready ? ' checked' : '',
                    disabled: disabled,
                    actions: actions,
                });
            })
            .join('');
    }

    function loadManual() {
        manualRows.innerHTML = window.CheckStockUI.render('stock/supplies/load-manual');
        request('/stock/planning/manual')
            .then(function (payload) {
                manualSupplies = payload.supplies || [];
                manualLoaded = true;
                renderManual();
            })
            .catch(function (error) {
                manualRows.innerHTML = window.CheckStockUI.render('stock/supplies/load-manual-2', {
                    message: error.message,
                });
            });
    }

    function resetManualForm() {
        manualForm.reset();
        manualForm.elements.supply_id.value = '';
        manualSubmit.textContent = 'Добавить поставку';
        manualCancel.hidden = true;
        manualStatus.textContent = '';
        manualStatus.classList.remove('is-error');
    }

    function editManual(id) {
        var row = manualSupplies.find(function (item) {
            return item.id === id;
        });
        if (!row) return;
        manualForm.elements.supply_id.value = row.id;
        manualForm.elements.store_slug.value = row.store_slug;
        manualForm.elements.delivery_at.value = String(row.delivery_at).slice(0, 16);
        manualForm.elements.origin.value = row.origin;
        manualForm.elements.destination.value = row.destination;
        manualForm.elements.supply_type.value = row.supply_type;
        manualForm.elements.note.value = row.note;
        manualForm.elements.ready.checked = Boolean(row.ready);
        manualSubmit.textContent = 'Сохранить изменения';
        manualCancel.hidden = false;
        manualForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    function replaceManual(row) {
        var index = manualSupplies.findIndex(function (item) {
            return item.id === row.id;
        });
        if (row.ready) {
            if (index !== -1) manualSupplies.splice(index, 1);
            renderManual();
            return;
        }
        if (index === -1) manualSupplies.push(row);
        else manualSupplies[index] = row;
        renderManual();
    }

    manualForm.addEventListener('submit', function (event) {
        event.preventDefault();
        if (!editable) return;
        var id = Number(manualForm.elements.supply_id.value || 0);
        var payload = {
            store_slug: manualForm.elements.store_slug.value,
            delivery_at: manualForm.elements.delivery_at.value,
            origin: manualForm.elements.origin.value.trim(),
            destination: manualForm.elements.destination.value.trim(),
            supply_type: manualForm.elements.supply_type.value.trim(),
            note: manualForm.elements.note.value.trim(),
            ready: manualForm.elements.ready.checked,
        };
        manualSubmit.disabled = true;
        manualStatus.textContent = 'Сохраняем…';
        manualStatus.classList.remove('is-error');
        request(id ? '/stock/planning/manual/' + id : '/stock/planning/manual', {
            method: id ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(function (response) {
                replaceManual(response.supply);
                resetManualForm();
            })
            .catch(function (error) {
                manualStatus.textContent = error.message;
                manualStatus.classList.add('is-error');
            })
            .finally(function () {
                manualSubmit.disabled = false;
            });
    });

    manualCancel.addEventListener('click', resetManualForm);

    manualRows.addEventListener('click', function (event) {
        var edit = event.target.closest('[data-manual-edit]');
        var remove = event.target.closest('[data-manual-delete]');
        if (edit) editManual(Number(edit.getAttribute('data-manual-edit')));
        if (!remove) return;
        var id = Number(remove.getAttribute('data-manual-delete'));
        if (!window.confirm('Удалить эту поставку из ручного плана?')) return;
        remove.disabled = true;
        request('/stock/planning/manual/' + id, { method: 'DELETE' })
            .then(function () {
                manualSupplies = manualSupplies.filter(function (item) {
                    return item.id !== id;
                });
                renderManual();
                if (Number(manualForm.elements.supply_id.value || 0) === id) resetManualForm();
            })
            .catch(function (error) {
                window.alert(error.message);
                remove.disabled = false;
            });
    });

    manualRows.addEventListener('change', function (event) {
        var toggle = event.target.closest('[data-manual-ready]');
        if (!toggle) return;
        var id = Number(toggle.getAttribute('data-manual-ready'));
        toggle.disabled = true;
        request('/stock/planning/manual/' + id + '/ready', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ready: toggle.checked }),
        })
            .then(function (response) {
                replaceManual(response.supply);
            })
            .catch(function (error) {
                toggle.checked = !toggle.checked;
                window.alert(error.message);
            })
            .finally(function () {
                toggle.disabled = !editable;
            });
    });

    if (!editable) {
        manualForm.hidden = true;
        manualReadonly.hidden = false;
    }

    loadWb();
    window.setInterval(function () {
        renderManual();
    }, 60000);
})();
