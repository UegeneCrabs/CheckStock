(() => {
  'use strict';
  const el = id => document.getElementById('sc-' + id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = v => v == null ? '—' : (Math.abs(Number(v))<0.5?0:Number(v)).toLocaleString('ru-RU',{maximumFractionDigits:0});
  const delta = v => v == null ? '—' : (v>=0.5?'+':'')+fmt(v);
  const deltaClass = value => typeof value !== 'number' || !Number.isFinite(value) || value === 0 ? '' : value < 0 ? 'sc-delta-negative' : 'sc-delta-positive';
  const fields = [['retail','Цена'],['purchase','Закупка'],['fulfillment','ФФ'],['advertising','Реклама'],['acquiring','Эквайринг'],['team','Комиссия компании'],['vat','НДС'],['tax','УСН / ОСНО'],['commission','Комиссия WB'],['logistics','Логистика'],['storage_total','Хранение'],['total_cost','Все расходы']];
  const precise = v => v == null ? '—' : Number(v).toLocaleString('ru-RU',{maximumFractionDigits:6});
  function valueHelp(r,key,group='profit') {
    const p=r[group]||{}, value=p[key], period=`${r.period_from} — ${r.period_to}`;
    let formula='', calculation='', source='БД калькулятора; '+r.store_name+', артикул '+r.article+'.';
    if(key==='logistics' && group==='profit') {
      const c=r.logistics_calculation;
      formula='Тариф склада = первый литр + max(объём − 1, 0) × доп. литр.\nИтог = (Σ(тариф склада × количество) ÷ Σ количества) × ИЛ.';
      if(c) {
        calculation=c.terms.map(t=>`${t.warehouse}${t.tariff && t.tariff!==t.warehouse?' → '+t.tariff:''}:\n${precise(t.base)} + max(${precise(t.volume)} − 1, 0) × ${precise(t.extra)} = ${precise(t.rate)} ₽; вес ${precise(t.quantity)}.`).join('\n\n');
        calculation+=`\n\nТуда: (${precise(c.weighted_sum)} ÷ ${precise(c.weight)}) × ${precise(c.localization_index)} = ${fmt(r.logistics)} ₽.`;
        source=`WB /api/v1/tariffs/box, дата ${c.tariff_date}: boxDelivery${r.scheme==='FBS'?'Marketplace':''}Base/Liter.\n`+(c.basis==='stock'?`Веса: сумма barcodesCount за ${period}; объём: volume. Источник: /api/v1/paid_storage. Это сумма дневных остатков, а не число уникальных штук.`:`Веса: quantity продаж из /api/finance/v1/sales-reports/detailed за ${period}. Объём: length × width × height / 1000 из /content/v2/get/cards/list.`);
      } else calculation='Подробные исходные ставки отсутствуют в сохранённом снимке.';
      source+='\nИЛ = '+precise(c?.localization_index)+' — задан по умолчанию для отчёта, не получен из API WB.';
      const ret=r.logistics_returns||{};
      formula+='\nОбратно = 46 + max(объём − 1, 0) × 14.\np = выкуп ÷ 100; повторные поездки = (1 − p) ÷ p.\nНа выкупленную штуку = туда + (туда + обратно) × (1 − p) ÷ p.';
      calculation+=`\n\nОбратно: 46 + max(${precise(ret.volume)} − 1, 0) × 14 = ${fmt(ret.reverse)} ₽.\nВыкуп: ${fmt(ret.buyout_percent)}%. Повторные поездки: ${precise(ret.retries)}.\nПервая доставка: ${fmt(ret.forward)} ₽.\nПовторные отправки: ${fmt(ret.extra_forward)} ₽.\nВозвраты: ${fmt(ret.reverse_per_buyout)} ₽.\nИтого: ${fmt(value)} ₽/выкупленную шт.\n${(ret.missing||[]).join('; ')}`;
      source+='\nОбратная доставка: тариф по умолчанию 46/14 ₽, без коэффициента склада и ИЛ, не из API. Объём — габариты карточки WB в БД. До 1 л применяется минимум 46 ₽.\nПроцент выкупа — details.buyout_percent из БД калькулятора. Расчётная оценка повторных поездок; без приёмки и дополнительных услуг.';
    } else if(key==='commission' && group==='profit') {
      formula='Цена до СПП × ставка категории ÷ 100.';
      calculation=`${fmt(p.retail)} × ${precise(r.commission_rate)}% = ${fmt(value)} ₽/шт.`;
      source=`Цена: БД калькулятора, ${r.calculator_date||'дата неизвестна'}.\nСтавка: WB /api/v1/tariffs/commission, ${r.commission_date}; поле ${r.scheme==='FBS'?'kgvpMarketplace':'paidStorageKgvp'}. Предмет: ${r.subject}; схема: ${r.scheme}. Ставка текущая на дату загрузки.`;
    } else if(key==='storage_total' && group==='profit') {
      formula='Сумма хранения до скидок ИУ за неделю ÷ проданные штуки.';
      calculation=`${precise(r.storage)} ÷ ${precise(r.sales)} = ${fmt(value)} ₽/шт.`;
      if(value==null) calculation=(r.sales>0?'Нет подтверждённых базовых начислений хранения WB.':'Нет продаж за неделю — расходы нельзя разделить на проданные штуки.')+`\nХранение за неделю: ${fmt(r.storage)} ₽; средняя ставка: ${precise(r.storage_unit_day)} ₽/шт./день.`;
      if(r.storage_calculation?.basis==='estimate_90_days') {
        formula='Оценка для товара без продаж: средняя стоимость хранения одной штуки за день × 90 дней.';
        calculation=`${precise(r.storage_unit_day)} × 90 = ${fmt(value)} ₽/шт.`;
      }
      source=`WB /api/v1/paid_storage: сумма warehousePrice; строки скидок исключены.\nЗнаменатель: quantity операций «Продажа» из /api/finance/v1/sales-reports/detailed, ${period}, схема ${r.scheme}. Возвраты из знаменателя не вычитаются.`;
      if(r.scheme==='FBS') {formula='Хранение на складе WB для FBS не начисляется в этой модели.';calculation='0 ₽/шт. — правило модели, не подтверждение отсутствия других складских расходов.';}
    } else if(key==='margin') {
      formula='Цена до СПП − сумма расходов на одну штуку.';
      calculation=`${fmt(p.retail)} − ${fmt(p.total_cost)} = ${fmt(value)} ₽/шт.`;
    } else if(key==='roi') {
      formula='Чистая прибыль ÷ закупка × 100%.';
      calculation=`${fmt(p.margin)} ÷ ${fmt(p.purchase)} × 100% = ${fmt(value)}%.`;
    } else if(group==='calculator_turnover') {
      formula='Сумма заказов − сумма отмен за период.';
      calculation=`Оборот: ${fmt(value)} ₽. ${p.coverage&&!p.coverage.complete?'Период неполный.':''}`;
      source=`БД юнит-экономики, wb_funnel_daily_orders (orders_amount − cancel_amount); WB sales-funnel/products, ${period}. Оборот общий для всех схем одного артикула.`;
    } else if(key==='total_cost') {
      formula='Закупка + ФФ + реклама + эквайринг + комиссия компании + НДС + налог + комиссия WB + логистика + хранение.';
      calculation=fields.filter(([k])=>!['retail','total_cost'].includes(k)).map(([k,n])=>`${n}: ${fmt(p[k])} ₽`).join('\n')+`\nИтого: ${fmt(value)} ₽/шт.`;
    } else if(['acquiring','team','vat','tax'].includes(key)) {
      const inputs=r.calculation_inputs||{}, client=inputs.client_price??p.retail;
      if(key==='acquiring'||key==='team') {
        const rate=inputs[key==='team'?'team_commission_percent':'acquiring'];
        formula='Цена до СПП × ставка ÷ 100.';
        calculation=`${fmt(p.retail)} × ${precise(rate)} ÷ 100 = ${fmt(value)} ₽/шт.`;
      } else if(key==='vat') {
        formula='Цена с СПП × ставка НДС ÷ (100 + ставка НДС).';
        calculation=`${fmt(client)} × ${precise(inputs.vat_percent)} ÷ (100 + ${precise(inputs.vat_percent)}) = ${fmt(value)} ₽/шт.`;
      } else {
        const osno=inputs.tax_system==='osno',rate=inputs[osno?'osno_percent':'usn_percent'];
        formula=osno?'Цена с СПП × ставка ОСНО ÷ 100.':'(Цена с СПП − НДС) × ставка УСН ÷ 100.';
        calculation=`${osno?fmt(client):'('+fmt(client)+' − '+fmt(p.vat)+')'} × ${precise(rate)} ÷ 100 = ${fmt(value)} ₽/шт.`;
      }
      source+=' Ставки: сохранённые настройки кабинета / товара.';
      if(inputs.client_price==null && ['vat','tax'].includes(key))source+=' В калькуляторе при отсутствии цены с СПП используется цена до СПП.';
    } else {
      formula=fieldHelp[key]||'Значение из калькулятора с ИУ.';
      calculation=`На одну штуку: ${fmt(value)} ₽. Дата цены: ${r.calculator_date||'нет данных'}.`;
    }
    if(group==='delta') {
      formula=key==='margin'?'Прибыль без ИУ − прибыль с ИУ.':'ROI без ИУ − ROI с ИУ.';
      calculation=`${fmt(r.profit?.[key])} − ${fmt(r.current_profit?.[key])} = ${delta(value)} ${key==='margin'?'₽/шт.':'п.п.'}`;
      source='Оба сценария расчёта этой строки.';
    }
    if(r.storage_calculation?.warning && ['profit','delta'].includes(group)) {
      source+='\n\n'+r.storage_calculation.warning;
      if(key==='storage_total' && group==='profit'){formula='При отсутствии начислений API принимаем хранение за 0 ₽.';calculation='0 ₽ — условное значение, а не подтверждённое бесплатное хранение.';}
    }
    return `Формула\n${formula}\n\nРасчёт\n${calculation}\n\nИсточник данных\n${source}`+(value==null?'\n\nНе рассчитано: '+(p.missing?.join('; ')||r.missing?.join('; ')||'не хватает исходных данных'):'')+'\n\nПромежуточные значения показаны до 6 знаков; результат — до целого числа.';
  }
  function numericCell(r,key,group='profit',cls='') {
    const value=r[group]?.[key];
    const assumedStorage=r.storage_calculation?.warning && ((group==='profit' && ['storage_total','total_cost','margin','roi'].includes(key)) || group==='delta');
    if(['profit','current_profit'].includes(group)&&['margin','roi'].includes(key)&&r[group]?.margin<0) cls+=' sc-loss';
    return `<td class="${cls}"><span class="sc-value" tabindex="0" data-help="${esc(valueHelp(r,key,group))}">${group==='delta'?delta(value):fmt(value)}${assumedStorage&&value!=null?' <sup class="sc-assumption">†</sup>':''}${group==='calculator_turnover'&&value!=null&&r[group]?.coverage&&!r[group].coverage.complete?' *':''}</span></td>`;
  }
  const fieldHelp = {
    retail:'Цена продавца до СПП из БД калькулятора.',
    purchase:'Закупка за 1 шт. из таблицы себестоимости, сохранённая в БД.',
    fulfillment:'Фулфилмент за 1 шт. из БД калькулятора.',
    advertising:'Реклама ÷ заказы ÷ доля выкупа. Данные калькулятора за период.',
    acquiring:'Цена × ставка эквайринга. Ставка — из настроек кабинета.',
    team:'Цена × ставка компании. Ставка — из данных товара или кабинета.',
    vat:'Цена с СПП × ставка НДС ÷ (100 + ставка НДС). Настройки калькулятора.',
    tax:'УСН: (цена с СПП − НДС) × ставка. ОСНО: цена с СПП × ставка.',
    commission:'Цена × стандартная ставка WB для категории и схемы продажи.',
    logistics:'Доставка туда, возвраты и повторные отправки на одну выкупленную штуку. Обратно — тариф по умолчанию 46/14 ₽.',
    storage_total:'Хранение недели ÷ продажи; без продаж — дневная ставка × 90 дней (оценка).',
    total_cost:'Сумма всех расходов на одну штуку.'
  };
  const tooltip=document.createElement('div');
  tooltip.id='sc-column-tooltip';tooltip.className='sc-column-tooltip';tooltip.role='tooltip';tooltip.hidden=true;
  document.body.appendChild(tooltip);
  let helpTarget=null;
  let hideTimer;
  function hideHelp(){clearTimeout(hideTimer);tooltip.hidden=true;if(helpTarget)helpTarget.removeAttribute('aria-describedby');helpTarget=null;}
  function showHelp(button){
    hideHelp();helpTarget=button;tooltip.textContent=button.dataset.help;tooltip.hidden=false;
    button.setAttribute('aria-describedby',tooltip.id);
    const rect=button.getBoundingClientRect();
    tooltip.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-tooltip.offsetWidth-8))+'px';
    tooltip.style.top=(rect.bottom+tooltip.offsetHeight+8<window.innerHeight?rect.bottom+6:Math.max(8,rect.top-tooltip.offsetHeight-6))+'px';
  }
  el('table').addEventListener('pointerover',e=>{const b=e.target.closest('.sc-help, .sc-value');if(b)showHelp(b);});
  el('table').addEventListener('pointerout',e=>{if(e.target.closest('.sc-help, .sc-value'))hideTimer=setTimeout(hideHelp,180);});
  tooltip.addEventListener('pointerenter',()=>clearTimeout(hideTimer));
  tooltip.addEventListener('pointerleave',hideHelp);
  el('table').addEventListener('focusin',e=>{const b=e.target.closest('.sc-help, .sc-value');if(b)showHelp(b);});
  el('table').addEventListener('focusout',hideHelp);
  document.addEventListener('keydown',e=>{if(e.key==='Escape')hideHelp();});
  window.addEventListener('resize',hideHelp);document.addEventListener('scroll',e=>{if(e.target!==tooltip)hideHelp();},true);
  let exporting=false;
  let stores=[], rows=[], page=1, expanded=false, sortKey='calculator_turnover.value', sortAscending=false;
  function filterOptions(id, values, allLabel, emptyLabel, hasEmpty) {
    const select=el(id), previous=select.value;
    const options=[...new Set(values.filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru'));
    select.innerHTML=`<option value="">${allLabel}</option>`+options.map(v=>`<option value="${esc('value:'+v)}">${esc(v)}</option>`).join('')+(hasEmpty?`<option value="empty">${emptyLabel}</option>`:'');
    select.value=[...select.options].some(o=>o.value===previous)?previous:'';
  }
  function render() {
    hideHelp();
    const allStores=el('store').value==='';
    const selected=stores.filter(s=>s.report&&(allStores||s.slug===el('store').value));
    const sourceRows=selected.flatMap(s=>s.report.rows.filter(r=>r.scheme!=='Не определена').map(r=>({...r,store_slug:s.slug,store_name:s.name,period_from:s.report.date_from,period_to:s.report.date_to,commission_date:s.report.commission_date})));
    const subjectOf=r=>String(r.subject||'').trim();
    filterOptions('subject',sourceRows.map(subjectOf),'Все предметы','Без предмета',sourceRows.some(r=>!subjectOf(r)));
    filterOptions('manager',sourceRows.flatMap(r=>r.managers||[]),'Все менеджеры','Без менеджера',sourceRows.some(r=>!r.managers?.length));
    const query=el('search').value.trim().toLowerCase(), scheme=el('scheme').value, activity=el('activity').value;
    const subject=el('subject').value, manager=el('manager').value;
    rows=sourceRows.filter(r=>(!query||(r.article+' '+r.vendor+' '+r.name).toLowerCase().includes(query))&&(!scheme||r.scheme===scheme)&&(activity!=='sales'||r.sales>0)&&(activity!=='missing'||!r.profit?.complete||!r.current_profit?.complete));
    rows=rows.filter(r=>(!subject||(subject==='empty'?!subjectOf(r):subjectOf(r)===subject.slice(6)))&&(!manager||(manager==='empty'?!r.managers?.length:(r.managers||[]).includes(manager.slice(6)))));
    if(sortKey){
      const [group,field]=sortKey.split('.');
      rows.sort((a,b)=>{
        const av=a[group]?.[field],bv=b[group]?.[field];
        const aKnown=typeof av==='number'&&Number.isFinite(av), bKnown=typeof bv==='number'&&Number.isFinite(bv);
        if(!aKnown||!bKnown)return aKnown?-1:bKnown?1:0;
        return sortAscending?av-bv:bv-av;
      });
    }
    const pages=Math.max(1,Math.ceil(rows.length/50)); page=Math.min(page,pages);
    const metricCount=7+(expanded?fields.length:0);
    let columns=el('table').querySelector('colgroup');
    if(!columns){columns=document.createElement('colgroup');el('table').prepend(columns);}
    columns.innerHTML=`<col style="width:40px"><col style="width:220px">${'<col class="sc-metric-column">'.repeat(metricCount)}<col style="width:100px">`;
    el('table').style.setProperty('--sc-metric-width',`calc((100% - 360px) / ${metricCount})`);
    el('table').style.minWidth=(360+metricCount*112)+'px';
    el('table').querySelector('thead').innerHTML=`<tr><th rowspan="2">Фото</th><th rowspan="2">Артикул</th><th rowspan="2" data-sort="calculator_turnover.value">ТО, ₽</th>${expanded?`<th colspan="${fields.length}" class="sc-group">Расходы без ИУ, ₽/шт.</th>`:''}<th colspan="2" class="sc-group sc-without"><button id="sc-toggle" aria-controls="sc-table" aria-expanded="${expanded}" aria-label="${expanded?'Скрыть':'Показать'} подробности расчёта" title="${expanded?'Скрыть':'Показать'} подробности">${expanded?'−':'+'}</button> Без ИУ</th><th colspan="2" class="sc-group sc-current">С ИУ · калькулятор</th><th colspan="2" class="sc-group">Разница · без ИУ − с ИУ</th><th rowspan="2"></th></tr><tr>${expanded?fields.map(([,name])=>`<th>${name}</th>`).join(''):''}<th data-sort="profit.margin">Чистая прибыль, ₽</th><th data-sort="profit.roi">ROI, %</th><th data-sort="current_profit.margin">Чистая прибыль, ₽</th><th data-sort="current_profit.roi">ROI, %</th><th data-sort="delta.margin">Разница прибыли, ₽</th><th data-sort="delta.roi">Разница ROI, п.п.</th></tr>`;
    el('table').querySelectorAll('th[data-sort]').forEach(th=>{
      const active=th.dataset.sort===sortKey;
      th.setAttribute('aria-sort',active?(sortAscending?'ascending':'descending'):'none');
      const button=document.createElement('button');button.type='button';button.className='sc-sort';
      button.textContent=th.textContent+(active?(sortAscending?' ↑':' ↓'):' ↕');
      button.title=active&&sortAscending?'Сортировать по убыванию':'Сортировать по возрастанию';
      th.textContent='';th.appendChild(button);
    });
    const marginHelp='Цена − все расходы на одну штуку.';
    const roiHelp='Чистая прибыль ÷ закупка × 100%.';
    const hints=[
      'Фото из карточки товара.',
      'Артикул WB и схема продажи.',
      'Оборот из юнит-экономики: заказы − отмены за период. Общий для FBW/FBS; * — неполный период.',
      ...(expanded?['Расходы без индивидуальных условий на одну штуку.']:[]),
      'Комиссия, логистика и хранение по стандартным условиям WB.',
      'Данные калькулятора с индивидуальными условиями.',
      'Без ИУ минус с ИУ.',
      'Сравнение расходов и причины пропусков.',
      ...(expanded?fields.map(([key])=>fieldHelp[key]):[]),
      'Без ИУ: '+marginHelp,'Без ИУ: '+roiHelp,'С ИУ: чистая прибыль из калькулятора.','С ИУ: '+roiHelp,
      'Прибыль без ИУ − прибыль с ИУ.',
      'ROI без ИУ − ROI с ИУ, в процентных пунктах.'
    ];
    el('table').querySelectorAll('thead th').forEach((th,i)=>{
      if(!th.textContent.trim())th.textContent='Детали';
      if(i<2 || (th.classList.contains('sc-group') && !th.textContent.includes('Расходы без ИУ')))return;
      const text=hints[i];
      const label=th.textContent.replace(/[+−]/g,'').trim();
      const button=document.createElement('button');button.type='button';button.className='sc-help';button.textContent='?';
      button.dataset.help=text;button.setAttribute('aria-label','О показателе: '+label);
      const line=document.createElement('div');line.className='sc-help-line';line.appendChild(button);th.prepend(line);
    });
    el('table').querySelector('tbody').innerHTML=rows.slice((page-1)*50,page*50).map((r,i)=>{
      const p=r.profit||{}, c=r.current_profit||{}, d=r.delta||{}, turnover=r.calculator_turnover||{};
      const partial=turnover.coverage && !turnover.coverage.complete;
      const url=/^https?:\/\//i.test(r.image_url||'')?r.image_url:'';
      return `<tr><td class="sc-photo">${url?`<img src="${esc(url)}" alt="${esc(r.name)}" loading="lazy" referrerpolicy="no-referrer">`:'—'}</td><td class="sc-article" title="${esc(r.name)}"><strong>${esc(r.article)}</strong><small>${allStores?esc(r.store_name)+' · ':''}${esc(r.scheme)}</small></td>${numericCell(r,'value','calculator_turnover')}${expanded?fields.map(([key])=>numericCell(r,key)).join(''):''}${numericCell(r,'margin','profit','sc-without')}${numericCell(r,'roi','profit','sc-without')}${numericCell(r,'margin','current_profit','sc-current')}${numericCell(r,'roi','current_profit','sc-current')}${numericCell(r,'margin','delta',deltaClass(d.margin))}${numericCell(r,'roi','delta',deltaClass(d.roi))}<td><button data-row="${(page-1)*50+i}">${p.complete&&c.complete?'Расчёт':'Нет данных'}</button></td></tr>`;
    }).join('')||`<tr><td colspan="${expanded?22:10}">Нет товаров по выбранным условиям</td></tr>`;
    el('page').textContent=`${page} / ${pages} · ${rows.length} позиций`;
    el('export').disabled=exporting||rows.length===0;
    el('prev').disabled=page===1;el('next').disabled=page===pages;
  }
  el('export').onclick=async()=>{
    if(exporting)return;
    exporting=true;
    const button=el('export');
    button.disabled=true;button.textContent='Подготовка…';
    try {
      const response=await fetch('/api/unit-economics-1c/reports/standard-costs.xlsx',{
        method:'POST',signal:AbortSignal.timeout(120000),headers:{'Content-Type':'application/json'},
        body:JSON.stringify({rows:rows.map(r=>({store_slug:r.store_slug,article:String(r.article),scheme:r.scheme,period_from:r.period_from,period_to:r.period_to}))})
      });
      if(!response.ok){
        const error=await response.json().catch(()=>({}));
        throw new Error(typeof error.detail==='string'?error.detail:'Не удалось выгрузить Excel. Повторите позже.');
      }
      if(!response.headers.get('content-type')?.includes('spreadsheetml'))throw new Error('Сессия истекла. Войдите в сайт и обновите отчёт.');
      const url=URL.createObjectURL(await response.blob()), link=document.createElement('a');
      link.href=url;link.download='wb-conditions.xlsx';document.body.appendChild(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(url),60000);
    } catch(error){el('error').hidden=false;el('error').textContent=error.name==='TimeoutError'?'Сервер не ответил вовремя. Повторите попытку.':error.message;}
    finally {exporting=false;button.disabled=rows.length===0;button.textContent='Скачать Excel';}
  };
  function detail(r) {
    const p=r.profit||{}, c=r.current_profit||{};
    el('detail-body').innerHTML=`<h2>${esc(r.name)}</h2>${r.storage_calculation?.warning?`<p class="sc-assumption">† ${esc(r.storage_calculation.warning)}</p>`:''}<p>${esc(r.store_name)} · ${esc(r.article)} · ${esc(r.scheme)}</p><p>Источник: БД калькулятора. Дата цены: ${esc(r.calculator_date||'нет данных')}.</p><p>${esc([...new Set([...(p.missing||[]),...(c.missing||[])])].join('; '))}</p><table><thead><tr><th>На 1 шт.</th><th>Без ИУ, ₽</th><th>С ИУ, ₽</th></tr></thead><tbody>${[...fields,['margin','Чистая прибыль'],['roi','ROI, %']].map(([key,name])=>`<tr><td>${name}</td><td>${fmt(p[key])}</td><td>${fmt(c[key])}</td></tr>`).join('')}</tbody></table><p>Хранение без ИУ: ${r.storage_calculation?.basis==='missing_api_zero'?'условно 0 ₽ — нет начислений API':r.storage_calculation?.basis==='estimate_90_days'?`оценка на 90 дней: ${precise(r.storage_unit_day)} ₽/шт./день × 90`:`${fmt(r.storage)} ₽ / ${fmt(r.sales)} проданных шт.`} = ${fmt(p.storage_total)} ₽/шт.</p><p>Логистика на выкупленную штуку: ${fmt(p.logistics)} ₽. Туда: ${fmt(r.logistics_returns?.forward)} ₽; обратно за поездку: ${fmt(r.logistics_returns?.reverse)} ₽; выкуп: ${fmt(r.logistics_returns?.buyout_percent)}%. Формула: туда + (туда + обратно) × (1 − p) / p. Обратный тариф по умолчанию: 46/14 ₽.</p><p>Комиссия без ИУ: ${fmt(p.retail)} ₽ × ${fmt(r.commission_rate)}% = ${fmt(p.commission)} ₽.</p>`;
    el('detail').showModal();
  }
  for(const id of ['store','search','scheme','activity','subject','manager']) el(id).addEventListener('input',()=>{page=1;render();});
  el('prev').onclick=()=>{page--;render();};el('next').onclick=()=>{page++;render();};el('close').onclick=()=>el('detail').close();
  el('method-open').onclick=()=>{hideHelp();el('method-dialog').showModal();};
  el('method-close').onclick=()=>el('method-dialog').close();
  el('method-dialog').addEventListener('click',e=>{if(e.target!==el('method-dialog'))return;const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();});
  el('table').onclick=e=>{if(e.target.closest('.sc-help, .sc-value'))return;const sort=e.target.closest('th[data-sort]');if(sort){const key=sort.dataset.sort;sortAscending=sortKey===key?!sortAscending:key!=='calculator_turnover.value';sortKey=key;page=1;render();el('table').querySelector(`th[data-sort="${key}"] .sc-sort`).focus();return;}if(e.target.closest('#sc-toggle')){expanded=!expanded;render();el('toggle').focus();return;}const b=e.target.closest('[data-row]');if(b)detail(rows[Number(b.dataset.row)]);};
  el('table').addEventListener('error',e=>{if(e.target.tagName==='IMG'){e.target.style.display='none';e.target.parentElement.textContent='Нет фото';}},true);
  function loadReport(){
    const report=document.getElementById('standard-costs');
    report.classList.add('sc-loading');report.setAttribute('aria-busy','true');
    el('loading').hidden=false;el('retry').hidden=true;el('error').hidden=true;
    return fetch('/api/unit-economics-1c/reports/standard-costs',{signal:AbortSignal.timeout(120000)}).then(async response=>{
    if(!response.ok)throw new Error('Ошибка загрузки: '+response.status);
    if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('Сессия истекла. Войдите в сайт и обновите отчёт.');
    stores=(await response.json()).stores;
    const failed=stores.filter(s=>s.error);
    el('error').hidden=failed.length===0;
    el('error').textContent=failed.length?'Не удалось загрузить: '+failed.map(s=>s.name).join(', ')+'. Данные остальных магазинов доступны.':'';
    el('store').innerHTML='<option value="">Все магазины</option>'+stores.map(s=>`<option value="${esc(s.slug)}" ${s.report?'':'disabled'}>${esc(s.name)}</option>`).join('');
    el('store').value='';
    render();
  }).catch(error=>{el('error').hidden=false;el('error').textContent=error.name==='TimeoutError'?'Сервер не ответил вовремя. Повторите попытку.':error.message;el('retry').hidden=false;})
    .finally(()=>{
      el('loading').hidden=true;
      const report=document.getElementById('standard-costs');
      report.classList.remove('sc-loading');report.setAttribute('aria-busy','false');
    });
  }
  el('retry').onclick=loadReport;
  loadReport();
})();
