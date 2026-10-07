/* Finance reads are cancellable; rendered details always carry the summary revision. */
(() => {
  'use strict';
  const api = '/api/finance-reports/yandex';
  const adminApi = '/api/admin/integrations/yandex-finance';
  const $ = id => document.getElementById(id);
  const filters = $('finance-filters');
  const admin = $('finance-admin');
  const dialog = $('finance-detail');
  const fmt = value => {
    if(value == null) return '—';
    const match=String(value).match(/^(-?)(\d+)(?:\.(\d+))?$/);
    if(!match) return '—';
    const fraction=(match[3]||'').replace(/0+$/,'');
    return match[1]+new Intl.NumberFormat('ru-RU').format(BigInt(match[2]))+(fraction?','+fraction:'');
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {sale:'Реализация',return:'Возврат',expense:'Расход',income:'Начисление',order:'Заказ',payment:'Выплата',unknown:'Не классифицировано',realization:'Реализация',services:'Услуги',orders:'Заказы',payments:'Выплаты'};
  Object.assign(labels,{commission:'Комиссия',acquiring:'Платежи',logistics:'Логистика',storage:'Хранение',advertising:'Реклама',other:'Прочее',unclassified:'Не классифицировано'});
  let summary = null, query = '', sequence = 0, controller = null, detailController = null, detailSequence = 0, selection = null;
  let keyConnection = null;
  async function request(url, options = {}) {
    const response = await fetch(url, {credentials:'same-origin', cache:'no-store', ...options, headers:{Accept:'application/json', ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers}});
    let body;
    try { body = await response.json(); } catch { throw new Error('Сервер вернул неожиданный ответ. Проверьте вход в систему.'); }
    if (!response.ok) { const message = body.detail || body.error || `Ошибка ${response.status}`; throw new Error(typeof message === 'string' ? message : 'Проверьте значения полей формы'); }
    return body;
  }
  function metric(key, value, day = '') {
    const title = value.reasons.length ? value.reasons.slice(0, 15).join('\n') : value.label;
    const inside = `${fmt(value.value)}${!value.complete ? `<small>известно ${fmt(value.known_value)}</small>` : ''}`;
    if (key === 'margin_percent') return `<span class="finance-value" title="${esc(title)}">${inside}</span>`;
    return `<button type="button" class="finance-value ${!value.complete ? 'is-partial' : ''}" data-metric="${esc(key)}" data-day="${esc(day)}" title="${esc(title)}">${inside}</button>`;
  }
  function render(data) {
    $('finance-report').hidden = false;
    $('finance-period').textContent = `${data.date_from} — ${data.date_to}`;
    $('finance-version').textContent = `Магазинов: ${data.stores.length}`;
    const missing = Object.values(data.metrics).some(m => !m.complete && m !== data.metrics.margin_percent);
    const stamps = data.status.map(s => s.last_success).filter(Boolean).sort();
    const state = $('finance-state');
    state.classList.toggle('finance-warning', missing || data.stale);
    state.textContent = [data.empty_scope ? 'Нет доступных магазинов.' : missing ? 'Неполные данные: прочерк означает, что полный итог пока неизвестен.' : 'Все обязательные источники загружены.', data.preliminary ? 'Текущий месяц предварительный.' : '', data.stale ? 'Последнее обновление завершилось ошибкой. Показана последняя опубликованная версия.' : '', stamps.length ? `Последняя успешная загрузка: ${new Date(stamps.at(-1)).toLocaleString('ru-RU')}.` : 'Успешных загрузок ещё нет.'].filter(Boolean).join(' ');
    if (data.status.some(s=>s.running)) state.textContent += ' Идёт обновление; пока показан опубликованный результат.';
    const cards = ['seller_turnover','expenses','profit','margin_percent'];
    $('finance-cards').innerHTML = cards.map(key => `<article class="finance-card"><h3>${esc(data.metrics[key].label)}</h3>${metric(key,data.metrics[key])}</article>`).join('');
    $('finance-metrics').innerHTML = Object.entries(data.metrics).map(([key,value]) => `<div class="finance-metric-row"><span>${esc(value.label)}</span>${metric(key,value)}</div>`).join('');
    $('finance-daily').querySelector('tbody').innerHTML = data.daily.map(row => `<tr><td>${esc(row.day)}</td>${['seller_turnover','expenses','net_cost','profit','buyout_count'].map(key => `<td>${metric(key,row.metrics[key],row.day)}</td>`).join('')}</tr>`).join('');
    $('finance-sources').innerHTML = data.status.map(s => `<div class="finance-source"><strong>${esc(s.store_slug)} · ${esc(s.business_id)} · ${esc(labels[s.source] || s.source)}</strong><p>Последнее обновление: ${s.last_success ? esc(new Date(s.last_success).toLocaleString('ru-RU')) : 'не загружено'}</p>${s.errors.map(e => `<p class="finance-error">${esc(e.month)}: ${esc(e.message)}</p>`).join('')}${s.coverage.map(c => `<p>${esc(c.from)} — ${esc(c.to)} ${c.issues.map(esc).join('; ')}</p>`).join('')}</div>`).join('') || 'Нет настроенных финансовых подключений для периода.';
  }
  async function load() {
    const own = ++sequence;
    controller?.abort(); detailController?.abort(); detailSequence++;
    controller = new AbortController(); dialog.close();
    summary = null;
    $('finance-report').hidden = true;
    $('finance-message').className = '';
    $('finance-message').textContent = 'Загрузка отчёта…';
    query = new URLSearchParams(new FormData(filters)).toString();
    try {
      const data = await request(`${api}?${query}`, {signal:controller.signal});
      if (own !== sequence) return;
      summary = data; render(data); $('finance-message').textContent = '';
    } catch (error) { if (own === sequence && error.name !== 'AbortError') { $('finance-message').className = 'finance-error'; $('finance-message').textContent = error.message; } }
  }
  async function loadDetails() {
    if (!summary || !selection) return;
    const own = ++detailSequence;
    detailController?.abort(); detailController = new AbortController();
    const params = new URLSearchParams(query);
    for (const [key,value] of Object.entries({...selection, version:summary.version})) if (value) params.set(key,value);
    for (const [key,value] of new FormData($('finance-detail-filters'))) if (value) params.set(key,value);
    $('finance-detail-message').textContent = 'Загрузка операций…'; $('finance-detail-rows').replaceChildren();
    $('finance-prev').disabled = $('finance-next').disabled = true;
    try {
      const data = await request(`${api}/details?${params}`, {signal:detailController.signal});
      if (own !== detailSequence || !dialog.open) return;
      $('finance-detail-message').textContent = `Известная сумма: ${fmt(data.known_sum)} · Неизвестных сумм: ${data.unknown_count} · Операций: ${data.total_count}`;
      $('finance-detail-rows').innerHTML = data.rows.map(r => `<tr><td>${esc(r.day)}${r.correction ? '<small>Возврат / корректировка</small>' : ''}</td><td>${esc(r.store_slug)}<small>${esc(r.business_id)} / ${esc(r.campaign_id)}</small></td><td>${esc(labels[r.kind] || r.kind)}<small>${esc(labels[r.category] || r.category)}</small></td><td>${esc(r.order_id)}<small>${esc(r.article)}</small></td><td>${r.quantity?fmt(r.quantity):'—'}</td><td>${fmt(r.contribution)}<small>${selection.metric.endsWith('_count')?'шт.':esc(r.currency)} · ${esc(r.cost_origin)}</small>${r.issues.map(x=>`<small>${esc(x)}</small>`).join('')}</td><td>${esc(r.source_sheet)}<small>${esc(new Date(r.captured_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}))}</small></td></tr>`).join('');
      $('finance-detail-page').textContent = `Страница ${data.page} из ${Math.max(1,Math.ceil(data.total_count / data.page_size))}`;
      $('finance-prev').disabled = data.page <= 1; $('finance-next').disabled = data.page * data.page_size >= data.total_count;
    } catch (error) { if (own === detailSequence && error.name !== 'AbortError') $('finance-detail-message').textContent = error.message; }
  }
  filters.addEventListener('submit', e => {e.preventDefault();load();});
  $('finance-report').addEventListener('click', e => {
    const button = e.target.closest('[data-metric]'); if (!button || !summary) return;
    selection = {metric:button.dataset.metric, day:button.dataset.day, page:1};
    const value=selection.day?summary.daily.find(d=>d.day===selection.day).metrics[selection.metric]:summary.metrics[selection.metric];
    $('finance-detail-title').textContent = value.label + (selection.day ? ` · ${selection.day}` : '');
    $('finance-detail-quality').hidden=!value.reasons.length;
    $('finance-detail-reasons').innerHTML=value.reasons.map(reason=>`<li>${esc(reason)}</li>`).join('');
    $('finance-detail-filters').reset(); dialog.showModal(); loadDetails();
  });
  $('finance-detail-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {detailController?.abort();detailSequence++;});
  $('finance-prev').addEventListener('click', () => {selection.page--;loadDetails();}); $('finance-next').addEventListener('click', () => {selection.page++;loadDetails();});
  $('finance-detail-filters').addEventListener('submit', e => {e.preventDefault();selection.page=1;loadDetails();});

  async function adminAction(action) {
    const message = $('finance-admin-message'); message.className = ''; message.textContent = 'Выполняется…';
    try { await action(); } catch(error) {message.className='finance-error';message.textContent=error.message;}
  }
  function formData(form) { const data=Object.fromEntries(new FormData(form)); for (const field of ['effective_to']) if (field in data && !data[field]) data[field]=null; return data; }
  async function connections() {
    const data = await request(`${adminApi}/connections`);
    $('finance-connections').innerHTML = data.connections.map(c=>`<div class="finance-connection"><span>${esc(c.store_slug)} · Business ${esc(c.business_id)} · Campaign ${c.campaign_ids.map(esc).join(', ')}<br><small>${esc(c.effective_from)} — ${esc(c.effective_to || 'без окончания')} · ${c.active?'включено':'отключено'}</small></span><button type="button" data-check="${esc(c.id)}">Проверить финансовый доступ</button><button type="button" data-key="${esc(c.id)}">Заменить ключ</button>${c.active?`<button type="button" data-disable="${esc(c.id)}">Отключить</button>`:''}</div>`).join('') || '<p>Подключений пока нет.</p>';
  }
  async function runs() {
    const data=await request(`${adminApi}/runs`);
    $('finance-runs').innerHTML = `<p>${data.running?'Идёт финансовая загрузка.':'Активных заданий нет.'}</p>`+data.runs.map(r=>`<div class="finance-source">${esc(r.id)} · ${esc(r.status)} · ${esc(new Date(r.started_at).toLocaleString('ru-RU'))}${r.error?`<p class="finance-error">${esc(r.error)}</p>`:''}</div>`).join('');
  }
  if (!admin.hidden) {
    $('finance-connection-form').addEventListener('submit', e=>{e.preventDefault();adminAction(async()=>{const data=formData(e.target);data.business_id=Number(data.business_id);data.campaign_ids=data.campaign_ids.split(',').map(x=>Number(x.trim()));await request(`${adminApi}/connections`,{method:'POST',body:JSON.stringify(data)});e.target.elements.api_key.value='';await connections();$('finance-admin-message').textContent='Подключение сохранено. Загрузите период для проверки финансовых доступов.';});});
    $('finance-discover').addEventListener('click',()=>adminAction(async()=>{const f=$('finance-connection-form');const data=await request(`${adminApi}/discover`,{method:'POST',body:JSON.stringify({store_slug:f.elements.store_slug.value,api_key:f.elements.api_key.value})});$('finance-discovery').textContent=data.campaigns.map(c=>`Business ${c.business_id}: Campaign ${c.campaign_id} ${c.business_name || c.domain || ''}`).join(' · ');$('finance-admin-message').textContent='Выберите кабинет и кампании из списка. Финансовые права проверяются при первой загрузке.';}));
    $('finance-connections').addEventListener('click',e=>{
      const key=e.target.closest('[data-key]'),disable=e.target.closest('[data-disable]'),check=e.target.closest('[data-check]');
      if(key){keyConnection=key.dataset.key;$('finance-key-form').reset();$('finance-key-dialog').showModal();}
      else if(disable){const day=window.prompt('Последний день действия подключения (ГГГГ-ММ-ДД). История сохранится.');if(day)adminAction(async()=>{await request(`${adminApi}/connections/${disable.dataset.disable}/disable`,{method:'POST',body:JSON.stringify({effective_to:day})});await connections();$('finance-admin-message').textContent='Подключение отключено.';});}
      else if(check){adminAction(async()=>{const data=await request(`${adminApi}/connections/${check.dataset.check}/check`,{method:'POST'});$('finance-admin-message').textContent=`Проверка финансовых отчётов: задание ${data.run_id}. Результат появится в статусах заданий.`;await runs();});}
    });
    $('finance-key-cancel').addEventListener('click',()=>$('finance-key-dialog').close());
    $('finance-key-form').addEventListener('submit',e=>{e.preventDefault();const value=e.target.elements.api_key.value;e.target.reset();$('finance-key-dialog').close();adminAction(async()=>{await request(`${adminApi}/connections/${keyConnection}/key`,{method:'PUT',body:JSON.stringify({api_key:value})});$('finance-admin-message').textContent='Ключ заменён.';});});
    $('finance-load-unallocated').addEventListener('click',()=>adminAction(async()=>{const data=await request(`${adminApi}/unallocated`);$('finance-unallocated').innerHTML=data.rows.map(r=>`<div class="finance-source">Business ${esc(r.business_id)} · ${esc(r.day)} · ${esc(r.source_sheet)} · ${fmt(r.amount)} ₽</div>`).join('') || 'Нераспределённых операций нет.';$('finance-admin-message').textContent='Операции загружены.';}));
    $('finance-run-form').addEventListener('submit',e=>{e.preventDefault();adminAction(async()=>{const data=await request(`${adminApi}/runs`,{method:'POST',body:JSON.stringify(formData(e.target))});$('finance-admin-message').textContent=`Задание ${data.run_id} принято. После завершения обновите отчёт.`;await runs();});});
    $('finance-refresh-runs').addEventListener('click',()=>adminAction(async()=>{await runs();$('finance-admin-message').textContent='Статусы обновлены.';}));
    $('finance-cost-form').addEventListener('submit',e=>{e.preventDefault();adminAction(async()=>{const data=await request(`${adminApi}/costs`,{method:'POST',body:JSON.stringify(formData(e.target))});$('finance-admin-message').textContent=data.message;});});
    $('finance-load-costs').addEventListener('click',()=>adminAction(async()=>{const store=$('finance-cost-form').elements.store_slug.value;const data=await request(`${adminApi}/costs?store=${encodeURIComponent(store)}`);$('finance-costs').innerHTML=data.costs.map(c=>`<div class="finance-source">${esc(c.article)} · ${fmt(c.price)} ₽ · ${esc(c.effective_from)} — ${esc(c.effective_to || 'без окончания')}<p>${esc(c.origin)} · Автор ${esc(c.actor)} · ${esc(c.reason)}</p></div>`).join('') || 'История пуста.';$('finance-admin-message').textContent='История загружена.';}));
  }
  async function init() {
    try {
      const data=await request(`${api}/filters`);
      const options=data.stores.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
      filters.elements.store.insertAdjacentHTML('beforeend',options);
      filters.elements.date_from.value=data.date_from;filters.elements.date_to.value=data.date_to;
      filters.elements.date_from.max=filters.elements.date_to.max=data.today;
      if (!admin.hidden) {
        admin.querySelectorAll('[data-finance-stores]').forEach(s=>s.insertAdjacentHTML('beforeend',options));
        $('finance-run-form').elements.date_from.value=data.date_from;$('finance-run-form').elements.date_to.value=data.date_to;
        await connections();
      }
      await load();
    } catch(error) {$('finance-message').className='finance-error';$('finance-message').textContent=error.message;}
  }
  init();
})();
