(function () {
  'use strict';
  var dateInput = document.querySelector('#cost-date'), query = document.querySelector('#query'), table = document.querySelector('#costs');
  var message = document.querySelector('#message'), sync = document.querySelector('#sync-costs'), refresh = document.querySelector('#refresh');
  function today() { return new Date().toLocaleDateString('en-CA', {timeZone: 'Europe/Moscow'}); }
  function esc(value) { var node = document.createElement('span'); node.textContent = String(value == null ? '' : value); return node.innerHTML; }
  function money(value) { return new Intl.NumberFormat('ru-RU', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(Number(value)) + ' ₽'; }
  function source(value) { return {google: 'Google Sheets', excel: 'Excel', manual: 'Вручную'}[value] || value; }
  function setMessage(text, error) { message.textContent = text || ''; message.className = error ? 'error-text' : ''; }
  async function loadStatus() {
    var r = await fetch('/api/costs/status'), p = await r.json();
    if (!r.ok) throw Error(p.detail || 'Не удалось получить статус');
    document.querySelector('#known-count').textContent = p.known_articles;
    document.querySelector('#nightly').textContent = 'каждый день в ' + String(p.nightly_hour_msk).padStart(2, '0') + ':00 МСК';
    var last = p.last_run, runText = 'Загрузка ещё не запускалась';
    if (last) {
      runText = 'Последняя загрузка: ' + last.status + ', ' + last.cost_date;
      try { var detail = JSON.parse(last.detail || '{}'); if (detail.conflicts && detail.conflicts.length) runText += '. Конфликтов: ' + detail.conflicts.length; } catch (ignore) {}
    }
    document.querySelector('#last-run').textContent = runText;
    document.querySelector('#sync-state').textContent = p.public_sheet_access ? 'Источник: общедоступная Google Sheets' : '';
    sync.disabled = Boolean(p.sync_running);
  }
  async function loadCosts() {
    var params = new URLSearchParams({cost_date: dateInput.value, query: query.value.trim()}), r = await fetch('/api/costs?' + params), p = await r.json();
    if (!r.ok) throw Error(p.detail || 'Не удалось загрузить данные');
    table.innerHTML = p.rows.map(function (row) { return '<tr><td>' + esc(row.article) + '</td><td>' + money(row.cost) + '</td><td>' + esc(source(row.source)) + '</td><td>' + esc(row.sheet_name || '—') + '</td><td>' + esc(new Date(row.imported_at).toLocaleString('ru-RU')) + '</td></tr>'; }).join('') || '<tr><td colspan="5">На выбранную дату себестоимость ещё не загружена.</td></tr>';
  }
  async function reload() { try { await Promise.all([loadStatus(), loadCosts()]); } catch (error) { setMessage(error.message, true); } }
  document.querySelector('#manual-form').onsubmit = async function (event) { event.preventDefault(); var data = Object.fromEntries(new FormData(event.currentTarget)); data.cost_date = dateInput.value; data.cost = Number(data.cost); try { var r = await fetch('/api/costs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)}), p = await r.json(); if (!r.ok) throw Error(p.detail || 'Не удалось сохранить'); setMessage('Себестоимость сохранена.'); event.currentTarget.reset(); reload(); } catch (error) { setMessage(error.message, true); } };
  document.querySelector('#excel-form').onsubmit = async function (event) { event.preventDefault(); var form = new FormData(event.currentTarget); try { var r = await fetch('/api/costs/import-excel?cost_date=' + encodeURIComponent(dateInput.value), {method: 'POST', body: form}), p = await r.json(); if (!r.ok) throw Error(p.detail || 'Не удалось импортировать'); setMessage('Импортировано: ' + p.accepted + '. Добавлено: ' + p.inserted + ', обновлено: ' + p.updated + '.'); event.currentTarget.reset(); reload(); } catch (error) { setMessage(error.message, true); } };
  sync.onclick = async function () { sync.disabled = true; setMessage('Загрузка из Google Sheets запущена…'); try { var r = await fetch('/api/costs/sync', {method: 'POST'}), p = await r.json(); if (!r.ok) throw Error(p.detail || 'Не удалось запустить загрузку'); setMessage(p.detail + ' Результат появится после завершения.'); } catch (error) { setMessage(error.message, true); } finally { setTimeout(reload, 1000); } };
  refresh.onclick = reload; dateInput.onchange = reload; dateInput.value = today(); reload();
}());
