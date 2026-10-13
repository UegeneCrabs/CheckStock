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

  var breakdowns = new Map();
  function renderBreakdown(cell, value, detail, state) {
    var key = cell.dataset.key, entry = breakdowns.get(key);
    if (!entry) {
      var row = cell.closest('tr'), metric = row.querySelector('.metric'), toggle = document.createElement('button'), panel = document.createElement('tr'), content = document.createElement('td');
      toggle.type = 'button'; toggle.className = 'metric-toggle'; toggle.textContent = '▸';
      toggle.setAttribute('aria-label', 'Расчёт: ' + metric.textContent.replace('ⓘ', '').trim());
      toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-controls', 'breakdown-' + key);
      panel.id = 'breakdown-' + key; panel.className = 'breakdown-row'; panel.hidden = true; content.colSpan = 3; panel.append(content);
      metric.prepend(toggle); row.after(panel);
      toggle.onclick = function () { panel.hidden = !panel.hidden; toggle.textContent = panel.hidden ? '▸' : '▾'; toggle.setAttribute('aria-expanded', String(!panel.hidden)); };
      entry = {panel:panel, content:content, help:row.querySelector('.metric-help')}; breakdowns.set(key, entry);
    }
    entry.content.replaceChildren();
    if (!detail) {
      var missing = document.createElement('p'); missing.textContent = 'Подробности расчёта пока недоступны.'; entry.content.append(missing); return;
    }
    entry.help.dataset.tip = detail.description;
    if (detail.note || !state.complete) {
      var note = document.createElement('p'); note.className = 'breakdown-note';
      note.textContent = [detail.note, !state.complete ? 'Данные за выбранный период неполные. Расчёт может измениться после обновления.' : ''].filter(Boolean).join(' '); entry.content.append(note);
    }
    var list = document.createElement('dl'); list.className = 'breakdown-list';
    (detail.rows || []).forEach(function (part) {
      var item = document.createElement('div'), label = document.createElement('dt'), amount = document.createElement('dd');
      label.textContent = (part.sign ? part.sign + ' ' : '') + part.label;
      if (cell.dataset.format === 'integer') amount.dataset.format = 'integer';
      // Colour the contribution to the total, not the copy button or label.
      // A negative deduction increases the total; unknown values stay neutral.
      var direction = part.sign === '+' ? 1 : (part.sign === '−' || part.sign === '-' ? -1 : 0);
      if (direction && typeof part.value === 'number' && Number.isFinite(part.value) && part.value !== 0) {
        amount.dataset.effect = direction * part.value > 0 ? 'positive' : 'negative';
      }
      renderValue(amount, part.value); item.append(label); item.append(amount); list.append(item);
    });
    if ((detail.rows || []).length) {
      var total = document.createElement('div'), totalLabel = document.createElement('dt'), totalValue = document.createElement('dd');
      total.className = 'breakdown-total'; totalLabel.textContent = '= Итог'; totalValue.dataset.format = cell.dataset.format || '';
      renderValue(totalValue, value); total.append(totalLabel); total.append(totalValue); list.append(total);
    }
    entry.content.append(list);
  }

  function render(values, quality, syncRunning, details) {
    document.querySelectorAll('[data-key]').forEach(function (cell) {
      var key = cell.dataset.key, state = quality[key] || {}, status = document.querySelector('[data-status-for="' + key + '"]'), badge = document.createElement('span');
      renderValue(cell, values[key]); badge.textContent = state.complete ? 'Полные' : 'Неполные'; badge.className = 'status ' + (state.complete ? 'status--complete' : 'status--incomplete'); badge.title = state.detail || 'Статус данных неизвестен'; status.className = 'status-cell'; status.replaceChildren(badge);
      renderBreakdown(cell, values[key], (details || {})[key], state);
      if (!state.complete && canSync && state.can_repair) {
        var repair = document.createElement('button'); repair.type = 'button'; repair.className = 'repair'; repair.textContent = syncRunning ? 'Загрузка…' : 'Загрузить'; repair.disabled = Boolean(syncRunning); repair.title = 'Загрузить только отсутствующие данные для этого показателя'; repair.onclick = function () { upload(key, repair); }; status.append(repair);
      }
    });
  }

  async function load() {
    error.textContent = '';
    var query = new URLSearchParams({date_from: from.value, date_to: to.value}); if (store.value) query.set('store_slug', store.value);
    try { var response = await fetch('/api/report?' + query), payload = await response.json(); if (!response.ok) throw Error(payload.detail || 'Отчёт не загружен'); canSync = Boolean(payload.can_sync); refresh.hidden = !canSync; refresh.disabled = Boolean(payload.sync_running || activeRun); refresh.textContent = refresh.disabled ? 'Обновление…' : 'Обновить по API'; document.querySelector('#period').textContent = parseDate(from.value).toLocaleDateString('ru-RU') + ' — ' + parseDate(to.value).toLocaleDateString('ru-RU'); render(payload.values, payload.quality || {}, payload.sync_running || activeRun, payload.details); } catch (exception) { error.textContent = exception.message; }
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
      syncInfo.textContent = (state.automatic ? 'Автопроверка включена. Сначала свежие даты. Пауза между циклами: ' + state.interval_minutes + ' мин.' : 'Автопроверка выключена.') + ' Ожидают проверки: ' + state.pending_store_days + ' записей «магазин + день».' + (state.running ? ' Загрузка выполняется.' : '');
      renderQueue(state.queue || []);
      renderSyncGuide(state);
      if (wasSyncRunning && !state.running && !activeRun) await load();
      wasSyncRunning = Boolean(state.running);
      if (canSync && !activeRun) { refresh.disabled = state.running; refresh.textContent = state.running ? 'Обновление…' : 'Обновить по API'; document.querySelectorAll('.repair').forEach(function (button) { button.disabled = state.running; button.textContent = state.running ? 'Загрузка…' : 'Загрузить'; }); }
    } catch (_) {
      syncInfo.textContent = 'Не удалось проверить состояние автозагрузки.';
      document.querySelector('#sync-guide-state').textContent = 'Не удалось получить текущее состояние. Инструкция ниже описывает правила, но не подтверждает, что автопроверка сейчас включена.';
    }
  }
  function renderSyncGuide(state) {
    document.querySelector('#sync-guide-state').textContent = state.automatic
      ? 'Автопроверка включена. Эти правила применяются к автоматическим загрузкам.'
      : 'Автопроверка сейчас выключена. Сайт показывает сохранённые данные, очередь ждёт. Правила ниже начнут действовать после включения автоматики. Обновление вручную доступно администратору.';
    [['lookback', state.lookback_days], ['batch', state.max_batch_days],
      ['jobs', state.max_cycle_jobs], ['interval', state.interval_minutes],
      ['cooldown', state.cooldown_seconds]].forEach(function (pair) {
      document.querySelector('#sync-guide-' + pair[0]).textContent = pair[1] == null ? '—' : String(pair[1]);
    });
    document.querySelector('#sync-guide-cutoff').textContent = state.cutoff ? parseDate(state.cutoff).toLocaleDateString('ru-RU') : '—';
  }
  var queueGroupsOpen = {};
  function renderQueue(items) {
    var box = document.querySelector('#sync-queue-items');
    if (!box) return;
    box.replaceChildren();
    document.querySelector('#sync-queue-summary').textContent = 'Очередь проверок (' + items.length + ') — магазины, даты и причины';
    if (!items.length) { box.textContent = 'Незавершённых проверок нет. Плановая сверка выполняется отдельно, если автопроверка включена.'; return; }
    var groups = {};
    items.forEach(function (item) { var key = item.type; if (!groups[key]) groups[key] = []; groups[key].push(item); });
    Object.keys(groups).forEach(function (key) {
      var group = document.createElement('details'), title = document.createElement('summary'), list = document.createElement('ul');
      group.open = Boolean(queueGroupsOpen[key]);
      group.ontoggle = function () { queueGroupsOpen[key] = group.open; };
      title.textContent = groups[key][0].title + ' — ' + groups[key].length; group.append(title);
      groups[key].forEach(function (item) {
        var row = document.createElement('li');
        row.textContent = item.store.toUpperCase() + ' · ' + parseDate(item.date).toLocaleDateString('ru-RU') + ' — ' + item.detail;
        if (item.last_attempt) row.textContent += ' Последняя попытка: ' + new Date(item.last_attempt).toLocaleString('ru-RU') + '.';
        list.append(row);
      });
      group.append(list); box.append(group);
    });
  }
  async function loadStores() { var payload = await (await fetch('/api/stores')).json(); (payload.stores || []).forEach(function (name) { var option = document.createElement('option'); option.value = name; option.textContent = name.toUpperCase(); store.append(option); }); }
  refresh.onclick = function () { upload(null, refresh); };
  document.querySelector('#apply').onclick = load; store.onchange = load; setDefaults();
  loadStores().then(load).then(function () { var saved = sessionStorage.getItem('rbe-sync-run'); if (saved) { activeRun = Number(saved); pollRun(activeRun, refresh); } updateSyncInfo(); });
  window.setInterval(updateSyncInfo, 15000);
}());
