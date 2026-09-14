(function () {
    'use strict';
    var root = document.getElementById('yandex-buyout-settings');
    var node = document.getElementById('yandex-buyout-config');
    if (!root || !node) return;
    var config = JSON.parse(node.textContent);
    var grid = root.querySelector('[data-yandex-buyout-grid]');
    var status = root.querySelector('[data-yandex-buyout-status]');
    (config.items || []).forEach(function (item) {
        var form = document.createElement('form');
        form.className = 'ue1cs-card';
        form.style.setProperty('--store-color', '#ffd633');
        var head = document.createElement('div');
        head.className = 'ue1cs-card-head';
        var heading = document.createElement('h3');
        heading.textContent = item.store_name;
        head.appendChild(heading);
        form.appendChild(head);
        var body = document.createElement('div');
        body.className = 'ue1cs-card-body';
        var group = document.createElement('div');
        group.className = 'ue1cs-field-group ue1cs-fields';
        body.appendChild(group);
        form.appendChild(body);
        [
            ['buyout_period_days', 'Период расчёта, дн.', 1, 29, 1],
            ['default_buyout_percent', 'Выкуп по умолчанию, %', 0.01, 100, 0.01],
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
            input.value = item[field[0]] == null ? '' : item[field[0]];
            input.disabled = !config.canEdit;
            wrap.appendChild(input);
            label.appendChild(wrap);
            group.appendChild(label);
        });
        var foot = document.createElement('div');
        foot.className = 'ue1cs-card-foot';
        var button = document.createElement('button');
        button.type = 'submit';
        button.textContent = 'Сохранить';
        button.disabled = !config.canEdit;
        foot.appendChild(button);
        form.appendChild(foot);
        form.addEventListener('submit', async function (event) {
            event.preventDefault();
            if (!form.reportValidity()) return;
            button.disabled = true;
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
                status.textContent = error.message;
            } finally {
                button.disabled = !config.canEdit;
            }
        });
        grid.appendChild(form);
    });
})();
