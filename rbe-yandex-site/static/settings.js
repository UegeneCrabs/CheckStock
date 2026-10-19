(function () {
  'use strict';
  const form = document.querySelector('#connection-form');
  const body = document.querySelector('#connections');
  const message = document.querySelector('#message');
  const checkAll = document.querySelector('#check-all');

  function esc(value) {
    const node = document.createElement('span');
    node.textContent = String(value);
    return node.innerHTML;
  }

  function status(item) {
    const labels = {
      connected: 'Доступны все', partial: 'Доступны не все',
      unavailable: 'Недоступны', limited: 'Лимит API', error: 'Ошибка проверки',
    };
    const state = item.status || 'unknown';
    const detail = item.detail || 'Проверка ещё не выполнялась';
    return '<span class="api-status api-status--' + esc(state) + '" title="' + esc(detail) + '">' +
      esc(labels[state] || 'Не проверен') + '</span>' +
      (state !== 'connected' ? '<small class="api-status-detail">' + esc(detail) + '</small>' : '');
  }

  async function load() {
    const result = await (await fetch('/api/connections')).json();
    body.innerHTML = (result.connections || []).map(function (item) {
      return '<tr><td>' + esc(item.store_slug).toUpperCase() + '</td><td>' + esc(item.business_id) +
        '</td><td>' + status(item) + '</td><td><button class="remove" data-store="' +
        esc(item.store_slug) + '" data-id="' + esc(item.business_id) + '">Удалить</button></td></tr>';
    }).join('') || '<tr><td colspan="4">Подключений пока нет.</td></tr>';
  }

  form.onsubmit = async function (event) {
    event.preventDefault();
    message.textContent = 'Проверяем ключ…';
    const data = Object.fromEntries(new FormData(form));
    data.business_id = Number(data.business_id);
    try {
      const response = await fetch('/api/connections', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.detail || 'Не удалось сохранить ключ');
      message.textContent = 'Ключ сохранён. Доступность магазинов показана ниже.';
      form.reset();
      await load();
    } catch (error) {
      message.textContent = error.message;
    }
  };

  checkAll.onclick = async function () {
    checkAll.disabled = true;
    message.textContent = 'Проверяем все подключения…';
    try {
      const response = await fetch('/api/connections/check', {method: 'POST'});
      const result = await response.json();
      if (!response.ok) throw Error(result.detail || 'Не удалось проверить подключения');
      message.textContent = result.connections.some(item => item.status !== 'connected')
        ? 'Проверка завершена: есть магазины с ограниченным доступом. Подробности в таблице.'
        : 'Проверка завершена: API доступно для всех магазинов.';
      await load();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      checkAll.disabled = false;
    }
  };

  body.onclick = async function (event) {
    const button = event.target.closest('button[data-store]');
    if (!button) return;
    if (!confirm('Удалить подключение?')) return;
    await fetch('/api/connections/' + encodeURIComponent(button.dataset.store) + '/' + button.dataset.id,
      {method: 'DELETE'});
    await load();
  };

  load();
})();
