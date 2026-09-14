(function () {
    'use strict';
    var root = document.getElementById('yandex-buyout-settings');
    var node = document.getElementById('yandex-buyout-config');
    if (!root || !node) return;
    var config = JSON.parse(node.textContent);
    var grid = root.querySelector('[data-yandex-buyout-grid]');
    var panel = document.getElementById('ym-cabinet-panel');
    var nav = panel.querySelector('[data-ym-cabinet-nav]');
    var items = config.items || [];
    panel.querySelector('[data-ym-cabinet-count]').textContent = String(items.length);
    function selectCabinet(slug, notify) {
        if (notify && !panel.dispatchEvent(new CustomEvent('ym-cabinet-change', {
            detail: { store: slug }, cancelable: true,
        }))) return;
        grid.querySelectorAll('[data-store]').forEach(function (form) {
            form.hidden = form.dataset.store !== slug;
        });
        nav.querySelectorAll('button').forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.cabinet === slug));
        });
    }
    (config.items || []).forEach(function (item) {
        var cabinetButton = document.createElement('button');
        cabinetButton.type = 'button';
        cabinetButton.textContent = item.store_name;
        cabinetButton.dataset.cabinet = item.store_slug;
        cabinetButton.setAttribute('aria-controls', 'yandex-buyout-settings yandex-economics-settings');
        cabinetButton.addEventListener('click', function () { selectCabinet(item.store_slug, true); });
        nav.appendChild(cabinetButton);
        var form = document.createElement('form');
        form.className = 'ue1cs-card ym-buyout-card';
        form.dataset.store = item.store_slug;
        form.style.setProperty('--store-color', '#ffd633');
        var head = document.createElement('div');
        head.className = 'ue1cs-card-head';
        var heading = document.createElement('h3');
        heading.textContent = 'Процент выкупа';
        head.appendChild(heading);
        var storeLabel = document.createElement('small');
        storeLabel.textContent = item.store_name;
        head.appendChild(storeLabel);
        form.appendChild(head);
        var body = document.createElement('div');
        body.className = 'ue1cs-card-body';
        var group = document.createElement('div');
        group.className = 'ue1cs-field-group ue1cs-fields';
        body.appendChild(group);
        form.appendChild(body);
        [
            ['buyout_period_days', 'Период расчёта', 1, 29, 1, 'дн.'],
            ['default_buyout_percent', 'Выкуп по умолчанию', 0.01, 100, 0.01, '%'],
        ].forEach(function (field) {
            var label = document.createElement('label');
            label.className = 'ue1cs-field';
            var title = document.createElement('span');
            title.textContent = field[1];
            label.appendChild(title);
            var wrap = document.createElement('span');
            wrap.className = 'ue1cs-input-wrap';
            var input = document.createElement('input');
            input.type = 'number';
            input.name = field[0];
            input.min = field[2];
            input.max = field[3];
            input.step = field[4];
            input.required = field[0] === 'buyout_period_days';
            if (!input.required) input.placeholder = 'Не задан';
            input.value = item[field[0]] == null ? '' : item[field[0]];
            input.disabled = !config.canEdit;
            wrap.appendChild(input);
            var unit = document.createElement('b');
            unit.textContent = field[5];
            wrap.appendChild(unit);
            label.appendChild(wrap);
            group.appendChild(label);
        });
        var foot = document.createElement('div');
        foot.className = 'ue1cs-card-foot';
        var status = document.createElement('span');
        status.className = 'ym-settings-status';
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        foot.appendChild(status);
        var button = document.createElement('button');
        button.type = 'submit';
        button.textContent = 'Сохранить выкуп';
        button.disabled = !config.canEdit;
        foot.appendChild(button);
        form.appendChild(foot);
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            if (!form.reportValidity()) return;
            button.disabled = true;
            status.classList.remove('is-error');
            status.textContent = 'Сохраняем…';
            try {
                var raw = form.elements.default_buyout_percent.value;
                var response = await fetch(
                    '/api/unit-economics-1c/yandex-market/buyout-settings/' +
                        encodeURIComponent(item.store_slug),
                    {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            buyout_period_days: Number(form.elements.buyout_period_days.value),
                            default_buyout_percent: raw === '' ? null : Number(raw),
                        }),
                    },
                );
                var result = await response.json();
                if (!response.ok || !result.ok)
                    throw new Error(result.error || 'Не удалось сохранить параметры');
                status.textContent = item.store_name + ': параметры сохранены';
            } catch (error) {
                status.classList.add('is-error');
                status.textContent = error.message;
            } finally {
                button.disabled = !config.canEdit;
            }
        });
        grid.appendChild(form);
    });
    var selected = new URLSearchParams(window.location.search).get('ym_store');
    if (items.length) {
        selectCabinet(items.some(function (item) { return item.store_slug === selected; }) ? selected : items[0].store_slug, false);
    } else {
        var empty = document.createElement('div');
        empty.className = 'ue1cs-empty';
        empty.textContent = 'Для вашей учётной записи нет доступных кабинетов YM.';
        grid.appendChild(empty);
    }
})();
