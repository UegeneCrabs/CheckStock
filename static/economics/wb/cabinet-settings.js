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
    var toastTimer = 0;
    var roiDefaults = { A: 20, B: 30, C: 50, D: 0, F: 50, NEW: 50, U: 20 };
    var fields = [
        {
            key: 'buyout_period_days',
            label: 'Период расчёта',
            step: '1',
            min: '1',
            max: '29',
            suffix: 'дн.',
            group: 'buyout',
            integer: true,
            warning: 'Введите целое число от 1 до 29.',
        },
        {
            key: 'default_buyout_percent',
            label: 'Выкуп по умолчанию',
            step: '0.01',
            min: '0.01',
            max: '100',
            suffix: '%',
            group: 'buyout',
            optional: true,
            warning:
                'Введите процент от 0,01 до 100. Он используется при нулевом выкупе WB или отсутствии данных.',
        },
        {
            key: 'target_drr_percent',
            label: 'Цель по ДРР',
            step: '0.01',
            min: '0',
            max: '100',
            suffix: '%',
            group: 'goals',
            warning: 'Введите цель по ДРР от 0 до 100%.',
        },
        { key: 'acceptance_coefficient', label: 'Коэффициент приёмки', step: '0.01', group: 'logistics' },
        {
            key: 'wb_extra_tariff_percent',
            label: 'Дополнительные тарифы',
            step: '0.01',
            suffix: '%',
            group: 'logistics',
        },
        {
            key: 'acquiring_percent',
            label: 'Эквайринг',
            step: '0.01',
            max: '100',
            suffix: '%',
            group: 'expenses',
        },
        {
            key: 'team_commission_percent',
            label: 'Комиссия команды · из Google Sheets',
            step: '0.01',
            max: '100',
            suffix: '%',
            group: 'expenses',
            readOnly: true,
        },
        { key: 'vat_percent', label: 'Налог НДС', step: '0.01', max: '100', suffix: '%', group: 'expenses' },
        {
            key: 'tax_system',
            label: 'Система налогообложения',
            type: 'select',
            group: 'expenses',
            gogolOnly: true,
            options: [
                { value: 'usn', label: 'УСН' },
                { value: 'osno', label: 'ОСНО' },
            ],
        },
        {
            key: 'usn_percent',
            label: 'Налог УСН',
            step: '0.01',
            max: '100',
            suffix: '%',
            group: 'expenses',
            taxSystems: ['usn'],
        },
        {
            key: 'osno_percent',
            label: 'Налог ОСНО',
            step: '0.01',
            max: '100',
            suffix: '%',
            group: 'expenses',
            gogolOnly: true,
            taxSystems: ['osno'],
        },
    ].concat(
        Object.keys(roiDefaults).map(function (code) {
            return {
                key: 'target_roi_by_code_' + code,
                roiCode: code,
                label: 'Код ' + code,
                step: '0.01',
                min: '0',
                max: '1000000',
                suffix: '%',
                group: 'roi',
                warning: 'Введите цель по ROI от 0 до 1 000 000%.',
            };
        }),
    );
    var fieldGroups = [
        { key: 'buyout', title: 'Процент выкупа', hint: 'Период без сегодняшнего дня · обновление каждые 4 часа' },
        { key: 'logistics', title: 'Логистика', hint: 'Приёмка и дополнительные тарифы WB' },
        { key: 'goals', title: 'Целевая цена', hint: 'ДРР с учётом выкупа и целевой ROI по коду товара' },
        { key: 'expenses', title: 'Расходы и налоги', hint: 'Ставки для расчёта юнит-экономики' },
    ];

    function showToast(message, error) {
        window.clearTimeout(toastTimer);
        toast.textContent = message;
        toast.classList.toggle('is-error', error === true);
        toast.classList.add('is-visible');
        toastTimer = window.setTimeout(function () {
            toast.classList.remove('is-visible');
        }, 3200);
    }
    function formatUpdated(item) {
        if (!item.updated_at) return 'Значения по умолчанию';
        var moment = new Date(item.updated_at);
        var date = Number.isNaN(moment.getTime()) ? item.updated_at : moment.toLocaleString('ru-RU');
        return 'Сохранено ' + date + (item.updated_by_name ? ' · ' + item.updated_by_name : '');
    }
    function fieldValue(field, item) {
        if (!field.roiCode) return item[field.key];
        var goals = item.target_roi_by_code || {};
        return goals[field.roiCode] === undefined ? roiDefaults[field.roiCode] : goals[field.roiCode];
    }
    function fieldHtml(field, item) {
        var taxSystemHidden =
            item.store_slug === 'gogol' &&
            field.taxSystems &&
            field.taxSystems.indexOf(item.tax_system) === -1;
        var fieldDisabled = !canEdit || field.readOnly || taxSystemHidden;
        var control =
            field.type === 'select'
                ? window.CheckStockUI.render('economics/wb/cabinet-settings/control-2', {
                      key: field.key,
                      content: fieldDisabled ? ' disabled' : '',
                      content_2: field.options
                          .map(function (option) {
                              return window.CheckStockUI.render('economics/wb/cabinet-settings/control', {
                                  value: option.value,
                                  content: option.value === item[field.key] ? ' selected' : '',
                                  label: option.label,
                              });
                          })
                          .join(''),
                  })
                : window.CheckStockUI.render('economics/wb/cabinet-settings/control-3', {
                      content: field.min || '0',
                      content_2: field.max ? ' max="' + field.max + '"' : '',
                      content_3: field.key === 'default_buyout_percent' ? ' placeholder="Не задан"' : '',
                      step: field.step,
                      key: field.key,
                      content_4: fieldValue(field, item),
                      content_5: fieldDisabled ? ' disabled' : '',
                  });
        return window.CheckStockUI.render('economics/wb/cabinet-settings/field-html-3', {
            content: field.taxSystems ? ' data-tax-systems="' + field.taxSystems.join(',') + '"' : '',
            content_2: taxSystemHidden ? ' hidden' : '',
            label: field.label,
            control: control,
            content_3: field.suffix
                ? window.CheckStockUI.render('common/ui/field-unit', {
                      suffix: field.suffix,
                  })
                : '',
            content_4: field.warning
                ? window.CheckStockUI.render('economics/wb/cabinet-settings/field-html-2', {
                      warning: field.warning,
                  })
                : '',
        });
    }
    function fieldGroupHtml(group, item) {
        var groupFields = fields.filter(function (field) {
            return field.group === group.key && (!field.gogolOnly || item.store_slug === 'gogol');
        });
        return window.CheckStockUI.render('economics/wb/cabinet-settings/field-group-html-2', {
            title: group.title,
            hint: group.hint,
            content: groupFields
                .map(function (field) {
                    return fieldHtml(field, item);
                })
                .join(''),
            content_2:
                group.key === 'goals'
                    ? window.CheckStockUI.render('economics/wb/cabinet-settings/field-group-html', {
                          content: fields
                              .filter(function (field) {
                                  return field.roiCode;
                              })
                              .map(function (field) {
                                  return fieldHtml(field, item);
                              })
                              .join(''),
                      })
                    : '',
        });
    }
    function cardHtml(item) {
        return window.CheckStockUI.render('economics/wb/cabinet-settings/card-html', {
            store_slug: item.store_slug,
            store_color: item.store_color,
            store_text: item.store_text,
            store_initials: item.store_initials,
            store_name: item.store_name,
            item: formatUpdated(item),
            content: fieldGroups
                .map(function (group) {
                    return fieldGroupHtml(group, item);
                })
                .join(''),
            content_2: canEdit ? '' : ' disabled',
        });
    }
    function payloadFromCard(card) {
        var payload = { target_roi_by_code: {} };
        fields
            .filter(function (field) {
                return !field.readOnly;
            })
            .forEach(function (field) {
                var input = card.querySelector('[data-setting="' + field.key + '"]');
                if (!input) return;
                if (field.roiCode) {
                    payload.target_roi_by_code[field.roiCode] = Number(input.value);
                    return;
                }
                if (field.optional && input.value.trim() === '') {
                    payload[field.key] = null;
                    return;
                }
                payload[field.key] =
                    field.type === 'select'
                        ? input.value
                        : field.integer
                          ? Number.parseInt(input.value, 10)
                          : Number(input.value);
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
        var field = fields.find(function (item) {
            return item.key === input.dataset.setting;
        });
        if (!field || !field.warning) return true;
        var value = Number(input.value);
        var minimum = Number(field.min);
        var maximum = Number(field.max);
        var valid =
            input.value.trim() !== '' &&
            Number.isFinite(value) &&
            (!field.integer || Number.isInteger(value)) &&
            value >= minimum &&
            value <= maximum;
        if (field.optional && input.value.trim() === '' && !input.validity.badInput) valid = true;
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
            if (
                input &&
                (field.roiCode
                    ? settings.target_roi_by_code !== undefined
                    : settings[field.key] !== undefined)
            ) {
                input.value = fieldValue(field, settings);
                validateField(input);
            }
        });
        syncTaxSystemFields(card, false);
    }
    function responseError(result, status) {
        var detail = result && result.detail;
        if (Array.isArray(detail)) {
            detail = detail
                .map(function (item) {
                    return item.msg || String(item);
                })
                .join('; ');
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
            showToast('Проверьте значения в отмеченных полях', true);
            return;
        }
        var store = card.dataset.store;
        var payload = payloadFromCard(card);
        if (
            Object.keys(payload).some(function (key) {
                return (
                    typeof payload[key] === 'number' && (!Number.isFinite(payload[key]) || payload[key] < 0)
                );
            })
        ) {
            showToast('Проверьте введённые значения', true);
            return;
        }
        setBusy(card, true);
        try {
            var response = await window.fetch(
                '/api/unit-economics-1c/cabinet-settings/' + encodeURIComponent(store),
                {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        Accept: 'application/json',
                        'X-Requested-With': 'fetch',
                    },
                    body: JSON.stringify(payload),
                },
            );
            var responseBody = await response.text();
            var result = {};
            try {
                result = responseBody ? JSON.parse(responseBody) : {};
            } catch (ignored) {
                /* no-op */
            }
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

    grid.innerHTML = items.map(cardHtml).join('');
    if (cabinetCount) cabinetCount.textContent = String(items.length);
    empty.hidden = items.length !== 0;
    var cabinetNav = root.querySelector('[data-wb-cabinet-nav]');
    function selectCabinet(slug) {
        grid.querySelectorAll('[data-store]').forEach(function (card) {
            card.hidden = card.dataset.store !== slug;
        });
        cabinetNav.querySelectorAll('button').forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.cabinet === slug));
        });
    }
    items.forEach(function (item) {
        var button = document.createElement('button');
        button.type = 'button';
        button.textContent = item.store_name;
        button.dataset.cabinet = item.store_slug;
        button.setAttribute('aria-controls', 'ue1cs-grid');
        button.addEventListener('click', function () {
            selectCabinet(item.store_slug);
        });
        cabinetNav.appendChild(button);
    });
    if (items.length) selectCabinet(items[0].store_slug);
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
