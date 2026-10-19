(function () {
  'use strict';
  var from=document.querySelector('#from'), to=document.querySelector('#to');
  var store=document.querySelector('#store'), campaign=document.querySelector('#campaign');
  var refresh=document.querySelector('#refresh-rbe2'), error=document.querySelector('#error');
  var activeRun=null, preview=window.RBE2_PREVIEW_DATA, loadVersion=0;
  var format=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1});
  var copyIcon='<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg>';
  var definitions=[
    ['orders_turnover','ТО заказы из воронки'],
    ['buyout_seller_turnover','ТО выкупы-возвраты в цене селлера'],
    ['buyout_buyer_turnover','ТО выкупы-возвраты в цене покупателя'],
    ['buyout_count','К-во выкупов'],
    ['commission','Комиссия'], ['storage','Хранение'], ['logistics','Логистика'],
    ['other_direct_expenses','Иное (эквайринг + другие)'], ['advertising','Реклама'],
    ['bank_receipt','Приход на РС'], ['sold_cost_of_goods','ЗЦ проданных товаров'],
    ['marginal_profit','Маржинальная прибыль'],
    ['marginal_profit_percent','Маржинальная прибыль, % от ТО выкупов в цене покупателя']
  ];
  var descriptions={
    orders_turnover:'Стоимость заказов за выбранные дни по операционному API. Это не выручка: часть заказов будет отменена или не выкуплена.',
    cancelled_count:'Количество отменённых единиц из заказанных. Невыкупы и отмены учитываются отдельно от состоявшихся продаж.',
    cancelled_turnover:'Стоимость отменённых заказов по операционным данным Маркета. Не вычитается второй раз из уже состоявшихся выкупов.',
    detail_redeemed_count:'Количество выкупленных единиц по данным заказов на дату доставки покупателю. Это не число заказов.',
    detail_returned_count:'Количество подтверждённых возвращённых единиц в дневной выборке API невыкупов и возвратов. Заявки без подтверждённого возврата не уменьшают продажи.',
    buyout_count:'Выкупленные единицы минус подтверждённые возвраты. Возврат может относиться к другому дню, поэтому итог отдельного дня бывает отрицательным.',
    detail_seller_sales:'Стоимость выкупленных товаров в цене продавца по заказам, на дату доставки. Не зависит от наличия финансового отчёта за этот день.',
    detail_seller_returns:'Стоимость подтверждённых возвратов в цене продавца по исходным товарам заказа. Невыкуп без состоявшейся продажи сюда не входит.',
    buyout_seller_turnover:'Выкупы за вычетом возвратов в цене продавца. Это база операционного расчёта RBE 2, отличная от финансового отчёта RBE.',
    buyout_buyer_turnover:'Выкупы за вычетом возвратов в цене покупателя. Скидки Маркета могут делать эту сумму отличной от оборота в цене продавца.',
    commission:'Исходная комиссия Маркета до зачёта баллов за выкупленные товары, за вычетом комиссии по возвратам. При отсутствии необходимых данных показатель неполный.',
    storage:'Хранение товаров и хранение невыкупов и возвратов включено в «Иное (эквайринг + другие)». Здесь прочерк, чтобы не учитывать расход повторно.',
    logistics:'Стоимость логистических услуг Маркета по доступным начислениям за период. Отсутствующие начисления не считаются нулём.',
    advertising:'Продвижение и реклама по сохранённым начислениям. Общие расходы кабинета учитываются один раз, а не по каждому магазину повторно.',
    other_direct_expenses:'Эквайринг и прочие услуги Маркета, не отнесённые к комиссии, логистике и рекламе. Одно начисление не включается дважды.',
    order_expenses:'Сумма комиссии, логистики, рекламы и прочих прямых расходов Маркета. Если составляющая не загружена, итог не подтверждён.',
    revenue:'Оборот выкупов минус возвраты в цене продавца, затем минус комиссия и логистика. Это промежуточная операционная выручка.',
    bank_receipt:'Расчётная сумма после всех прямых расходов Маркета, а не фактическое поступление на банковский счёт.',
    sold_cost_of_goods:'Закупочная стоимость выкупленных товаров за вычетом возвратов по ценам соответствующего магазина. Если цены товара нет, итог неполный.',
    marginal_profit:'Расчётная сумма к перечислению минус себестоимость. Налоги и расходы вне Маркета здесь не вычтены; это прибыль до налогов.',
    marginal_profit_percent:'Маржинальная прибыль делится на сумму выкупов минус возвраты в цене покупателя и умножается на 100. При нулевом обороте процент не рассчитывается.'
  };
  var parts={
    orders_turnover:[['+','Заказы за вычетом отмен','detail_orders_remaining'],['+','Отменённая часть заказов','detail_orders_cancelled']],
    buyout_count:[['+','Выкуплено, шт.','detail_redeemed_count'],['−','Возвращено, шт.','detail_returned_count']],
    buyout_seller_turnover:[['+','Выкуплено в цене продавца','detail_seller_sales'],['−','Возвращено в цене продавца','detail_seller_returns']],
    buyout_buyer_turnover:[['+','Выкуплено в цене покупателя','detail_buyer_sales'],['−','Возвращено в цене покупателя','detail_buyer_returns']],
    order_expenses:[['+','Комиссия','commission'],['+','Логистика','logistics'],['+','Реклама','advertising'],['+','Прочие услуги','other_direct_expenses']],
    revenue:[['+','Выкупы минус возвраты в цене продавца','buyout_seller_turnover'],['−','Комиссия','commission'],['−','Логистика','logistics']],
    bank_receipt:[['+','Выручка после комиссии и логистики','revenue'],['−','Реклама','advertising'],['−','Прочие услуги','other_direct_expenses']],
    marginal_profit:[['+','Расчётная сумма к перечислению','bank_receipt'],['−','Себестоимость','sold_cost_of_goods']],
    marginal_profit_percent:[['','Маржинальная прибыль','marginal_profit'],['÷','ТО выкупов в цене покупателя','buyout_buyer_turnover']]
  };
  function dateKey(d) { return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
  function dateText(d) { return d.split('-').reverse().join('.'); }
  function amount(v) { return v==null?'—':format.format(v); }
  function cell(text,tag) { var el=document.createElement(tag||'td'); el.textContent=text; return el; }
  async function copyNumber(value) {
    if(navigator.clipboard&&navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(value); return; } catch(_) { /* HTTP-compatible fallback. */ }
    }
    var input=document.createElement('textarea'), focused=document.activeElement;
    input.value=value; input.readOnly=true; input.style.cssText='position:fixed;left:-9999px;top:0';
    document.body.append(input);
    try { input.select(); input.setSelectionRange(0,value.length); if(!document.execCommand('copy')) throw Error('Не удалось скопировать число'); }
    finally { input.remove(); if(focused&&focused.focus) focused.focus({preventScroll:true}); }
  }
  function renderValue(target,value,percent) {
    var text=amount(value)+(percent&&value!=null?'%':''), wrap=document.createElement('div'), number=document.createElement('span');
    wrap.className='report-value'; number.textContent=text; wrap.append(number);
    if(typeof value==='number'&&Number.isFinite(value)) {
      var copy=document.createElement('button'); copy.type='button'; copy.className='copy-number'; copy.innerHTML=copyIcon;
      copy.title='Копировать число'; copy.setAttribute('aria-label','Копировать число '+text);
      copy.onclick=async function() {
        var notice=document.querySelector('#copy-status'); copy.disabled=true; notice.textContent='';
        try { await copyNumber(text.replace(/[\s%]/g,'')); copy.textContent='✓'; copy.classList.add('copied'); copy.title='Скопировано'; notice.textContent='Число скопировано'; }
        catch(_) { copy.title='Не удалось скопировать. Попробуйте ещё раз'; notice.textContent=copy.title; }
        finally { copy.disabled=false; window.setTimeout(function(){copy.innerHTML=copyIcon;copy.classList.remove('copied');copy.title='Копировать число';},1500); }
      };
      wrap.append(copy);
    }
    target.replaceChildren(wrap);
  }
  function renderBreakdown(body,key,value,state,payload) {
    var values=payload.values, known=((payload.expenses||{}).known_subtotals||{})[key];
    var panel=document.createElement('tr'), content=document.createElement('td'), description=cell(descriptions[key],'p');
    panel.id='breakdown-'+key; panel.className='breakdown-row'; panel.hidden=true; content.colSpan=3;
    description.className='breakdown-description'; content.append(description);
    if(!state.complete) {
      var note=cell(state.detail||'Данные за выбранный период неполные. Итог может измениться после загрузки.','p');
      note.className='breakdown-note'; content.append(note);
    }
    var list=document.createElement('dl'); list.className='breakdown-list';
    var sourceParts=(parts[key]||[]).slice();
    if(['commission','logistics','advertising','other_direct_expenses'].includes(key)) {
      var grouped=new Map();
      ((payload.expenses||{}).breakdown||[]).filter(function(item){return item.group===key;}).forEach(function(item){
        var label=String(item.store||'').toUpperCase()+' · '+String(item.sheet||'начисление');
        grouped.set(label,(grouped.get(label)||0)+Number(item.amount||0));
      });
      grouped.forEach(function(sum,label){sourceParts.push(['',label,null,sum]);});
    }
    sourceParts.forEach(function(part) {
      var item=document.createElement('div'), label=cell(part[0]+' '+part[1],'dt'), number=document.createElement('dd');
      var partValue=part.length>3?part[3]:values[part[2]];
      if(part[0]==='−'&&typeof partValue==='number'&&partValue!==0) number.dataset.effect=partValue>0?'negative':'positive';
      else if(part[0]==='+'&&typeof partValue==='number'&&partValue!==0) number.dataset.effect=partValue>0?'positive':'negative';
      renderValue(number,partValue); item.append(label,number); list.append(item);
    });
    var total=document.createElement('div'), totalLabel=cell(value==null&&known>0?'= Подтверждённая часть':'= Итог','dt'), totalValue=document.createElement('dd');
    total.className='breakdown-total'; renderValue(totalValue,value==null&&known>0?known:value,key==='marginal_profit_percent'); total.append(totalLabel,totalValue); list.append(total);
    content.append(list); panel.append(content); body.append(panel);
    return panel;
  }
  async function json(url,options) {
    var response=await fetch(url,options), payload=await response.json();
    if(!response.ok) throw Error(payload.detail||'Запрос не выполнен');
    return payload;
  }
  function render(payload) {
    var body=document.querySelector('#metrics'); body.replaceChildren();
    definitions.forEach(function(pair) {
      var key=pair[0], row=document.createElement('tr'), label=document.createElement('th');
      var metric=document.createElement('span'), toggle=document.createElement('button'), name=cell(pair[1],'span'), help=cell('ⓘ','span');
      metric.className='metric'; toggle.type='button'; toggle.className='metric-toggle'; toggle.textContent='▸';
      toggle.setAttribute('aria-label','Расчёт: '+pair[1]); toggle.setAttribute('aria-expanded','false'); toggle.setAttribute('aria-controls','breakdown-'+key);
      help.className='metric-help'; help.tabIndex=0; help.dataset.tip=descriptions[key]; metric.append(toggle,name,help); label.append(metric);
      var value=document.createElement('td'), state=(payload.quality||{})[key]||{};
      value.dataset.key=key; renderValue(value,payload.values[key],key==='marginal_profit_percent');
      var known=((payload.expenses||{}).known_subtotals||{})[key];
      if(payload.values[key]==null && known>0) {
        renderValue(value,known);
        var partial=cell('подтверждённая часть','small'); partial.className='rbe2-partial'; value.append(partial);
      }
      var status=document.createElement('td'), badge=document.createElement('span');
      status.className='status-cell'; if(key==='marginal_profit'||key==='marginal_profit_percent') row.className='total';
      badge.className='status '+(state.complete?'status--complete':'status--incomplete');
      badge.textContent=state.complete?'Полные':'Неполные';
      badge.title=state.detail||'Исходные данные ещё не загружены'; status.append(badge);
      row.append(label,value,status); body.append(row);
      var panel=renderBreakdown(body,key,payload.values[key],state,payload);
      toggle.onclick=function(){panel.hidden=!panel.hidden;toggle.textContent=panel.hidden?'▸':'▾';toggle.setAttribute('aria-expanded',String(!panel.hidden));};
    });
    document.querySelector('#period').textContent=dateText(from.value)+' — '+dateText(to.value);
    document.querySelector('#range').textContent=dateText(from.value)+' — '+dateText(to.value);
    document.querySelector('#last-loaded').textContent=payload.last_loaded_at?'Загружено: '+new Date(payload.last_loaded_at).toLocaleString('ru-RU'):'Данные ещё не загружены';
    var selected=campaign.value; campaign.replaceChildren(new Option('Все модели и склады',''));
    (payload.campaigns||[]).forEach(function(c) { campaign.append(new Option(c.store_slug.toUpperCase()+' · '+(c.model==='FBY'?'FBO':c.model)+' · '+c.name,String(c.id))); });
    campaign.value=selected;
    refresh.hidden=preview?true:!payload.can_sync;
    refresh.disabled=Boolean(payload.sync_running||activeRun);
    refresh.textContent=refresh.disabled?'Загрузка…':'Загрузить по API';
  }
  async function load() {
    error.textContent=''; if(preview) { render(preview); return; }
    var version=++loadVersion;
    var params=new URLSearchParams({date_from:from.value,date_to:to.value});
    if(store.value) params.set('store_slug',store.value);
    if(campaign.value) params.set('campaign_id',campaign.value);
    try { var payload=await json('/api/rbe2/report?'+params); if(version===loadVersion)render(payload); }
    catch(e) { if(version===loadVersion)error.textContent=e.message; }
    try {
      var queue=await json('/api/rbe2/sync-status'), label=document.querySelector('#auto-status');
      if(version===loadVersion) label.textContent=queue.automatic?'Автозагрузка включена · в очереди '+queue.pending_jobs+' заданий':'Автозагрузка выключена';
    } catch(_) { /* The saved report remains usable if queue status is unavailable. */ }
  }
  async function poll(id) {
    try {
      var state=await json('/api/rbe2/sync/'+id);
      if(['queued','running'].includes(state.status)) { window.setTimeout(function(){poll(id);},3000); return; }
      activeRun=null; sessionStorage.removeItem('rbe2-sync-run'); await load();
      error.textContent=state.status==='completed'?'Операционные данные загружены.':state.detail;
    } catch(e) { error.textContent=e.message; window.setTimeout(function(){poll(id);},10000); }
  }
  refresh.onclick=async function() {
    refresh.disabled=true;
    try {
      var body={date_from:from.value,date_to:to.value}; if(store.value) body.store_slug=store.value;
      var state=await json('/api/rbe2/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      activeRun=state.run_id; sessionStorage.setItem('rbe2-sync-run',String(activeRun));
      error.textContent=state.detail; poll(activeRun);
    } catch(e) { error.textContent=e.message; refresh.disabled=false; }
  };
  document.querySelector('#apply').onclick=load;
  store.onchange=function() { campaign.value=''; load(); }; campaign.onchange=load;
  var end=new Date(); end.setDate(end.getDate()-1); var start=new Date(end); start.setDate(end.getDate()-6);
  from.value=preview?preview.date_from:dateKey(start); to.value=preview?preview.date_to:dateKey(end);
  if(preview) {
    store.append(new Option('TRIS','tris')); store.value='tris'; store.disabled=true; campaign.disabled=true;
    document.querySelector('#range').disabled=true; document.querySelector('#apply').disabled=true; render(preview);
  } else {
    json('/api/stores').then(function(p) {
      (p.stores||[]).forEach(function(s) { store.append(new Option(s.toUpperCase(),s)); }); return load();
    }).then(function() {
      var saved=sessionStorage.getItem('rbe2-sync-run'); if(saved) { activeRun=Number(saved); poll(activeRun); }
    }).catch(function(e) { error.textContent=e.message; });
  }
}());
