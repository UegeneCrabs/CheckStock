(function () {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const from = $('#cost-from'), to = $('#cost-to'), query = $('#query'), table = $('#costs');
  const message = $('#message'), warning = $('#cost-warning'), sync = $('#sync-costs');
  const previous = $('#cost-prev'), next = $('#cost-next'), count = $('#cost-count');
  const pageSize = 200;
  let offset = 0, requestId = 0, selected = null, statusTimer;
  const today = () => new Date().toLocaleDateString('en-CA', {timeZone: 'Europe/Moscow'});
  const dateLabel = value => value ? value.split('-').reverse().join('.') : '—';
  function esc(value) {
    const node = document.createElement('span');
    node.textContent = String(value == null ? '' : value);
    return node.innerHTML;
  }
  const money = value => new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(Number(value)) + ' ₽';
  const source = value => ({google: 'Google Sheets', excel: 'Excel', manual: 'Вручную'}[value] || value);
  function setMessage(text, error = false) {
    message.textContent = text || '';
    message.className = error ? 'error-text' : '';
  }
  async function response(url, options) {
    const r = await fetch(url, options), p = await r.json();
    if (!r.ok) throw Error(typeof p.detail === 'string' ? p.detail : 'Не удалось выполнить запрос. Проверьте введённые данные.');
    return p;
  }
  async function loadStatus() {
    const p = await response('/api/costs/status');
    $('#known-count').textContent = p.known_articles;
    $('#nightly').textContent = 'каждый день в ' + String(p.nightly_hour_msk).padStart(2, '0') + ':00 МСК';
    const labels = {completed: 'завершена', partial: 'есть расхождения', retrying: 'ожидает повторной попытки', running: 'выполняется', failed: 'не удалась'};
    const last = p.last_run;
    let runText = 'Загрузка ещё не запускалась';
    if (last) {
      runText = 'Последняя загрузка: ' + (labels[last.status] || last.status) + ', дата цен: ' + dateLabel(last.cost_date);
      try {
        const detail = JSON.parse(last.detail || '{}');
        if (detail.conflicts && detail.conflicts.length) runText += '. Расхождений: ' + detail.conflicts.length;
        if (detail.skipped_without_store) runText += '. Пропущено цен без магазина: ' + detail.skipped_without_store;
      } catch (ignore) { /* Older runs can have a plain-text description. */ }
    }
    $('#last-run').textContent = runText;
    $('#sync-state').textContent = p.sync_running ? 'Сейчас идёт загрузка из Google Sheets.' : (p.public_sheet_access ? 'Источник: общедоступная Google Sheets' : '');
    sync.disabled = Boolean(p.sync_running);
    sync.textContent = p.sync_running ? 'Загрузка…' : 'Загрузить принудительно';
    clearTimeout(statusTimer);
    if (p.sync_running) statusTimer = setTimeout(async () => {
      try { await loadStatus(); if (!sync.disabled) await loadCosts(); }
      catch (error) { setMessage(error.message, true); }
    }, 5000);
  }
  function rowHtml(row) {
    const timestamp = new Date(row.imported_at);
    const updated = Number.isNaN(timestamp.getTime()) ? '—' : timestamp.toLocaleString('ru-RU', {timeZone: 'Europe/Moscow'});
    return '<tr><td>' + esc(dateLabel(row.cost_date)) + '</td><td>' + esc(row.article) + '</td><td>' +
      esc(row.store_slug.toUpperCase()) + '</td><td>' + esc(source(row.source)) +
      '</td><td>' + esc(row.sheet_name || '—') + '</td><td>' + esc(updated) + '</td><td>' +
      (row.conflict ? '<span class="error-text">Разные цены — не используется</span>' : money(row.cost)) + '</td></tr>';
  }
  async function loadCosts() {
    const id = ++requestId;
    previous.disabled = next.disabled = true;
    count.textContent = 'Загрузка…';
    table.setAttribute('aria-busy', 'true');
    try {
      const params = new URLSearchParams({...selected, limit: pageSize, offset});
      const p = await response('/api/costs?' + params);
      if (id !== requestId) return;
      table.innerHTML = p.rows.map(rowHtml).join('') || '<tr><td colspan="7">За выбранный период и по этому поиску сохранённых цен нет.</td></tr>';
      count.textContent = p.total ? 'Показано ' + (offset + 1) + '–' + (offset + p.rows.length) + ' из ' + p.total : 'Найдено записей: 0';
      previous.disabled = offset === 0;
      next.disabled = offset + p.rows.length >= p.total;
      warning.textContent = p.conflicts && p.conflicts.length ? 'В выбранном периоде есть расхождения цен для одного магазина и артикула (' + p.conflicts.length + '). Такие цены не используются. Уточните их в исходной таблице.' : '';
    } catch (error) {
      if (id !== requestId) return;
      table.innerHTML = '<tr><td colspan="7">Не удалось загрузить цены. Нажмите «Показать», чтобы повторить.</td></tr>';
      count.textContent = '';
      warning.textContent = '';
      setMessage(error.message, true);
    } finally {
      if (id === requestId) table.setAttribute('aria-busy', 'false');
    }
  }
  function applyFilters() {
    if (!from.value || !to.value || from.value > to.value) {
      setMessage('Укажите начало и конец периода в правильном порядке.', true);
      return;
    }
    setMessage('');
    selected = {date_from: from.value, date_to: to.value, query: query.value.trim()};
    offset = 0;
    loadCosts();
  }
  $('#cost-filter').onsubmit = event => { event.preventDefault(); applyFilters(); };
  from.onchange = to.onchange = applyFilters;
  previous.onclick = () => { offset = Math.max(0, offset - pageSize); loadCosts(); };
  next.onclick = () => { offset += pageSize; loadCosts(); };

  $('#manual-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget, button = form.querySelector('button'), data = Object.fromEntries(new FormData(form));
    if (!data.store_slug) { setMessage('Выберите магазин.', true); return; }
    data.cost = Number(data.cost);
    button.disabled = true;
    try {
      await response('/api/costs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
      setMessage('Себестоимость сохранена на ' + dateLabel(data.cost_date) + ' для ' + data.store_slug.toUpperCase() + '.');
      await loadCosts();
      await loadStatus();
    } catch (error) { setMessage(error.message, true); }
    finally { button.disabled = false; }
  };
  $('#excel-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget, button = form.querySelector('button'), data = new FormData(form);
    if (!data.get('store_slug')) { setMessage('Выберите магазин.', true); return; }
    button.disabled = true;
    try {
      const p = await response('/api/costs/import-excel?' + new URLSearchParams({cost_date: data.get('cost_date'), store_slug: data.get('store_slug')}), {method: 'POST', body: data});
      setMessage('На ' + dateLabel(data.get('cost_date')) + ' импортировано: ' + p.accepted + ' для ' + data.get('store_slug').toUpperCase() + '. Добавлено: ' + p.inserted + ', обновлено: ' + p.updated + '.');
      await loadCosts();
      await loadStatus();
    } catch (error) { setMessage(error.message, true); }
    finally { button.disabled = false; }
  };
  sync.onclick = async () => {
    sync.disabled = true;
    setMessage('Загрузка из Google Sheets запущена…');
    try {
      const p = await response('/api/costs/sync', {method: 'POST'});
      setMessage(p.detail + ' Результат появится после завершения.');
    } catch (error) { setMessage(error.message, true); }
    finally {
      try { await loadStatus(); if (!sync.disabled) await loadCosts(); }
      catch (error) { sync.disabled = false; setMessage(error.message, true); }
    }
  };
  from.value = to.value = today();
  response('/api/stores').then(p => {
    document.querySelectorAll('select[name="store_slug"]').forEach(select => {
      select.innerHTML = '<option value="" disabled selected>Выберите магазин</option>' + p.stores.map(store => '<option value="' + esc(store) + '">' + esc(store.toUpperCase()) + '</option>').join('');
    });
  }).catch(error => setMessage('Не удалось загрузить список магазинов. ' + error.message, true));
  document.querySelectorAll('input[name="cost_date"]').forEach(input => { input.value = today(); });
  applyFilters();
  loadStatus().catch(error => setMessage(error.message, true));
}());
