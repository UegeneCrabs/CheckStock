(function () {
    'use strict';
    var panel = document.getElementById('ym-cabinet-panel');
    if (!panel) return;
    var form = panel.querySelector('[data-ym-goals-form]');
    var config = JSON.parse(document.getElementById('yandex-buyout-config').textContent);
    var request = window.YandexEconomicsFields.request;
    var status = form.querySelector('[data-ym-goals-status]');
    var save = form.querySelector('[type="submit"]');
    var reload = form.querySelector('[data-ym-goals-reload]');
    var data = null, store = '', busy = false, dirty = false;

    function message(text, error) {
        status.textContent = text;
        status.classList.toggle('is-error', !!error);
    }
    function controls() {
        save.disabled = busy || !data || !dirty || !config.canEdit;
        reload.disabled = busy;
        form.querySelectorAll('input').forEach(function (input) { input.disabled = busy || !config.canEdit; });
    }
    function input(name, title, value, maximum) {
        var label = document.createElement('label');
        label.className = 'ue1cs-field';
        var text = document.createElement('span');
        text.textContent = title;
        var wrap = document.createElement('span');
        wrap.className = 'ue1cs-input-wrap';
        var control = document.createElement('input');
        control.type = 'number'; control.name = name; control.min = '0';
        control.max = String(maximum); control.step = 'any'; control.required = true; control.value = value;
        var unit = document.createElement('b'); unit.textContent = '%';
        wrap.append(control, unit); label.append(text, wrap);
        return label;
    }
    function draw() {
        form.querySelector('[data-ym-goals-general]').replaceChildren(
            input('target_drr_percent', 'Цель по ДРР', data.values.target_drr_percent, 100),
            input('target_roi_percent', 'Целевой ROI для остальных кодов', data.values.target_roi_percent, 1000000)
        );
        var codes = form.querySelector('[data-ym-goals-codes]');
        codes.replaceChildren();
        Object.keys(data.values.target_roi_by_code).forEach(function (code) {
            codes.append(input('roi_' + code, 'Код ' + code, data.values.target_roi_by_code[code], 1000000));
        });
        controls();
    }
    function path() { return 'reports/target-price/settings/' + encodeURIComponent(store); }
    async function load(slug) {
        panel.querySelector('[data-ym-cabinet-status]').hidden = true;
        store = slug; data = null; busy = true; dirty = false;
        form.querySelector('[data-ym-goals-general]').replaceChildren();
        form.querySelector('[data-ym-goals-codes]').replaceChildren();
        controls(); message('Загружаем цели…');
        try {
            data = await request(path()); draw(); message('');
        } catch (error) { message(error.message, true); }
        finally { busy = false; controls(); }
    }
    form.addEventListener('input', function () { dirty = true; message('Есть несохранённые изменения.'); controls(); });
    reload.addEventListener('click', function () { if (!busy) load(store); });
    form.addEventListener('submit', async function (event) {
        event.preventDefault();
        if (busy || !data || !dirty || !config.canEdit || !form.reportValidity()) return;
        var payload = { revision: data.revision, target_roi_by_code: {} };
        form.querySelectorAll('input').forEach(function (control) {
            if (control.name.startsWith('roi_')) payload.target_roi_by_code[control.name.slice(4)] = Number(control.value);
            else payload[control.name] = Number(control.value);
        });
        busy = true; controls(); message('Сохраняем цели…');
        try {
            await request(path(), 'PUT', payload);
            data.revision++; dirty = false;
            panel.querySelector('[data-ym-cabinet-status]').hidden = true;
            message('Цели сохранены. Они применятся при следующем формировании отчёта ЯМ.');
        } catch (error) { message(error.message, true); }
        finally { busy = false; controls(); }
    });
    panel.addEventListener('ym-cabinet-change', function (event) {
        if (!busy && !dirty) return;
        event.preventDefault();
        var note = panel.querySelector('[data-ym-cabinet-status]');
        note.textContent = busy ? 'Дождитесь загрузки или сохранения целей.' : 'Сохраните или отмените изменения целей перед сменой кабинета.';
        note.hidden = false;
    });
    panel.addEventListener('ym-cabinet-selected', function (event) { load(event.detail.store); });
    if (panel.dataset.selectedStore) load(panel.dataset.selectedStore);
})();
