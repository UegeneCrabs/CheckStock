(function () {
    'use strict';
    var api = '/api/unit-economics-1c/yandex-market/reports/target-price';
    async function request(path, options) {
        var response = await fetch(path, Object.assign({ cache: 'no-store', headers: {
            'Content-Type': 'application/json', 'X-Requested-With': 'fetch', Accept: 'application/json'
        } }, options || {}));
        var data = await response.json();
        if (!response.ok || !data.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || 'Не удалось выполнить запрос');
        return data;
    }
    window.YandexTargetReport = { init: function (context) {
        var root = context.root, node = context.node, config = context.config, format = context.format;
        var row = null, sequence = 0, timer, busy = false, scenario = {};
        function el(id) { return document.getElementById('ym-' + id); }
        function message(text) { el('target-message').textContent = text || ''; }
        function buttons() {
            el('goal-save').disabled = busy || !config.canEdit;
            el('goal-reset').disabled = busy || !config.canEdit;
        }
        function values() {
            var drr = el('goal-drr'), roi = el('goal-roi');
            if (!drr.value || !roi.value || !drr.checkValidity() || !roi.checkValidity()) return null;
            return { article: row.article, target_drr_percent: Number(drr.value), target_roi_percent: Number(roi.value) };
        }
        function show(result) {
            el('target-result').replaceChildren();
            [['Цена продавца', result.target_retail_price, ' ₽'], ['Цена с СПП', result.target_spp_price, ' ₽'],
             ['Цена с Пэй', result.target_price, ' ₽'], ['Расчётный ROI', result.target_actual_roi, '%']].forEach(function (metric) {
                var item = document.createElement('div'), label = document.createElement('span'), value = document.createElement('strong');
                label.textContent = metric[0]; value.textContent = format(metric[1]) + (metric[1] == null ? '' : metric[2]);
                item.append(label, value); el('target-result').append(item);
            });
            message((result.target_warnings || []).join(' '));
            var costs = result.target_calculation && result.target_calculation.costs || {};
            var labels = { commission: 'Комиссия ЯМ', payment_acceptance: 'Приём платежа', acquiring: 'Перевод платежа',
                delivery: 'Доставка', repeat_delivery: 'Повторная доставка', returns: 'Возвраты', transit: 'Транзит',
                purchase: 'Закупка', fulfillment: 'ФФ', company_commission: 'Комиссия компании', vat: 'НДС', usn: 'УСН',
                loss: 'Потери', disposal: 'Утилизация', advertising: 'Реклама', logistics: 'Логистика' };
            el('target-costs').replaceChildren();
            Object.keys(costs).forEach(function (key) {
                var dt = document.createElement('dt'), dd = document.createElement('dd');
                dt.textContent = labels[key] || key; dd.textContent = format(costs[key]) + ' ₽';
                el('target-costs').append(dt, dd);
            });
        }
        var fields = [
            ['seller_price', 'Цена продавца, ₽'], ['buyer_price', 'Цена с СПП, ₽'], ['pay_price', 'Цена с Пэй, ₽'],
            ['purchase_price', 'Закупочная стоимость, ₽'], ['fulfillment_cost', 'Затраты на ФФ, ₽'],
            ['buyout_percent', 'Выкуп, %'], ['commission_percent', 'Комиссия ЯМ, %'],
            ['acquiring_percent', 'Перевод платежа, %'], ['payment_acceptance', 'Приём платежа, ₽'],
            ['delivery_cost', 'Доставка, ₽'], ['middle_mile', 'Средняя миля, ₽'], ['transit_cost', 'Транзит, ₽'],
            ['company_commission_percent', 'Комиссия компании, %'], ['vat_percent', 'НДС, %'], ['usn_percent', 'УСН, %'],
            ['loss_percent', 'Потери, %'], ['disposal_cost', 'Утилизация, ₽']
        ];
        function inputs() {
            el('target-inputs').replaceChildren();
            fields.forEach(function (spec) {
                var label = document.createElement('label'), span = document.createElement('span'), input = document.createElement('input');
                span.textContent = spec[1]; input.type = 'number'; input.min = '0'; input.max = spec[1].includes('%') ? '100' : '1000000000';
                input.step = 'any'; input.placeholder = '—'; input.value = row.source_values[spec[0]] == null ? '' : row.source_values[spec[0]];
                input.setAttribute('aria-label', spec[1]);
                input.addEventListener('input', function () {
                    if (!input.checkValidity() || !input.value) { message('Проверьте исходные данные.'); return; }
                    scenario[spec[0]] = Number(input.value); schedule();
                });
                label.append(span, input); el('target-inputs').append(label);
            });
        }
        function open(next) {
            clearTimeout(timer); sequence++; row = next; scenario = {};
            node('drawer-title').textContent = row.name;
            node('drawer-meta').textContent = row.store_name + ' · Арт. ' + row.article;
            node('drawer-thumb').textContent = 'ЯМ';
            el('goal-drr').value = row.target_drr; el('goal-roi').value = row.target_roi;
            root.classList.add('has-calculator'); document.body.classList.add('uetp-calculator-open');
            node('drawer').classList.add('is-open'); node('overlay').classList.add('is-open');
            node('drawer').setAttribute('aria-hidden', 'false'); show(row); inputs(); buttons();
        }
        function close() {
            clearTimeout(timer); sequence++; row = null;
            root.classList.remove('has-calculator'); document.body.classList.remove('uetp-calculator-open');
            node('drawer').classList.remove('is-open'); node('overlay').classList.remove('is-open');
            node('drawer').setAttribute('aria-hidden', 'true');
        }
        function schedule() {
            clearTimeout(timer); var id = ++sequence;
            el('target-result').textContent = 'Пересчёт…';
            timer = setTimeout(async function () {
                if (!row) return;
                var payload = values();
                if (!payload) { message('Проверьте целевые ДРР и ROI.'); return; }
                try {
                    var data = await request(api + '/' + encodeURIComponent(row.store_slug) + '/preview', {
                        method: 'POST', body: JSON.stringify(Object.assign(payload, { values: scenario })) });
                    if (id === sequence && row) show(data.rows[0]);
                } catch (error) { if (id === sequence) { el('target-result').textContent = '—'; message(error.message); } }
            }, 350);
        }
        ['goal-drr', 'goal-roi'].forEach(function (id) { el(id).addEventListener('input', schedule); });
        async function save(reset) {
            if (!row || busy || !config.canEdit) return;
            var payload = values(); if (!reset && !payload) { message('Проверьте целевые ДРР и ROI.'); return; }
            var selected = row, key = row.store_slug + '|' + row.article;
            busy = true; buttons(); clearTimeout(timer); sequence++;
            try {
                var path = api + '/' + encodeURIComponent(selected.store_slug) + '/targets';
                await request(reset ? path + '?article=' + encodeURIComponent(selected.article) + '&revision=' + selected.target_revision : path,
                    reset ? { method: 'DELETE' } : { method: 'PUT', body: JSON.stringify(Object.assign(payload, { revision: selected.target_revision })) });
                await context.load();
                var fresh = context.rows().find(function (item) { return item.store_slug + '|' + item.article === key; });
                if (row === selected && fresh) { open(fresh); message(reset ? 'Цели сброшены к настройкам кабинета.' : 'Цели сохранены. Цены на Маркете не изменялись.'); }
            } catch (error) { message(error.message); }
            finally { busy = false; buttons(); }
        }
        el('goal-save').addEventListener('click', function () { save(false); });
        el('goal-reset').addEventListener('click', function () { save(true); });
        node('drawer-close').addEventListener('click', close); node('overlay').addEventListener('click', close);
        document.addEventListener('keydown', function (event) { if (event.key === 'Escape') close(); });
        var settingsButton = el('target-settings'), dialog = el('goal-dialog'), cabinetRevision = 0, cabinetSequence = 0;
        settingsButton.hidden = !config.canManageGoals;
        (config.stores || []).forEach(function (store) {
            var option = document.createElement('option'); option.value = store.slug; option.textContent = store.name; el('goal-store').append(option);
        });
        async function loadCabinet() {
            var id = ++cabinetSequence; el('goal-status').textContent = 'Загрузка…'; el('goal-form').querySelector('[type="submit"]').disabled = true;
            try {
                var data = await request(api + '/settings/' + encodeURIComponent(el('goal-store').value));
                if (id !== cabinetSequence) return;
                cabinetRevision = data.revision; el('cabinet-goals').replaceChildren();
                [['target_drr_percent', 'ДРР, %', data.values.target_drr_percent], ['target_roi_percent', 'ROI для остальных кодов, %', data.values.target_roi_percent]]
                    .concat(Object.keys(data.values.target_roi_by_code).map(function (code) { return [code, 'ROI ' + code + ', %', data.values.target_roi_by_code[code]]; }))
                    .forEach(function (spec) {
                        var label = document.createElement('label'), text = document.createElement('span'), input = document.createElement('input');
                        text.textContent = spec[1]; input.type = 'number'; input.name = spec[0]; input.value = spec[2]; input.min = '0'; input.max = spec[0] === 'target_drr_percent' ? '100' : '1000000'; input.step = 'any'; input.required = true;
                        label.append(text, input); el('cabinet-goals').append(label);
                    });
                el('goal-status').textContent = ''; el('goal-form').querySelector('[type="submit"]').disabled = false;
            } catch (error) { if (id === cabinetSequence) el('goal-status').textContent = error.message; }
        }
        settingsButton.addEventListener('click', function () { dialog.showModal(); loadCabinet(); });
        el('goal-store').addEventListener('change', loadCabinet);
        el('goal-close').addEventListener('click', function () { dialog.close(); });
        el('goal-form').addEventListener('submit', async function (event) {
            event.preventDefault(); var form = event.currentTarget;
            if (!form.reportValidity()) return;
            var payload = { revision: cabinetRevision, target_roi_by_code: {} };
            Array.from(el('cabinet-goals').querySelectorAll('input')).forEach(function (input) {
                if (input.name.startsWith('target_')) payload[input.name] = Number(input.value);
                else payload.target_roi_by_code[input.name] = Number(input.value);
            });
            form.querySelector('[type="submit"]').disabled = true;
            try {
                await request(api + '/settings/' + encodeURIComponent(el('goal-store').value), { method: 'PUT', body: JSON.stringify(payload) });
                cabinetRevision++; el('goal-status').textContent = 'Сохранено.'; await context.load(); close();
            } catch (error) { el('goal-status').textContent = error.message; }
            finally { form.querySelector('[type="submit"]').disabled = false; }
        });
        return { open: open };
    } };
})();
