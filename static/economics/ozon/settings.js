(() => {
    'use strict';
    const panel = document.getElementById('ozon-cabinet-panel');
    if (!panel) return;
    const endpoint = '/api/unit-economics-1c/ozon/cabinets';
    const forms = Array.from(panel.querySelectorAll('[data-ozon-section]'));
    const dirty = new Set();
    const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 });
    const definitions = {
        goals: [
            ['target_drr_percent', 'Цель по ДРР', 0, 100, true],
            ['target_roi_percent', 'Целевой ROI для остальных кодов', 0, 1000000, true],
        ],
        buyout: [
            ['period_days', 'Период расчёта', 1, 90, true, 'дн.'],
            ['default_percent', 'Выкуп по умолчанию', 0.01, 100, false],
        ],
        expenses: [
            ['acquiring_percent', 'Эквайринг', 0, 100, false],
            ['team_commission_percent', 'Комиссия команды · из Google Sheets', 0, 100, false],
            ['vat_percent', 'Налог НДС', 0, 100, false],
            ['usn_percent', 'Налог УСН', 0, 100, false],
        ],
    };
    let items = [], selected = null, busy = true;

    function message(node, text, error = false) {
        node.textContent = text;
        node.classList.toggle('is-error', error);
    }
    function controls() {
        forms.forEach(form => {
            form.querySelector('[type="submit"]').disabled = busy || !dirty.has(form.dataset.ozonSection);
            form.querySelector('[data-ozon-reset]').disabled = busy || !dirty.has(form.dataset.ozonSection);
            form.querySelectorAll('input').forEach(input => { input.disabled = busy; });
        });
        panel.querySelectorAll('[data-ozon-cabinet-nav] button').forEach(button => { button.disabled = busy; });
    }
    function field(definition, value) {
        const [name, title, min, max, required, suffix = '%'] = definition;
        const label = document.createElement('label'); label.className = 'ue1cs-field';
        const text = document.createElement('span'); text.textContent = title;
        const wrap = document.createElement('span'); wrap.className = 'ue1cs-input-wrap';
        const input = document.createElement('input');
        input.type = 'number'; input.name = name; input.min = min; input.max = max;
        input.step = name === 'period_days' ? '1' : 'any'; input.required = required;
        input.value = value == null ? '' : value; input.placeholder = 'Не задан';
        input.readOnly = name === 'team_commission_percent';
        const unit = document.createElement('b'); unit.textContent = suffix;
        wrap.append(input, unit); label.append(text, wrap);
        return label;
    }
    function draw(form) {
        const key = form.dataset.ozonSection;
        const values = selected.values[key];
        form.querySelector('[data-ozon-fields]').replaceChildren(...definitions[key].map(definition => field(
            definition, definition[0] === 'team_commission_percent' ? selected.team_commission.percent : values[definition[0]],
        )));
        if (key === 'goals') {
            form.querySelector('[data-ozon-roi]').replaceChildren(...Object.entries(values.target_roi_by_code).map(([code, value]) =>
                field(['roi_' + code, 'Код ' + code, 0, 1000000, false], value)));
        }
        if (key === 'expenses') {
            const team = selected.team_commission;
            const note = form.querySelector('[data-ozon-team-note]');
            note.textContent = team.values.length > 1
                ? 'В Google Sheets разные ставки: ' + team.values.map(value => number.format(value) + '%').join(', ') + '. Для каждого товара используется его ставка.'
                : team.percent == null ? 'Комиссия команды ещё не загружена из Google Sheets.'
                    : 'Комиссия команды берётся из строки каждого товара в Google Sheets.';
            if (team.missing_products) note.textContent += ' Без ставки: ' + team.missing_products + ' из ' + team.products + ' товаров.';
            if (team.updated_at) note.textContent += ' Данные от ' + window.CheckStockUI.date(team.updated_at) + '.';
        }
        message(form.querySelector('[data-ozon-status]'), '');
    }
    function select(store, updateUrl = false) {
        if (busy || (selected && selected.store_slug === store)) return;
        const status = panel.querySelector('[data-ozon-cabinet-status]');
        if (dirty.size) {
            message(status, 'Сохраните или отмените изменения перед сменой кабинета.', true);
            return;
        }
        selected = items.find(item => item.store_slug === store) || items[0];
        if (!selected) { message(status, 'Нет доступных кабинетов Ozon.'); return; }
        panel.querySelector('[data-ozon-name]').textContent = selected.store_name;
        panel.querySelector('[data-ozon-avatar]').textContent = selected.store_name.slice(0, 2);
        panel.querySelector('[data-ozon-cabinet-card]').hidden = false;
        panel.querySelectorAll('[data-ozon-cabinet-nav] button').forEach(button => {
            button.setAttribute('aria-pressed', String(button.dataset.store === selected.store_slug));
        });
        forms.forEach(draw); controls(); message(status, '');
        if (updateUrl) {
            const url = new URL(location.href); url.searchParams.set('ozon_store', selected.store_slug);
            history.replaceState(null, '', url);
        }
    }
    forms.forEach(form => {
        const key = form.dataset.ozonSection;
        form.addEventListener('input', () => {
            dirty.add(key); controls(); message(form.querySelector('[data-ozon-status]'), 'Есть несохранённые изменения.');
        });
        form.querySelector('[data-ozon-reset]').addEventListener('click', () => {
            if (busy) return;
            dirty.delete(key); draw(form); controls(); message(panel.querySelector('[data-ozon-cabinet-status]'), '');
        });
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (busy || !selected || !dirty.has(key) || !form.reportValidity()) return;
            const values = key === 'goals' ? { target_roi_by_code: {} } : {};
            form.querySelectorAll('input').forEach(input => {
                if (input.readOnly) return;
                const value = input.value === '' ? null : Number(input.value);
                if (input.name.startsWith('roi_')) values.target_roi_by_code[input.name.slice(4)] = value;
                else values[input.name] = value;
            });
            const status = form.querySelector('[data-ozon-status]');
            busy = true; controls(); message(status, 'Сохраняем…');
            try {
                const response = await fetch(endpoint + '/' + encodeURIComponent(selected.store_slug), {
                    method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({ revision: selected.revision, [key]: values }),
                });
                if (response.status === 409) throw new Error('Настройки изменились в другой вкладке. Скопируйте свои изменения и обновите страницу.');
                if (!response.ok || response.redirected) throw new Error('Не удалось сохранить настройки. Проверьте значения и повторите.');
                const result = await response.json();
                Object.assign(selected, result); dirty.delete(key); draw(form);
                try {
                    localStorage.setItem('checkstock.ozon.cabinet-updated', JSON.stringify({
                        store: selected.store_slug, revision: result.revision, updated_at: result.updated_at,
                    }));
                } catch {}
                message(status, key === 'buyout' ? 'Сохранено. Период применится при следующей выгрузке выкупа.' : 'Сохранено.');
            } catch (error) { message(status, error.message, true); }
            finally { busy = false; controls(); }
        });
    });
    window.addEventListener('beforeunload', event => {
        if (dirty.size) { event.preventDefault(); event.returnValue = ''; }
    });
    (async () => {
        try {
            const data = await window.CheckStockUI.request(endpoint);
            items = data.items;
            panel.querySelector('[data-ozon-cabinet-count]').textContent = items.length;
            panel.querySelector('[data-ozon-cabinet-nav]').replaceChildren(...items.map(item => {
                const button = document.createElement('button'); button.type = 'button';
                button.textContent = item.store_name; button.dataset.store = item.store_slug;
                button.setAttribute('aria-pressed', 'false'); button.onclick = () => select(item.store_slug, true);
                return button;
            }));
            busy = false; select(new URL(location.href).searchParams.get('ozon_store'));
        } catch (error) { message(panel.querySelector('[data-ozon-cabinet-status]'), error.message, true); }
    })();
})();
