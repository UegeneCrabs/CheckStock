(function () {
  'use strict';
  var from=document.querySelector('#from'), to=document.querySelector('#to');
  var store=document.querySelector('#store'), campaign=document.querySelector('#campaign');
  var refresh=document.querySelector('#refresh-rbe2'), error=document.querySelector('#error');
  var activeRun=null, preview=window.RBE2_PREVIEW_DATA, loadVersion=0;
  var format=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2});
  var definitions=[
    ['orders_turnover','Заказано, ₽'], ['cancelled_count','Отменено из заказанных, шт.'],
    ['cancelled_turnover','Отменено из заказанных, ₽'], ['detail_redeemed_count','Выкуплено, шт.'],
    ['detail_returned_count','Возвращено, шт.'], ['buyout_count','Выкупы минус возвраты, шт.'],
    ['detail_seller_sales','Выкуплено в цене продавца, ₽'], ['detail_seller_returns','Возвращено в цене продавца, ₽'],
    ['buyout_seller_turnover','Выкупы минус возвраты в цене продавца, ₽'],
    ['buyout_buyer_turnover','Выкупы минус возвраты в цене покупателя, ₽'],
    ['commission','Комиссия по заказам после зачёта баллов, ₽'],
    ['logistics','Логистика в составе заказов, ₽'], ['advertising','Продвижение в составе заказов, ₽'],
    ['other_direct_expenses','Иные услуги в составе заказов, ₽'],
    ['order_expenses','Всего услуг по заказам после зачёта баллов, ₽'],
    ['bank_receipt','Расчётная сумма к перечислению, ₽'],
    ['sold_cost_of_goods','Себестоимость выкупов минус возвраты, ₽'], ['marginal_profit','Маржинальная прибыль, ₽']
  ];
  function dateKey(d) { return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
  function dateText(d) { return d.split('-').reverse().join('.'); }
  function amount(v) { return v==null?'—':format.format(v); }
  function cell(text,tag) { var el=document.createElement(tag||'td'); el.textContent=text; return el; }
  async function json(url,options) {
    var response=await fetch(url,options), payload=await response.json();
    if(!response.ok) throw Error(payload.detail||'Запрос не выполнен');
    return payload;
  }
  function render(payload) {
    var body=document.querySelector('#metrics'); body.replaceChildren();
    definitions.forEach(function(pair) {
      var key=pair[0], row=document.createElement('tr'), label=cell(pair[1],'th');
      var value=cell(amount(payload.values[key])), state=(payload.quality||{})[key]||{};
      var status=document.createElement('td'), badge=document.createElement('span');
      value.className='rbe2-value'; if(key==='marginal_profit') row.className='total';
      badge.className='rbe2-status '+(!state.complete?'missing':state.estimated?'estimate':'complete');
      badge.textContent=!state.complete?'Не готово':state.estimated?'По заказам':'Загружено';
      badge.title=state.detail||'Исходные данные ещё не загружены'; status.append(badge);
      if(payload.values[key]!=null) {
        var copy=document.createElement('button'); copy.type='button'; copy.textContent='Копировать';
        copy.setAttribute('aria-label','Копировать '+pair[1]);
        copy.onclick=async function() {
          try { await navigator.clipboard.writeText(String(payload.values[key])); copy.textContent='Готово'; }
          catch(_) { error.textContent='Не удалось скопировать число.'; }
        };
        value.append(copy);
      }
      row.append(label,value,status); body.append(row);
    });
    var notes=document.querySelector('#notes'); notes.replaceChildren();
    (payload.notes||[]).forEach(function(text) { notes.append(cell(text,'li')); });
    var events=document.querySelector('#events'); events.replaceChildren();
    [['sales','Выкуп'],['returns','Возврат'],['cancellations','Отмена']].forEach(function(pair) {
      ((payload.events||{})[pair[0]]||[]).forEach(function(item) {
        var row=document.createElement('tr');
        [pair[1],item.store.toUpperCase(),dateText(item.date),item.order_id+(item.return_id?' / '+item.return_id:''),item.sku,amount(item.count),amount(item.seller_amount)].forEach(function(text) { row.append(cell(text)); });
        events.append(row);
      });
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
