(() => {
    'use strict';
    const root=document.getElementById('ephemerides'); if(!root) return;
    const $=id=>document.getElementById('e-'+id), M=window.EphemeridesModel, A=window.CheckStockAnalyzer;
    const config=JSON.parse(document.getElementById('ephemerides-config').textContent);
    const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const number=(n,percent=false)=>n==null?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:percent?1:2}).format(n)+(percent?'%':'');
    const weekLabel=w=>`W${w.number} · ${w.year}`;
    const kinds={competitor:'Конкуренты',decision:'Решения',next:'На следующую неделю'};
    const groups={product:'Реквизиты товара',stock:'Остатки',control:'Контроль товара',previous:'Предыдущая неделя',current:'Выбранная неделя',history:'Комментарии по неделям'};
    const tones={product:'product',stock:'base',control:'performance',previous:'traffic',current:'week',history:'notes'};
    const initial=new URL(location.href).searchParams.get('week');
    let week=M.monday(initial && /^\d{4}-\d{2}-\d{2}$/.test(initial) && !isNaN(Date.parse(initial)) ? initial:config.today);
    if(week>config.today || week<'2000-01-03') week=M.monday(config.today);
    let data=null, columns=[], columnMap=new Map(), filtered=[], page=1, pageSize=20, query='',segment='all', sort=null, direction=1, filterColumn=null, filterOptions=[], revisionsItem=null, latestRevision=null, busy=false;
    let separate=false, dense=false, summaryCollapsed=false, visible=new Set(Object.keys(groups));
    try { const prefs=JSON.parse(localStorage.getItem('ephemerides-view')||'null'); if(prefs) {separate=!!prefs.separate;dense=!!prefs.dense;summaryCollapsed=!!prefs.summaryCollapsed;visible=new Set((prefs.visible||Object.keys(groups)).filter(g=>g in groups));visible.add('product');} } catch {}
    const filters=new Map();
    async function jsonFetch(url, options={}) {
        const response=await fetch(url,{credentials:'same-origin',...options});
        let result; try {result=await response.json();} catch { throw new Error('Сервер вернул некорректный ответ. Проверьте соединение и вход в систему.'); }
        if(!response.ok) {const error=new Error(typeof result.detail==='string'?result.detail:`Ошибка ${response.status}`);error.conflict=response.status===409;throw error;}
        return result;
    }
    const decisionReadOnly='Решения можно редактировать только за текущую неделю';
    const canEditComment=item=>M.canEditComment(config.canEdit,item.kind,item.week);
    const drafts=new M.Drafts(async payload => {
        if(!canEditComment(payload)) throw new Error(config.canEdit?decisionReadOnly:'Нет прав на изменение комментариев');
        return (await jsonFetch('/api/analytics/ephemerides/comments',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).comment;
    }, updateSaveState);
    function commentItem(row,c) { return drafts.get(row,c.week,c.kind); }
    function commentHtml(row,c) {
        const item=commentItem(row,c), token=encodeURIComponent(item.key), view=M.commentPresentation(item,config.canEdit,!!c.readOnly);
        const content=view.editable?`<textarea maxlength="4000" aria-label="${escape(kinds[c.kind]+' · '+c.week+' · '+row.name)}" placeholder="Добавить комментарий…">${escape(item.text)}</textarea>`:`<div class="e-comment-text" data-comment-text>${escape(item.text)}</div>`;
        return `<div class="e-comment ${view.editable?'':'e-comment-readonly'}" data-comment="${escape(token)}" ${view.empty?'hidden':''}>${content}<button type="button" title="История изменений / разрешить конфликт" aria-label="История изменений">↶</button><small></small></div>`;
    }
    function updateSaveState(item) {
        const items=[...drafts.items.values()], errors=items.filter(i=>i.error), pending=items.some(i=>i.pending), dirty=items.some(i=>i.dirty);
        $('save').textContent=errors.length?`Не сохранено: ${errors.length}. ${errors.some(i=>i.error.conflict)?'Конфликт версий — откройте ↶ у поля.':'Повторите сохранение.'}`:pending?'Сохранение…':dirty?'Есть несохранённые изменения':config.canEdit?'Все изменения сохранены':'Просмотр без редактирования';
        $('save').classList.toggle('error',!!errors.length);$('retry').hidden=!errors.some(i=>!i.error.conflict);
        if(!item) return;
        root.querySelectorAll(`[data-comment="${CSS.escape(encodeURIComponent(item.key))}"]`).forEach(el=>{
            const textarea=el.querySelector('textarea');
            if(textarea) {
                textarea.readOnly=!canEditComment(item);
                if(textarea!==document.activeElement && textarea.value!==item.text) textarea.value=item.text;
            } else {
                el.querySelector('[data-comment-text]').textContent=item.text;
                el.hidden=M.commentPresentation(item,config.canEdit,true).empty;
            }
            const status=el.querySelector('small');status.classList.toggle('error',!!item.error);
            status.textContent=item.error?(item.error.conflict?'Конфликт · нажмите ↶':item.error.message):item.pending?'Сохранение…':item.dirty?'Не сохранено':item.meta?`${item.meta.author} · ${new Date(item.meta.updated_at).toLocaleString('ru-RU')}`:'';
            status.title=status.textContent;
        });
    }
    function bindComments(container) {
        container.querySelectorAll('[data-comment]').forEach(el=>{
            const item=drafts.items.get(decodeURIComponent(el.dataset.comment)), input=el.querySelector('textarea');
            if(input) {
                input.addEventListener('focus',()=>{input.readOnly=!canEditComment(item);});
                input.addEventListener('input',()=>{if(canEditComment(item))drafts.edit(item,input.value);else input.readOnly=true;});
                input.addEventListener('blur',()=>drafts.save(item));
                input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();input.blur();}});
            }
            el.querySelector('button').addEventListener('click',()=>showRevisions(item));
            updateSaveState(item);
        });
    }
    async function showRevisions(item) {
        revisionsItem=item;latestRevision=null;$('conflict-actions').hidden=true;$('revisions').textContent='Загрузка…';$('revisions-dialog').showModal();
        try {
            const params=new URLSearchParams({store:item.row.store_slug,article:item.row.article,week:item.week,kind:item.kind});
            const result=await jsonFetch('/api/analytics/ephemerides/comments/revisions?'+params);
            if(revisionsItem!==item) return;
            latestRevision=result.revisions[0]||null;
            $('revisions').innerHTML=(item.error?.conflict?`<article><strong>Ваш несохранённый текст</strong><p>${escape(item.text)||'Пустой комментарий'}</p></article>`:'')+(result.revisions.map(r=>`<article><strong>Версия ${r.version}</strong> <small>${escape(r.author)} · ${escape(new Date(r.updated_at).toLocaleString('ru-RU'))}</small><p>${escape(r.text)||'Комментарий очищен'}</p></article>`).join('')||'<p>Изменений ещё нет.</p>');
            $('conflict-actions').hidden=!(config.canEdit&&item.error?.conflict);
            $('use-draft').hidden=!canEditComment(item);
        } catch(error) { $('revisions').textContent=error.message; }
    }
    $('use-server').onclick=()=>{drafts.resolve(revisionsItem,latestRevision,false);$('revisions-dialog').close();};
    $('use-draft').onclick=async()=>{const item=revisionsItem;if(!canEditComment(item))return;drafts.resolve(item,latestRevision,true);$('revisions-dialog').close();await drafts.save(item);};
    function makeColumns() {
        const list=[];
        function add(key,label,group,width=110,type='number',extra={}) {list.push({id:key,key,label,group,width,type,...extra});}
        add('product','Товар','product',160,'product');
        if(!config.history) {
            add('manager','Менеджер','product',100,'text');add('category','Категория','product',100,'text');add('project','Проект','product',75,'text');
            if(separate) {add('barcode','Баркод','product',120,'text');add('article','Артикул WB','product',105,'text');add('image','Фото','product',55,'photo');}
            add('code','Код ABC-D','product',65,'text');
            add('fbs','Сток FBS','stock',65);add('fbo','Сток FBO','stock',65);add('fromCustomer','В пути от клиента','stock',75);add('stock','Общий сток','stock',75);
            add('rating','Рейтинг','control',65);add('competitor','Конкуренты','control',180,'comment',{week:data.current.start,kind:'competitor'});
            for(const period of ['previous','current']) {
                [['spp','СПП','percent',55],['impressions','Показы','number',70],['ctr','CTR','percent',55],['roi','ROI','percent',65],['drr','ДРР','percent',55],['wallet','Цена WB с кошельком, ₽','number',80],['price','Цена поставщика, ₽','number',80],['goal','Цель недели, шт','number',65],['fact','Факт недели, шт','number',65]].forEach(([key,label,type,width=115])=>add(period+'-'+key,label,period,width,type,{key,period}));
                if(period==='current') add('current-turnover','ТО эфемериды, ₽',period,110,'number',{key:'turnover',period});
                add(period+'-decision','Решения',period,155,'comment',{week:data[period].start,kind:'decision'});
            }
            list.push(...M.weeklyCommentColumns(data.weeks));
        } else {
            list.push(...M.weeklyCommentColumns(data.weeks,$('kind').value,250));
        }
        columnMap=new Map(list.map(c=>[c.id,c]));
        columns=list.filter(c=>config.history||visible.has(c.group));
    }
    function value(row,c) {
        if(c.type==='comment') return commentItem(row,c).text;
        if(c.type==='product') return [row.name,row.article,row.barcode].join(' · ');
        if(c.type==='photo') return row.image?'Есть фото':'Нет фото';
        return c.period?row[c.period]?.[c.key]??null:row[c.key]??null;
    }
    function incomplete(row,c) {
        if(c.key==='stock') return row.stockPartial;
        if(!c.period) return false;
        const coverage=row[c.period]?.coverage||{};
        const key={turnover:'turnover',fact:'orders',impressions:'advertising',ctr:'advertising',drr:'drr'}[c.key];
        return c.key==='roi'?!coverage.roiComplete:key?coverage[key]<coverage.expected:false;
    }
    function productHtml(row,index) {
        const image=/^https?:\/\//i.test(row.image||'')?`<img src="${escape(row.image)}" alt="" loading="lazy">`:'<span class="e-photo e-muted">WB</span>';
        return `<button class="e-product" data-product="${index}">${separate&&!config.history?'':image}<span><strong>${escape(row.name)}</strong><small>WB ${escape(row.article)}</small><small>${escape(row.barcode)}${config.history?' · '+escape(row.project):''}</small></span></button>`;
    }
    function cell(row,c) {
        const val=value(row,c);
        if(c.type==='product') return productHtml(row,data.rows.indexOf(row));
        if(c.type==='comment') return commentHtml(row,c);
        if(c.type==='photo') return /^https?:\/\//i.test(row.image||'')?`<img class="e-photo" src="${escape(row.image)}" alt="" loading="lazy">`:'—';
        if(c.type==='text') return `<span class="e-text">${escape(val||'—')}</span>`;
        const partial=incomplete(row,c), coverage=row[c.period]?.coverage;
        let title=partial?'Неполные данные':'';
        if(c.period&&c.key==='turnover') title=`Сумма заказов за ${data[c.period].start} — ${data[c.period].end}. Загружено дней: ${coverage.turnover} из ${coverage.expected}.`;
        if(c.period&&['price','wallet','spp'].includes(c.key)) title=`Последняя сохранённая цена внутри недели: ${row[c.period].priceDay||'нет данных'}`;
        if(c.key==='fromCustomer') title=`Снимок возвратов: ${row.transitUpdated||'ещё не загружен'}. Показатель всего артикула WB.`;
        if(c.key==='fbs'||c.key==='fbo') title=`Текущие остатки: ${row.stockUpdated||'нет даты'}`;
        return `<span title="${escape(title)}" class="${val==null?'e-muted':c.key==='roi'?val<0?'e-negative':'e-positive':''}">${number(val,c.type==='percent')}</span>`;
    }
    function applyFilters() {
        const q=query.toLocaleLowerCase('ru').trim();
        filtered=data.rows.filter(row=>{
            const text=[row.name,row.article,row.barcode,row.project,row.manager,row.category,...Object.values(row.comments||{}).map(c=>c?.text||'')].join(' ').toLocaleLowerCase('ru');
            if(q&&!text.includes(q)) return false;
            if(segment==='negative'&&!(row.current?.roi!=null&&row.current.roi<0)) return false;
            if(segment==='below'&&!(row.current?.goal!=null&&row.current?.fact!=null&&row.current.fact<row.current.goal)) return false;
            return [...filters].every(([key,set])=>!columnMap.has(key)||set.has(String(value(row,columnMap.get(key))??'')));
        });
        if(sort&&columnMap.has(sort)) {const c=columnMap.get(sort);filtered.sort((a,b)=>{const x=value(a,c),y=value(b,c);return x==null?y==null?0:1:y==null?-1:direction*(typeof x==='number'?x-y:String(x).localeCompare(String(y),'ru',{numeric:true}));});}
    }
    function renderSummary() {
        if(config.history) return;
        const toggle=$('summary-toggle'), action=summaryCollapsed?'Развернуть по магазинам':'Свернуть до общего итога';
        toggle.setAttribute('aria-expanded',String(!summaryCollapsed));
        toggle.setAttribute('aria-label',action);toggle.title=action;
        $('summary-arrow').textContent=summaryCollapsed?'▾':'▴';
        if(!data)return;
        const turnoverRows=filtered.map(row=>({...row,...row.dayTurnover}));
        const stores=summaryCollapsed&&turnoverRows.length?[{project:'Все магазины',...A.turnoverTotals(turnoverRows)}]:A.storeTurnover(turnoverRows);
        $('summary').innerHTML=stores.map(store=>`<tr class="${summaryCollapsed?'e-summary-total':''}"><td>${escape(store.project)}</td>${['plan','fact','forecast','difference','deviation'].map(key=>`<td title="${store.partial[key]?'Неполные данные':''}">${number(store[key],key==='deviation')}</td>`).join('')}</tr>`).join('')||'<tr><td colspan="6">Нет товаров</td></tr>';
    }
    function render() {
        if(!data) return;
        makeColumns();applyFilters();renderSummary();
        const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages);
        $('cols').innerHTML=columns.map(c=>`<col style="width:${c.width}px">`).join('');$('table').style.width=columns.reduce((n,c)=>n+c.width,0)+'px';
        const sections=[];columns.forEach(c=>{const key=c.id==='product'?'frozen':c.group;const last=sections.at(-1);if(last?.key===key)last.count++;else sections.push({key,count:1,group:c.group});});
        const label=group=>data[group]?.number?'Неделя '+weekLabel(data[group]):groups[group];
        $('head').innerHTML=`<tr class="e-group">${sections.map(s=>`<th colspan="${s.count}" class="tone-${tones[s.group]} ${s.key==='frozen'?'e-sticky':''}">${s.key==='frozen'?'Товар':escape(label(s.group))}</th>`).join('')}</tr><tr class="e-labels">${columns.map(c=>`<th data-group="${c.group}" class="tone-${tones[c.group]} ${c.id==='product'?'e-sticky':''}" aria-sort="${sort===c.id?direction===1?'ascending':'descending':'none'}"><button class="e-sort" data-sort="${c.id}">${escape(c.label)}${sort===c.id?direction===1?' ↑':' ↓':''}</button><button class="e-filter ${filters.has(c.id)?'active':''}" data-filter="${c.id}" aria-label="Фильтр: ${escape(c.label)}">▽</button></th>`).join('')}</tr>`;
        if(!config.history) $('head').insertAdjacentHTML('beforeend',`<tr class="e-totals">${columns.map(c=>{const total=M.summary(filtered,c.period,c.key);return `<td class="tone-${tones[c.group]} ${c.id==='product'?'e-sticky':'e-number'}">${c.id==='product'?'Итого · '+filtered.length:total==null?'':number(total,c.type==='percent')}</td>`;}).join('')}</tr>`);
        const shown=filtered.slice((page-1)*pageSize,page*pageSize);
        $('body').innerHTML=shown.map(row=>`<tr>${columns.map(c=>`<td class="tone-${tones[c.group]} ${c.id==='product'?'e-sticky':''} ${['number','percent'].includes(c.type)?'e-number':''}">${cell(row,c)}</td>`).join('')}</tr>`).join('');
        $('count').textContent=filtered.length;$('empty').hidden=!!filtered.length;$('pagination').textContent=filtered.length?`${(page-1)*pageSize+1}–${Math.min(page*pageSize,filtered.length)} из ${filtered.length}`:'0 товаров';$('page-number').textContent=`${page} / ${pages}`;$('prev-page').disabled=page===1;$('next-page').disabled=page===pages;
        $('filter-chips').innerHTML=[...filters.keys()].map(key=>`<button data-clear="${escape(key)}">${escape(columnMap.get(key)?.label||key)} ×</button>`).join('');
        $('jumps').innerHTML=(config.history?[]:['product','stock','control','previous','current','history']).filter(g=>visible.has(g)).map(g=>`<button data-jump="${g}">${escape(g==='product'?'Все поля':label(g))}</button>`).join('');
        bindComments($('body'));updateSaveState();
    }
    function prefs() { try {localStorage.setItem('ephemerides-view',JSON.stringify({separate,dense,summaryCollapsed,visible:[...visible]}));} catch {} }
    function showFilter(id) {
        filterColumn=columnMap.get(id);$('filter-title').textContent=filterColumn.label;$('filter-search').value='';
        filterOptions=[...new Set(data.rows.map(row=>String(value(row,filterColumn)??'')))].sort((a,b)=>a.localeCompare(b,'ru',{numeric:true})).map(text=>({text,checked:!filters.has(id)||filters.get(id).has(text)}));
        renderFilter();$('filter-dialog').showModal();
    }
    function renderFilter() {
        const q=$('filter-search').value.toLocaleLowerCase('ru');
        $('filter-values').innerHTML=filterOptions.map((item,i)=>({item,i})).filter(({item})=>item.text.toLocaleLowerCase('ru').includes(q)).map(({item,i})=>`<label><input type="checkbox" data-option="${i}" ${item.checked?'checked':''}><span>${escape(item.text||'Нет данных')}</span></label>`).join('');
    }
    $('filter-search').oninput=renderFilter;
    $('filter-values').onchange=e=>{if(e.target.dataset.option!=null)filterOptions[Number(e.target.dataset.option)].checked=e.target.checked;};
    $('filter-apply').onclick=()=>{if(filterOptions.every(o=>o.checked))filters.delete(filterColumn.id);else filters.set(filterColumn.id,new Set(filterOptions.filter(o=>o.checked).map(o=>o.text)));$('filter-dialog').close();page=1;render();};
    $('filter-reset').onclick=()=>{filters.delete(filterColumn.id);$('filter-dialog').close();page=1;render();};
    $('head').onclick=e=>{const sortButton=e.target.closest('[data-sort]'),filterButton=e.target.closest('[data-filter]');if(sortButton){direction=sort===sortButton.dataset.sort?-direction:1;sort=sortButton.dataset.sort;render();}else if(filterButton)showFilter(filterButton.dataset.filter);};
    $('filter-chips').onclick=e=>{const button=e.target.closest('[data-clear]');if(button){filters.delete(button.dataset.clear);page=1;render();}};
    $('jumps').onclick=e=>{
        const group=e.target.closest('[data-jump]')?.dataset.jump;
        if(!group)return;
        if(group==='product') {$('scroll').scrollTo({left:0,behavior:'instant'});return;}
        const th=$('head').querySelector(`.e-labels [data-group="${group}"]`);
        if(!th)return;
        const scroll=$('scroll'), frozen=$('head').querySelector('.e-labels .e-sticky');
        const visibleStart=frozen?frozen.getBoundingClientRect().right:scroll.getBoundingClientRect().left+scroll.clientLeft;
        const left=scroll.scrollLeft+th.getBoundingClientRect().left-visibleStart;
        scroll.scrollTo({left:Math.max(0,left),behavior:'smooth'});
    };
    function openDrawer(index) {
        const row=data.rows[index];if(!row)return;$('drawer').hidden=false;
        $('drawer-title').textContent=row.name;
        let html=`<p>${escape(row.project)} · WB ${escape(row.article)}</p><p class="e-muted">${escape(row.manager)} · ${escape(row.category)}</p>`;
        if(!config.history) {
            html+=`<table><thead><tr><th>Показатель</th><th>${escape(weekLabel(data.previous))}</th><th>${escape(weekLabel(data.current))}</th></tr></thead><tbody>${[['fact','Заказы, шт'],['turnover','ТО, ₽'],['roi','ROI, %'],['drr','ДРР, %'],['goal','Цель, шт'],['price','Цена, ₽']].map(([key,label])=>`<tr><td>${label}</td><td>${number(row.previous[key])}</td><td>${number(row.current[key])}</td></tr>`).join('')}</tbody></table><p>FBS ${number(row.fbs)} · FBO ${number(row.fbo)} · От клиента ${number(row.fromCustomer)}</p>`;
        }
        const start=config.history?data.weeks[0].start:data.current.start;
        Object.entries(kinds).forEach(([kind,label])=>{html+=`<h3>${escape(label)} · ${escape(start)}</h3>${commentHtml(row,{week:start,kind,readOnly:config.history&&kind==='decision'})}`;});
        $('drawer-body').innerHTML=html;bindComments($('drawer-body'));
    }
    $('body').onclick=e=>{const button=e.target.closest('[data-product]');if(button)openDrawer(Number(button.dataset.product));};
    $('close-drawer').onclick=()=>{$('drawer').hidden=true;};
    $('retry').onclick=()=>drafts.flush();
    $('search').oninput=()=>{query=$('search').value;page=1;render();};
    $('segments').onclick=e=>{const button=e.target.closest('[data-segment]');if(!button)return;segment=button.dataset.segment;$('segments').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});page=1;render();};
    $('reset').onclick=()=>{filters.clear();query='';$('search').value='';segment='all';$('segments').querySelector('[data-segment="all"]').click();};
    $('size').onchange=()=>{pageSize=Number($('size').value);page=1;render();};
    $('prev-page').onclick=()=>{page--;render();$('scroll').scrollTop=0;};$('next-page').onclick=()=>{page++;render();$('scroll').scrollTop=0;};
    $('kind').onchange=()=>{filters.clear();render();};
    $('summary-toggle').onclick=()=>{summaryCollapsed=!summaryCollapsed;prefs();renderSummary();};
    renderSummary();
    $('separate').checked=separate;$('dense').checked=dense;root.classList.toggle('e-dense',dense);
    $('groups').innerHTML=Object.entries(groups).map(([id,label])=>`<label><input type="checkbox" data-visible="${id}" ${visible.has(id)?'checked':''} ${id==='product'?'disabled':''}>${escape(label)}</label>`).join('');
    $('groups').onchange=e=>{const id=e.target.dataset.visible;if(id){e.target.checked?visible.add(id):visible.delete(id);prefs();render();}};
    $('separate').onchange=()=>{separate=$('separate').checked;prefs();render();};$('dense').onchange=()=>{dense=$('dense').checked;root.classList.toggle('e-dense',dense);prefs();};
    $('columns').onclick=()=>{$('column-panel').hidden=!$('column-panel').hidden;$('columns').setAttribute('aria-expanded',String(!$('column-panel').hidden));};
    $('columns-done').onclick=()=>{$('column-panel').hidden=true;$('columns').setAttribute('aria-expanded','false');};
    $('export').onclick=()=>{
        if(!data)return;
        const lines=[columns.map(c=>A.csvCell(c.period?weekLabel(data[c.period])+' · '+c.label:c.label)).join(';'),...filtered.map(row=>columns.map(c=>A.csvCell(value(row,c))).join(';'))];
        const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`ephemerides-${config.history?'comments-':''}${week}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    async function load(next=week) {
        if(busy)return;busy=true;$('workspace').inert=true;
        try {
            if(!await drafts.flush()) { $('load').textContent='Сначала сохраните комментарии или разрешите конфликт.';return; }
            $('load').textContent='Загрузка данных…';
            const result=await jsonFetch(`/api/analytics/ephemerides${config.history?'/comments':''}?week=${encodeURIComponent(next)}`);
            data=result;week=next;drafts.items.clear();filters.clear();page=1;$('drawer').hidden=true;render();
            $('week-label').textContent=config.history?`${weekLabel(data.weeks.at(-1))} → ${weekLabel(data.weeks[0])}`:`${weekLabel(data.previous)} → ${weekLabel(data.current)}`;
            $('other-page').href=`/analytics/ephemerides${config.history?'':'/comments'}?week=${week}`;
            $('load').textContent=config.history?'История за 12 недель. Выберите более раннюю дату для предыдущих записей.':'';
            const url=new URL(location.href);url.searchParams.set('week',week);history.replaceState(null,'',url);
        } catch(error) { $('load').replaceChildren(document.createTextNode(error.message+' '));const button=document.createElement('button');button.className='button';button.textContent='Повторить загрузку';button.onclick=()=>load(next);$('load').append(button); }
        finally {busy=false;$('workspace').inert=false;$('week').value=week;$('next-week').disabled=M.shift(week,7)>config.today;$('prev-week').disabled=week<='2000-01-03';}
    }
    $('week').max=config.today;$('week').value=week;
    $('week').onchange=()=>{if($('week').value && $('week').checkValidity())load(M.monday($('week').value));};
    $('prev-week').onclick=()=>load(M.shift(week,-7));$('next-week').onclick=()=>load(M.shift(week,7));
    $('other-page').onclick=async e=>{e.preventDefault();if(await drafts.flush())location.assign($('other-page').href);};
    window.addEventListener('beforeunload',e=>{if(drafts.dirty()){e.preventDefault();e.returnValue='';}});
    if(config.history) {$('title').textContent='История комментариев';$('summary-panel').hidden=true;$('segments').hidden=true;$('kind-wrap').hidden=false;$('other-page').textContent='← К эфемеридам';$('columns').hidden=true;}
    load();
})();
