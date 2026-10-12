(function () {
    'use strict';
    var form = document.getElementById('rbe-yandex-connection-form');
    if (!form) return;
    var message = form.querySelector('[data-rbe-message]');
    var key = form.querySelector('[name="api_key"]');
    var reveal = form.querySelector('[data-rbe-reveal]');
    var body = document.querySelector('[data-rbe-connections]');

    function show(text, error) { message.textContent = text; message.classList.toggle('is-error', Boolean(error)); }
    function request(url, options) {
        return fetch(url, options).then(function (response) { return response.json().then(function (data) {
            if (!response.ok || !data.ok) throw new Error(data.error || data.detail || 'Не удалось выполнить действие');
            return data;
        }); });
    }
    function render(connection) {
        var row = document.createElement('tr');
        row.innerHTML = '<td></td><td></td><td></td><td><button type="button" class="rbe-danger" data-rbe-remove>Удалить</button></td>';
        row.cells[0].textContent = connection.store_name;
        row.cells[1].textContent = connection.business_id;
        row.cells[2].textContent = (connection.campaign_ids || []).join(', ') || 'Определяются автоматически';
        row.querySelector('[data-rbe-remove]').dataset.store = connection.store_slug;
        row.querySelector('[data-rbe-remove]').dataset.business = connection.business_id;
        return row;
    }
    reveal.addEventListener('click', function () { key.type = key.type === 'password' ? 'text' : 'password'; reveal.textContent = key.type === 'password' ? 'Показать' : 'Скрыть'; });
    form.addEventListener('submit', function (event) {
        event.preventDefault(); var submit = form.querySelector('[type="submit"]'); submit.disabled = true; show('Проверяем доступ к кабинету…', false);
        request('/api/rbe/yandex-connections', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' }, body: JSON.stringify({ store_slug: form.store_slug.value, business_id: Number(form.business_id.value), api_key: key.value }) })
            .then(function (data) { var old = body.querySelector('[data-store="' + data.connection.store_slug + '"][data-business="' + data.connection.business_id + '"]'); if (old) old.closest('tr').remove(); var empty = body.querySelector('.rbe-connections__empty'); if (empty) empty.closest('tr').remove(); body.appendChild(render(data.connection)); form.reset(); show(data.message, false); })
            .catch(function (error) { show(error.message, true); }).finally(function () { submit.disabled = false; });
    });
    body.addEventListener('click', function (event) { var button = event.target.closest('[data-rbe-remove]'); if (!button || !window.confirm('Удалить это подключение?')) return; button.disabled = true; request('/api/rbe/yandex-connections/' + encodeURIComponent(button.dataset.store) + '/' + encodeURIComponent(button.dataset.business), { method: 'DELETE', headers: { 'X-Requested-With': 'fetch' } }).then(function () { button.closest('tr').remove(); show('Подключение удалено', false); }).catch(function (error) { button.disabled = false; show(error.message, true); }); });
}());
