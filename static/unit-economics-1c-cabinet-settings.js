(function () {
    'use strict';

    var root = document.getElementById('ue1cs-page');
    var configNode = document.getElementById('ue1cs-config');
    if (!root || !configNode) return;

    var config = JSON.parse(configNode.textContent || '{}');
    var items = Array.isArray(config.items) ? config.items : [];
    var canEdit = config.canEdit === true;
    var grid = document.getElementById('ue1cs-grid');
    var empty = document.getElementById('ue1cs-empty');
    var toast = document.getElementById('ue1cs-toast');
    var cabinetCount = document.getElementById('ue1cs-cabinet-count');
    var sourceSync = document.getElementById('ue1cs-source-sync');
    var priceSync = document.getElementById('ue1cs-price-sync');
    var toastTimer = 0;
    var fields = [
        { key: 'buyout_period_days', label: 'Период расчёта', step: '1', min: '1', max: '29', suffix: 'дн.', group: 'buyout', integer: true,
            warning: 'Введите целое число от 1 до 29.' },
        { key: 'acceptance_coefficient', label: 'КФ приёмки', step: '0.01', group: 'logistics' },
        { key: 'wb_extra_tariff_percent', label: 'Доп. тарифы WB', step: '0.01', suffix: '%', group: 'logistics' },
        { key: 'acquiring_percent', label: 'Процент эквайринга', step: '0.01', max: '100', suffix: '%', group: 'expenses' },
        { key: 'team_commission_percent', label: 'Комиссия команды · Google Sheets', step: '0.01', max: '100', suffix: '%', group: 'expenses', readOnly: true },
        { key: 'vat_percent', label: 'Налог НДС', step: '0.01', max: '100', suffix: '%', group: 'expenses' },
        { key: 'tax_system', label: 'Система налогообложения', type: 'select', group: 'expenses', gogolOnly: true,
            options: [{ value: 'usn', label: 'УСН' }, { value: 'osno', label: 'ОСНО' }] },
        { key: 'usn_percent', label: 'Налог УСН', step: '0.01', max: '100', suffix: '%', group: 'expenses', taxSystems: ['usn'] },
        { key: 'osno_percent', label: 'Налог ОСНО', step: '0.01', max: '100', suffix: '%', group: 'expenses', gogolOnly: true, taxSystems: ['osno'] }
    ];
    var fieldGroups = [
        { key: 'buyout', title: 'Процент выкупа WB', hint: 'Завершённые дни, сегодня не включается' },
        { key: 'logistics', title: 'Логистика WB', hint: 'Приёмка и тарифы' },
        { key: 'expenses', title: 'Расходы', hint: 'Доли в процентах' }
    ];

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function showToast(message, error) {
        window.clearTimeout(toastTimer);
        toast.textContent = message;
        toast.classList.toggle('is-error', error === true);
        toast.classList.add('is-visible');
        toastTimer = window.setTimeout(function () { toast.classList.remove('is-visible'); }, 3200);
    }
    function formatUpdated(item) {
        if (!item.updated_at) return 'Значения по умолчанию';
        var moment = new Date(item.updated_at);
        var date = Number.isNaN(moment.getTime()) ? item.updated_at : moment.toLocaleString('ru-RU');
        return 'Сохранено ' + date + (item.updated_by_name ? ' · ' + item.updated_by_name : '');
    }
    function fieldHtml(field, item) {
        var taxSystemHidden = item.store_slug === 'gogol' && field.taxSystems
            && field.taxSystems.indexOf(item.tax_system) === -1;
        var fieldDisabled = !canEdit || field.readOnly || taxSystemHidden;
        var control = field.type === 'select'
            ? '<select data-setting="' + field.key + '"' + (fieldDisabled ? ' disabled' : '') + '>'
                + field.options.map(function (option) {
                    return '<option value="' + escapeHtml(option.value) + '"'
                        + (option.value === item[field.key] ? ' selected' : '') + '>'
                        + escapeHtml(option.label) + '</option>';
                }).join('') + '</select>'
            : '<input type="number" min="' + (field.min || '0') + '"' + (field.max ? ' max="' + field.max + '"' : '')
                + ' step="' + field.step + '" data-setting="' + field.key
                + '" value="' + escapeHtml(item[field.key]) + '"' + (fieldDisabled ? ' disabled' : '') + '>';
        return '<label class="ue1cs-field"' + (field.taxSystems ? ' data-tax-systems="' + field.taxSystems.join(',') + '"' : '')
            + (taxSystemHidden ? ' hidden' : '') + '><span>' + escapeHtml(field.label) + '</span><span class="ue1cs-input-wrap">'
            + control
            + (field.suffix ? '<b>' + field.suffix + '</b>' : '') + '</span>'
            + (field.warning ? '<small class="ue1cs-field-warning" data-field-warning hidden>'
                + escapeHtml(field.warning) + '</small>' : '') + '</label>';
    }
    function fieldGroupHtml(group, item) {
        var groupFields = fields.filter(function (field) {
            return field.group === group.key && (!field.gogolOnly || item.store_slug === 'gogol');
        });
        return '<section class="ue1cs-field-group"><div class="ue1cs-field-group-head"><strong>'
            + escapeHtml(group.title) + '</strong><span>' + escapeHtml(group.hint) + '</span></div>'
            + '<div class="ue1cs-fields">' + groupFields.map(function (field) { return fieldHtml(field, item); }).join('')
            + '</div></section>';
    }
    function cardHtml(item) {
        return '<article class="ue1cs-card" data-store="' + escapeHtml(item.store_slug)
            + '" style="--store-color:' + escapeHtml(item.store_color) + ';--store-text:' + escapeHtml(item.store_text) + '">'
            + '<div class="ue1cs-card-head"><span class="ue1cs-avatar">'
            + escapeHtml(item.store_initials) + '</span><div><h3>' + escapeHtml(item.store_name)
            + '</h3></div><span class="ue1cs-state" data-state>'
            + escapeHtml(formatUpdated(item)) + '</span></div>'
            + '<div class="ue1cs-card-body">' + fieldGroups.map(function (group) { return fieldGroupHtml(group, item); }).join('')
            + '</div><div class="ue1cs-card-foot"><button type="button" data-save'
            + (canEdit ? '' : ' disabled') + '>Сохранить</button></div></article>';
    }
    function payloadFromCard(card) {
        var payload = {};
        fields.filter(function (field) { return !field.readOnly; }).forEach(function (field) {
            var input = card.querySelector('[data-setting="' + field.key + '"]');
            if (!input) return;
            payload[field.key] = field.type === 'select'
                ? input.value
                : field.integer ? Number.parseInt(input.value, 10) : Number(input.value);
        });
        if (payload.tax_system === 'osno') {
            payload.usn_percent = 0;
        } else {
            payload.tax_system = 'usn';
            payload.osno_percent = 0;
        }
        return payload;
    }
    function validateField(input) {
        var field = fields.find(function (item) { return item.key === input.dataset.setting; });
        if (!field || !field.warning) return true;
        var value = Number(input.value);
        var minimum = Number(field.min);
        var maximum = Number(field.max);
        var valid = input.value.trim() !== '' && Number.isFinite(value)
            && (!field.integer || Number.isInteger(value))
            && value >= minimum && value <= maximum;
        var warning = input.closest('.ue1cs-field').querySelector('[data-field-warning]');
        input.setAttribute('aria-invalid', valid ? 'false' : 'true');
        input.setCustomValidity(valid ? '' : field.warning);
        if (warning) warning.hidden = valid;
        return valid;
    }
    function validateCard(card) {
        return fields.every(function (field) {
            var input = card.querySelector('[data-setting="' + field.key + '"]');
            return !input || validateField(input);
        });
    }
    function syncTaxSystemFields(card, resetInactive) {
        var input = card.querySelector('[data-setting="tax_system"]');
        if (!input) return;
        Array.prototype.forEach.call(card.querySelectorAll('[data-tax-systems]'), function (field) {
            var active = field.dataset.taxSystems.split(',').indexOf(input.value) !== -1;
            var control = field.querySelector('[data-setting]');
            field.hidden = !active;
            if (!control) return;
            control.disabled = !active || !canEdit;
            if (!active && resetInactive) control.value = '0';
        });
    }
    function applySavedSettings(card, settings) {
        fields.forEach(function (field) {
            var input = card.querySelector('[data-setting="' + field.key + '"]');
            if (input && settings[field.key] !== undefined) {
                input.value = settings[field.key];
                validateField(input);
            }
        });
        syncTaxSystemFields(card, false);
    }
    function responseError(result, status) {
        var detail = result && result.detail;
        if (Array.isArray(detail)) {
            detail = detail.map(function (item) { return item.msg || String(item); }).join('; ');
        }
        return (result && result.error) || detail || 'Не удалось сохранить параметры (HTTP ' + status + ')';
    }
    function setBusy(card, busy) {
        var button = card.querySelector('[data-save]');
        button.disabled = busy || !canEdit;
        button.textContent = busy ? 'Сохраняем…' : 'Сохранить';
        card.classList.toggle('is-saving', busy);
    }
    async function saveCard(card) {
        if (!canEdit) return;
        if (!validateCard(card)) {
            showToast('Период выкупа должен быть целым числом от 1 до 29', true);
            return;
        }
        var store = card.dataset.store;
        var payload = payloadFromCard(card);
        if (Object.keys(payload).some(function (key) {
            return typeof payload[key] === 'number' && (!Number.isFinite(payload[key]) || payload[key] < 0);
        })) {
            showToast('Проверьте введённые значения', true);
            return;
        }
        setBusy(card, true);
        try {
            var response = await window.fetch('/api/unit-economics-1c/cabinet-settings/' + encodeURIComponent(store), {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify(payload)
            });
            var responseBody = await response.text();
            var result = {};
            try { result = responseBody ? JSON.parse(responseBody) : {}; } catch (ignored) { /* no-op */ }
            if (!response.ok || !result.ok) throw new Error(responseError(result, response.status));
            applySavedSettings(card, result.settings);
            card.querySelector('[data-state]').textContent = formatUpdated(result.settings);
            showToast(result.settings.store_name + ': параметры сохранены');
        } catch (error) {
            showToast(error.message || 'Не удалось сохранить параметры', true);
        } finally {
            setBusy(card, false);
        }
    }

    function applySourceSettings(updatedItems) {
        (Array.isArray(updatedItems) ? updatedItems : []).forEach(function (item) {
            var card = grid.querySelector('[data-store="' + CSS.escape(item.store_slug) + '"]');
            if (!card) return;
            var commission = card.querySelector('[data-setting="team_commission_percent"]');
            if (commission) commission.value = item.team_commission_percent;
            var state = card.querySelector('[data-state]');
            if (state) state.textContent = formatUpdated(item);
        });
    }
    function setSourceSyncBusy(busy) {
        if (!sourceSync) return;
        sourceSync.disabled = busy || !canEdit;
        sourceSync.classList.toggle('is-loading', busy);
        var label = sourceSync.querySelector('[data-source-sync-label]');
        if (label) label.textContent = busy ? 'Загружаем…' : 'Выгрузить себес';
    }
    async function syncSourceData() {
        if (!canEdit || !sourceSync) return;
        setSourceSyncBusy(true);
        try {
            var response = await window.fetch('/api/unit-economics-1c/source-data/sync', {
                method: 'POST',
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'Не удалось загрузить себестоимость');
            applySourceSettings(result.items);
            var report = result.report || {};
            showToast('Себес обновлён: ' + Number(report.saved || 0) + ' товаров · '
                + Number(report.sheet_count || 0) + ' WB-листов');
        } catch (error) {
            showToast(error.message || 'Не удалось загрузить себестоимость', true);
        } finally {
            setSourceSyncBusy(false);
        }
    }

    function setPriceSyncBusy(busy) {
        if (!priceSync) return;
        priceSync.disabled = busy || !canEdit;
        priceSync.classList.toggle('is-loading', busy);
        var label = priceSync.querySelector('[data-price-sync-label]');
        if (label) label.textContent = busy ? 'Загружаем…' : 'Выгрузить цены';
    }
    async function syncPrices() {
        if (!canEdit || !priceSync) return;
        setPriceSyncBusy(true);
        try {
            var response = await window.fetch('/api/unit-economics-1c/prices/sync', {
                method: 'POST',
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Не удалось загрузить цены');
            var reports = Object.values(result.report || {});
            var saved = reports.reduce(function (total, report) { return total + Number(report.rows || 0); }, 0);
            var partial = reports.filter(function (report) { return !report.ok; }).length;
            showToast('Цены обновлены: ' + saved + ' товаров' + (partial ? ' · частично в ' + partial + ' кабинетах' : ''));
        } catch (error) {
            showToast(error.message || 'Не удалось загрузить цены', true);
        } finally {
            setPriceSyncBusy(false);
        }
    }

    grid.innerHTML = items.map(cardHtml).join('');
    if (cabinetCount) cabinetCount.textContent = String(items.length);
    empty.hidden = items.length !== 0;
    if (sourceSync) {
        sourceSync.disabled = !canEdit;
        if (!canEdit) sourceSync.title = 'Требуются права на изменение раздела';
        sourceSync.addEventListener('click', syncSourceData);
    }
    if (priceSync) {
        priceSync.disabled = !canEdit;
        if (!canEdit) priceSync.title = 'Требуются права на изменение раздела';
        priceSync.addEventListener('click', syncPrices);
    }
    grid.addEventListener('click', function (event) {
        var button = event.target.closest('[data-save]');
        if (!button) return;
        saveCard(button.closest('[data-store]'));
    });
    grid.addEventListener('change', function (event) {
        if (!event.target.matches('[data-setting="tax_system"]')) return;
        syncTaxSystemFields(event.target.closest('[data-store]'), true);
    });
    grid.addEventListener('input', function (event) {
        if (!event.target.matches('[data-setting]')) return;
        validateField(event.target);
    });
})();
