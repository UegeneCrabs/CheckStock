(function () {
  'use strict';
  var from = document.querySelector('#from'), to = document.querySelector('#to'), store = document.querySelector('#store');
  var error = document.querySelector('#error');
  var refresh = document.querySelector('#refresh-period'), syncInfo = document.querySelector('#sync-info'), activeRun = null, wasSyncRunning = false;
  var formatter = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}), canSync = false;

  // Date-only values must never pass through UTC: in Moscow, toISOString()
  // changes a local midnight to the preceding calendar day.
  function dateKey(day) { return day.getFullYear() + '-' + String(day.getMonth() + 1).padStart(2, '0') + '-' + String(day.getDate()).padStart(2, '0'); }
  function parseDate(value) { var parts = value.split('-').map(Number); return new Date(parts[0], parts[1] - 1, parts[2]); }
  function setDefaults() { var today = new Date(), finish = new Date(today), start; finish.setDate(today.getDate() - 1); start = new Date(finish); start.setDate(finish.getDate() - 6); from.value = dateKey(start); to.value = dateKey(finish); }
  function valueText(cell, value) { if (value === null || value === undefined) return '—'; if (cell.dataset.format === 'integer') return new Intl.NumberFormat('ru-RU').format(value); if (cell.dataset.format === 'percent') return formatter.format(value) + '%'; return formatter.format(value); }

  var copyIcon = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>';
  async function copyNumber(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(text); return; } catch (_) { /* Try the HTTP-compatible fallback. */ }
    }
    var input = document.createElement('textarea'), focused = document.activeElement;
    input.value = text; input.readOnly = true; input.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.append(input);
    try {
      input.select(); input.setSelectionRange(0, text.length);
      if (!document.execCommand('copy')) throw Error('Не удалось скопировать число');
    } finally { input.remove(); if (focused && focused.focus) focused.focus({preventScroll:true}); }
  }
  function renderValue(cell, value) {
    var text = valueText(cell, value), wrap = document.createElement('div'), number = document.createElement('span');
    wrap.className = 'report-value'; number.textContent = text; wrap.append(number);
    if (typeof value === 'number' && Number.isFinite(value)) {
      var copy = document.createElement('button');
      copy.type = 'button'; copy.className = 'copy-number'; copy.innerHTML = copyIcon;
      copy.title = 'Копировать число'; copy.setAttribute('aria-label', 'Копировать число ' + text);
      copy.onclick = async function () {
        copy.disabled = true;
        var notice = document.querySelector('#copy-status'); notice.textContent = '';
        try {
          await copyNumber(text.replace(/[\s%]/g, ''));
          copy.textContent = '✓'; copy.classList.add('copied'); copy.title = 'Скопировано'; notice.textContent = 'Число скопировано';
        } catch (_) { copy.title = 'Не удалось скопировать. Попробуйте ещё раз'; notice.textContent = copy.title; }
        finally {
          copy.disabled = false;
          window.setTimeout(function () { copy.innerHTML = copyIcon; copy.classList.remove('copied'); copy.title = 'Копировать число'; }, 1500);
        }
      };
      wrap.append(copy);
    }
    cell.replaceChildren(wrap);
  }

  function render(values, quality, syncRunning) {
    document.querySelectorAll('[data-key]').forEach(function (cell) {
      var key = cell.dataset.key, state = quality[key] || {}, status = document.querySelector('[data-status-for="' + key + '"]'), badge = document.createElement('span');
      renderValue(cell, values[key]); badge.textContent = state.complete ? 'Полные' : 'Неполные'; badge.className = 'status ' + (state.complete ? 'status--complete' : 'status--incomplete'); badge.title = state.detail || 'Статус данных неизвестен'; status.className = 'status-cell'; status.replaceChildren(badge);
      if (!state.complete && canSync && state.can_repair) {
        var repair = document.createElement('button'); repair.type = 'button'; repair.className = 'repair'; repair.textContent = syncRunning ? 'Загрузка…' : 'Загрузить'; repair.disabled = Boolean(syncRunning); repair.title = 'Загрузить только отсутствующие данные для этого показателя'; repair.onclick = function () { upload(key, repair); }; status.append(repair);
      }
    });
  }

  async function load() {
    error.textContent = '';
    var query = new URLSearchParams({date_from: from.value, date_to: to.value}); if (store.value) query.set('store_slug', store.value);
    try { var response = await fetch('/api/report?' + query), payload = await response.json(); if (!response.ok) throw Error(payload.detail || 'Отчёт не загружен'); canSync = Boolean(payload.can_sync); refresh.hidden = !canSync; refresh.disabled = Boolean(payload.sync_running || activeRun); refresh.textContent = refresh.disabled ? 'Обновление…' : 'Обновить по API'; document.querySelector('#period').textContent = parseDate(from.value).toLocaleDateString('ru-RU') + ' — ' + parseDate(to.value).toLocaleDateString('ru-RU'); render(payload.values, payload.quality || {}, payload.sync_running || activeRun); } catch (exception) { error.textContent = exception.message; }
  }
  async function upload(metric, button) {
    button.disabled = true; button.textContent = 'Загрузка…'; error.textContent = '';
    try { var body = {date_from: from.value, date_to: to.value}; if (metric) body.metric = metric; if (store.value) body.store_slug = store.value; var response = await fetch('/api/sync', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}), payload = await response.json(); if (!response.ok) throw Error(payload.detail || 'Не удалось запустить загрузку'); error.style.color = '#297342'; error.textContent = payload.detail || 'Загрузка запущена.'; if (payload.run_id) { activeRun = payload.run_id; sessionStorage.setItem('rbe-sync-run', String(activeRun)); refresh.disabled = true; pollRun(activeRun, button); } else { await load(); } } catch (exception) { error.style.color = '#ba3030'; error.textContent = exception.message; button.disabled = false; button.textContent = metric ? 'Загрузить' : 'Обновить по API'; }
  }
  async function pollRun(runId, button) {
    try {
      var response = await fetch('/api/targeted-sync/' + runId), payload = await response.json(); if (!response.ok) throw Error(payload.detail || 'Не удалось получить статус загрузки');
      var run = payload.run, active = run.status === 'queued' || run.status === 'running' || run.status === 'retrying';
      if (active) { error.style.color = run.status === 'retrying' ? '#a06800' : '#297342'; error.textContent = run.detail; button.textContent = run.status === 'retrying' ? 'Повтор будет' : 'Загрузка…'; window.setTimeout(function () { pollRun(runId, button); }, 3000); return; }
      activeRun = null; sessionStorage.removeItem('rbe-sync-run');
      var finalColor = run.status === 'completed' ? '#297342' : (run.status === 'waiting' ? '#a06800' : '#ba3030'), finalDetail = run.detail; await load(); error.style.color = finalColor; error.textContent = finalDetail; updateSyncInfo();
    } catch (exception) { error.style.color = '#ba3030'; error.textContent = exception.message + '. Повторная проверка статуса через 15 секунд.'; if (response && response.status === 404) { activeRun = null; sessionStorage.removeItem('rbe-sync-run'); button.disabled = false; } else { window.setTimeout(function () { pollRun(runId, button); }, 15000); } }
  }
  async function updateSyncInfo() {
    try {
      var response = await fetch('/api/sync-status'); if (!response.ok) return; var state = await response.json();
      syncInfo.textContent = (state.automatic ? 'Автопроверка: пауза ' + state.interval_minutes + ' мин. между циклами. Повторная сверка последних ' + state.lookback_days + ' дней.' : 'Автопроверка выключена.') + ' В очереди: ' + state.pending_store_days + ' дней по магазинам.' + (state.running ? ' Загрузка выполняется.' : '');
      if (wasSyncRunning && !state.running && !activeRun) await load();
      wasSyncRunning = Boolean(state.running);
      if (canSync && !activeRun) { refresh.disabled = state.running; refresh.textContent = state.running ? 'Обновление…' : 'Обновить по API'; document.querySelectorAll('.repair').forEach(function (button) { button.disabled = state.running; button.textContent = state.running ? 'Загрузка…' : 'Загрузить'; }); }
    } catch (_) { syncInfo.textContent = 'Не удалось проверить состояние автозагрузки.'; }
  }
  async function loadStores() { var payload = await (await fetch('/api/stores')).json(); (payload.stores || []).forEach(function (name) { var option = document.createElement('option'); option.value = name; option.textContent = name.toUpperCase(); store.append(option); }); }
  refresh.onclick = function () { upload(null, refresh); };
  document.querySelector('#apply').onclick = load; store.onchange = load; setDefaults();
  loadStores().then(load).then(function () { var saved = sessionStorage.getItem('rbe-sync-run'); if (saved) { activeRun = Number(saved); pollRun(activeRun, refresh); } updateSyncInfo(); });
  window.setInterval(updateSyncInfo, 15000);
}());
