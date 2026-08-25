(function () {
    'use strict';
    var root = document.getElementById('unit-economics-1c-report');
    var configNode = document.getElementById('ue1cr-config');
    if (!root || !configNode) return;
    var config = JSON.parse(configNode.textContent || '{}');
    var nodes = {
        form: document.getElementById('ue1cr-filters'), from: document.getElementById('ue1cr-date-from'),
        to: document.getElementById('ue1cr-date-to'), store: document.getElementById('ue1cr-store'),
        subject: document.getElementById('ue1cr-subject'), legal: document.getElementById('ue1cr-legal'),
        submit: document.getElementById('ue1cr-submit'), rows: document.getElementById('ue1cr-rows'),
        total: document.getElementById('ue1cr-total'), summary: document.getElementById('ue1cr-summary'),
        empty: document.getElementById('ue1cr-empty'), error: document.getElementById('ue1cr-error')
    };
    var number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    var money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function value(item, suffix) {
        return item === null || item === undefined ? '—' : number.format(item) + (suffix || '');
    }
    function rub(item) { return item === null || item === undefined ? '—' : money.format(item); }
    function rowHtml(row) {
        return '<tr><td><strong>' + escapeHtml(row.name) + '</strong><span>Арт. '
            + escapeHtml(row.article) + '</span></td><td>' + escapeHtml(row.store_name)
            + '</td><td>' + escapeHtml(row.subject) + '</td><td>' + escapeHtml(row.legal_entity)
            + '</td><td class="num">' + value(row.orders_count) + '</td><td class="num">'
            + rub(row.orders_amount) + '</td><td class="num">' + value(row.buyout_percent, '%')
            + '</td><td class="num">' + value(row.stock) + '</td><td class="num">'
            + value(row.ctr, '%') + '</td><td class="num">' + rub(row.cpc)
            + '</td><td class="num">' + rub(row.advertising_spend) + '</td><td class="num">'
            + rub(row.margin) + '</td><td class="num">' + value(row.roi, '%') + '</td></tr>';
    }
    function totalHtml(total) {
        return '<tr><th colspan="4">Итого по выборке</th><th class="num">' + value(total.orders_count)
            + '</th><th class="num">' + rub(total.orders_amount) + '</th><th class="num">'
            + value(total.buyout_percent, '%') + '</th><th class="num">' + value(total.stock)
            + '</th><th class="num">' + value(total.ctr, '%') + '</th><th class="num">'
            + rub(total.cpc) + '</th><th class="num">' + rub(total.advertising_spend)
            + '</th><th class="num">' + rub(total.margin) + '</th><th class="num">'
            + value(total.roi, '%') + '</th></tr>';
    }
    function updateOptions(select, values, firstLabel) {
        var selected = select.value;
        select.innerHTML = '<option value="">' + escapeHtml(firstLabel) + '</option>'
            + values.map(function (item) { return '<option value="' + escapeHtml(item) + '">'
                + escapeHtml(item) + '</option>'; }).join('');
        if (values.indexOf(selected) !== -1) select.value = selected;
    }
    async function load() {
        nodes.submit.disabled = true;
        nodes.submit.textContent = 'Считаем…';
        nodes.error.hidden = true;
        var query = new URLSearchParams({
            date_from: nodes.from.value, date_to: nodes.to.value, store: nodes.store.value
        });
        if (nodes.subject.value) query.set('subject', nodes.subject.value);
        if (nodes.legal.value) query.set('legal_entity', nodes.legal.value);
        try {
            var response = await window.fetch('/api/unit-economics-1c/reports/unit-profit?' + query.toString(), {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' }
            });
            var result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'Не удалось сформировать отчёт');
            nodes.rows.innerHTML = result.rows.map(rowHtml).join('');
            nodes.total.innerHTML = totalHtml(result.totals);
            nodes.empty.hidden = result.rows.length > 0;
            nodes.summary.innerHTML = '<div><span>Период</span><strong>' + escapeHtml(result.period_from)
                + ' — ' + escapeHtml(result.period_to) + '</strong></div><div><span>Артикулов</span><strong>'
                + value(result.rows.length) + '</strong></div><div><span>Заказы</span><strong>'
                + rub(result.totals.orders_amount) + '</strong></div><div><span>Маржа</span><strong>'
                + rub(result.totals.margin) + '</strong></div><div><span>ROI</span><strong>'
                + value(result.totals.roi, '%') + '</strong></div>';
            updateOptions(nodes.subject, result.filters.subjects || [], 'Все предметы');
            updateOptions(nodes.legal, result.filters.legal_entities || [], 'Все юрлица');
        } catch (error) {
            nodes.error.textContent = error.message || 'Не удалось сформировать отчёт';
            nodes.error.hidden = false;
        } finally {
            nodes.submit.disabled = false;
            nodes.submit.textContent = 'Сформировать';
        }
    }
    (config.stores || []).forEach(function (store) {
        var option = document.createElement('option');
        option.value = store.slug; option.textContent = store.name; nodes.store.appendChild(option);
    });
    nodes.from.value = config.defaultDateFrom;
    nodes.to.value = config.defaultDateTo;
    nodes.form.addEventListener('submit', function (event) { event.preventDefault(); load(); });
    load();
})();
