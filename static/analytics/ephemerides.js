(() => {
    'use strict';
    const root=document.getElementById('ephemerides'); if(!root) return;
    const $=id=>document.getElementById('e-'+id), M=window.EphemeridesModel, A=window.CheckStockAnalyzer;
    const config=A.marketplaceConfig(JSON.parse(document.getElementById('ephemerides-config').textContent));
    A.localize(root,config);
    const market=config.labels.marketplace;
    const isWb=config.marketplace==='WB';
    const stockLabel=isWb?'Сток, доступный к продаже':'Общий сток';
    const applyGoalsButton=$('apply-goals');
    applyGoalsButton.hidden=!isWb||config.history||!config.canEdit;
    if(isWb) {
        $('summary-plan-period').textContent='на неделю';
        $('summary-fact-period').textContent='за неделю';
        $('summary-forecast-period').textContent='на неделю';
    }
    $('other-page').href=config.pageBase+'/ephemerides'+(config.history?'':'/comments');
    const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const formats=[new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}),new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1})];
    const number=(n,percent=false)=>n==null?'—':formats[percent?1:0].format(n)+(percent?'%':'');
    const weekLabel=w=>`W${w.number} · ${w.year}`;
    const kinds={competitor:'Конкуренты',decision:'Решения',next:'На следующую неделю'};
    const groups={product:'Реквизиты товара',stock:'Остатки',control:'Контроль товара',previous:'Предыдущая неделя',current:'Выбранная неделя',history:'Комментарии по неделям'};
    const tones={product:'product',stock:'base',control:'performance',previous:'traffic',current:'week',history:'notes'};
    const initial=new URL(location.href).searchParams.get('week');
    let week=M.monday(initial && /^\d{4}-\d{2}-\d{2}$/.test(initial) && !isNaN(Date.parse(initial)) ? initial:config.today);
    if(week>config.today || week<'2000-01-03') week=M.monday(config.today);
    let data=null, columns=[], columnMap=new Map(), filtered=[], page=1, pageSize=20, query='',segment='all', sort=config.history?null:'today-turnover', direction=-1, revisionsItem=null, latestRevision=null, busy=false;
    let separate=false, dense=false, summaryCollapsed=false, visible=new Set(Object.keys(groups));
    const hiddenColumns=new Set();
    try { const prefs=JSON.parse(localStorage.getItem(config.storageKey || 'ephemerides-view')||'null'); if(prefs) {separate=!!prefs.separate;dense=!!prefs.dense;summaryCollapsed=!!prefs.summaryCollapsed;visible=new Set((prefs.visible||Object.keys(groups)).filter(g=>g in groups));visible.add('product');(prefs.hiddenColumns||[]).forEach(id=>hiddenColumns.add(id));} } catch {}
    const filters=new Map();
    let drawerRowId=null, coefficientRetryAfter=0;
    async function jsonFetch(url, options={}) {
        const response=await fetch(url,{credentials:'same-origin',...options});
        let result; try {result=await response.json();} catch { throw new Error('Сервер вернул некорректный ответ. Проверьте соединение и вход в систему.'); }
        if(!response.ok) {const error=new Error(typeof result.detail==='string'?A.reasonMessage(result.detail):`Ошибка ${response.status}`);error.conflict=response.status===409;error.goal=result.goal;throw error;}
        return result;
    }
    const decisionReadOnly='Решения можно редактировать только за текущую неделю';
    const canEditComment=item=>M.canEditComment(config.canEdit,item.kind,item.week);
    const drafts=new M.Drafts(async payload => {
        if(!canEditComment(payload)) throw new Error(config.canEdit?decisionReadOnly:'Нет прав на изменение комментариев');
        return (await jsonFetch(config.apiBase+'/ephemerides/comments',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})).comment;
    }, updateSaveState);
    const goalRecord=goal=>({...goal,text:String(goal.quantity??'')});
    const goalDrafts=new M.Drafts(async ({store,article,week,text,version})=>{
        const entered=text.trim(), quantity=entered?Number(entered):null;
        if(entered&&(!/^\d+$/.test(entered)||quantity>config.weeklyGoalMax)) {
            throw new Error('Введите целое число от 0 до '+number(config.weeklyGoalMax));
        }
        const payload={store,article,week,quantity,version};
        const result=await jsonFetch(config.apiBase+'/ephemerides/goals',{
            method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),
        });
        return goalRecord(result.goal);
    }, updateGoalSaveState, {
        read:row=>goalRecord({quantity:row.current.newGoal,version:row.newGoalVersion}),
        write:(item,goal)=>{
            item.row.current.newGoal=goal?.quantity??null;
            item.row.newGoalVersion=goal?.version??0;
            const total=$('head').querySelector('[data-weekly-goal-total]');
            if(total) {
                const quantity=M.summary(filtered,'current','newGoal',config.marketplace);
                total.textContent=quantity==null?'':number(quantity);
            }
        },
    });
    async function flushChanges() {
        const results=await Promise.all([drafts.flush(),goalDrafts.flush()]);
        return results.every(Boolean);
    }
    function commentItem(row,c) { return drafts.get(row,c.week,c.kind); }
    function commentHtml(row,c) {
        const item=commentItem(row,c), token=encodeURIComponent(item.key), view=M.commentPresentation(item,config.canEdit,!!c.readOnly);
        const content=view.editable?`<textarea maxlength="4000" aria-label="${escape(kinds[c.kind]+' · '+c.week+' · '+row.name)}" placeholder="Добавить комментарий…">${escape(item.text)}</textarea>`:`<div class="e-comment-text" data-comment-text>${escape(item.text)}</div>`;
        return `<div class="e-comment ${view.editable?'':'e-comment-readonly'}" data-comment="${escape(token)}" ${view.empty?'hidden':''}>${content}<button type="button" title="История изменений / разрешить конфликт" aria-label="История изменений">↶</button><small></small></div>`;
    }
    function updateSaveState(item) {
        const goals=[...goalDrafts.items.values()], items=[...drafts.items.values(),...goals];
        const errors=items.filter(i=>i.error), pending=items.some(i=>i.pending), dirty=items.some(i=>i.dirty);
        const errorHint=goals.some(i=>i.error?.conflict)?'В поле цели нажмите «Загрузить сохранённое».':
            errors.some(i=>i.error.conflict)?'Конфликт версий — откройте ↶ у поля.':'Повторите сохранение.';
        $('save').textContent=errors.length?`Не сохранено: ${errors.length}. ${errorHint}`:pending?'Сохранение…':dirty?'Есть несохранённые изменения':config.canEdit?'Все изменения сохранены':'Просмотр без редактирования';
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
    function goalHtml(row) {
        const item=goalDrafts.get(row,data.current.start,'goal');
        const label=`Новая цель, шт · ${row.project} · ${row.article} · ${weekLabel(data.current)}`;
        return `<div class="e-goal" data-weekly-goal="${escape(encodeURIComponent(item.key))}">
            <input type="text" inputmode="numeric" aria-label="${escape(label)}" placeholder="—" value="${escape(item.text)}">
            <small role="alert" hidden></small><button type="button" hidden>Загрузить сохранённое</button>
        </div>`;
    }
    function updateGoalSaveState(item) {
        updateSaveState();
        const editor=$('body').querySelector(`[data-weekly-goal="${CSS.escape(encodeURIComponent(item.key))}"]`);
        if(!editor) return;
        const input=editor.querySelector('input'), status=editor.querySelector('small');
        if(input!==document.activeElement&&input.value!==item.text) input.value=item.text;
        input.setAttribute('aria-invalid',String(!!item.error));
        status.hidden=!item.error;
        status.textContent=item.error?.message??'';
        editor.querySelector('button').hidden=!item.error?.goal;
    }
    function bindGoalEditors(container) {
        container.querySelectorAll('[data-weekly-goal]').forEach(editor=>{
            const item=goalDrafts.items.get(decodeURIComponent(editor.dataset.weeklyGoal)), input=editor.querySelector('input');
            input.addEventListener('input',()=>goalDrafts.edit(item,input.value));
            input.addEventListener('blur',async()=>{await goalDrafts.save(item);updateGoalSaveState(item);});
            input.addEventListener('keydown',event=>{
                if(event.key==='Enter') {event.preventDefault();input.blur();}
            });
            editor.querySelector('button').addEventListener('click',()=>{
                if(item.error?.goal) goalDrafts.resolve(item,goalRecord(item.error.goal),false);
            });
            updateGoalSaveState(item);
        });
    }
    async function showRevisions(item) {
        revisionsItem=item;latestRevision=null;$('conflict-actions').hidden=true;$('revisions').textContent='Загрузка…';$('revisions-dialog').showModal();
        try {
            const params=new URLSearchParams({store:item.row.store_slug,article:item.row.article,week:item.week,kind:item.kind});
            const result=await jsonFetch(config.apiBase+'/ephemerides/comments/revisions?'+params);
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
            if(separate) {add('barcode','Баркод','product',120,'text');add('article',config.labels.article,'product',105,'text');add('image','Фото','product',55,'photo');}
            add('code','Код ABC-D','product',65,'text');
            add('today-turnover','ТО сегодня, ₽','product',110,'number',{key:'fact',period:'dayTurnover'});
            add('fbs','Сток FBS','stock',65);add('fbo','Сток '+config.labels.fbo,'stock',65);add('fromCustomer','В пути от клиента','stock',75);add('ff','На ФФ','stock',65);
            add('stock',stockLabel,'stock',isWb?126:75);
            if(config.marketplace==='WB') add('inboundFbo','В пути на склады FBO','stock',92);
            add('rating','Рейтинг','control',65);add('competitor','Конкуренты','control',180,'comment',{week:data.current.start,kind:'competitor'});
            for(const period of ['previous','current']) {
                const metrics=[['spp','СПП','percent',55],['impressions','Показы','number',70],['ctr','CTR','percent',55],['roi','ROI','percent',65],['drr','ДРР','percent',55],['wallet',config.labels.wallet+', ₽','number',80],['price',isWb?'Цена поставщика, ₽':'Цена продавца, ₽','number',80],['goal','Цель недели, шт','number',65],['fact','Факт недели, шт','number',65]];
                for(const [key,label,type,width] of metrics) {
                    if(isWb&&period==='current'&&key==='fact') {
                        add('current-sales-trend','Тренд продаж за 3 дня, шт',period,128,'trend',{key:'salesTrend',period});
                        continue;
                    }
                    add(period+'-'+key,label,period,width,type,{key,period});
                    if(isWb&&period==='current'&&key==='goal') add('current-new-goal','Новая цель, шт',period,106,'number',{key:'newGoal',period});
                }
                if(period==='current') add('current-turnover','ТО эфемериды по цели, ₽',period,110,'number',{key:'turnover',period});
                add(period+'-decision','Решения',period,155,'comment',{week:data[period].start,kind:'decision'});
            }
            list.push(...M.weeklyCommentColumns(data.weeks));
        } else {
            list.push(...M.weeklyCommentColumns(data.weeks,$('kind').value,250));
        }
        columnMap=new Map(list.map(c=>[c.id,c]));
        columns=list.filter(c=>config.history||c.id==='product'||visible.has(c.group)&&!hiddenColumns.has(c.id));
    }
    function value(row,c) {
        if(c.type==='comment') return commentItem(row,c).text;
        if(c.type==='product') return [row.name,row.article,row.barcode].join(' · ');
        if(c.type==='photo') return row.image?'Есть фото':'Нет фото';
        const raw=c.period?row[c.period]?.[c.key]:row[c.key];
        return c.type==='trend'?trendText(raw):raw??null;
    }
    const trendText=values=>values?.some(value=>value!=null)?values.map(value=>number(value)).join(' → '):null;
    function trendHtml(values) {
        const text=trendText(values);
        if(text==null) return '<span class="e-muted">—</span>';
        const change=M.trendChange(values), tone=change>0?'e-positive':change<0?'e-negative':'';
        const title=(data.trendDays||[]).map((day,index)=>`${day}: ${number(values[index])} шт`).join(' · ');
        return `<span class="e-trend ${tone}" title="${escape(title)}">${text}</span>`;
    }
    function productHtml(row,index) {
        const image=/^https?:\/\//i.test(row.image||'')?`<img src="${escape(row.image)}" alt="" loading="lazy">`:`<span class="e-photo e-muted">${escape(market)}</span>`;
        return `<button class="e-product" data-product="${index}">${separate&&!config.history?'':image}<span><strong>${escape(row.name)}</strong><small data-copy-article="${escape(row.article)}" tabindex="0" title="Навести или нажать, чтобы скопировать артикул">${escape(market)} ${escape(row.article)} ⧉</small><small>${escape(row.barcode)}${config.history?' · '+escape(row.project):''}</small></span></button>`;
    }
    function cell(row,c) {
        const val=value(row,c);
        if(c.type==='product') return productHtml(row,data.rows.indexOf(row));
        if(c.type==='comment') return commentHtml(row,c);
        if(c.key==='newGoal'&&config.canEdit) return goalHtml(row);
        if(c.type==='trend') return trendHtml(row[c.period]?.[c.key]);
        if(c.type==='photo') return /^https?:\/\//i.test(row.image||'')?`<img class="e-photo" src="${escape(row.image)}" alt="" loading="lazy">`:'—';
        if(c.type==='text') return `<span class="e-text" ${c.key==='article'?`data-copy-article="${escape(row.article)}" tabindex="0" title="Навести или нажать, чтобы скопировать артикул"`:''}>${escape(val||'—')}</span>`;
        let title='';
        if(c.period==='dayTurnover') title=`Сумма заказов за сегодня (${data.today}, Москва), без вычета отмен. По умолчанию товары ранжируются по этому показателю.`;
        if(c.period&&c.key==='turnover') title='Цель недели, шт × Цена поставщика, ₽. Если цель или цена неизвестны, ТО не рассчитан.';
        if(c.period&&['price','wallet','spp'].includes(c.key)) title=`Последняя сохранённая цена внутри недели: ${row[c.period].priceDay||'нет данных'}`;
        if(c.key==='fromCustomer') title=`Снимок понедельника ${row.stockWeek}: ${row.transitUpdated||'ещё не сохранён'}. Показатель всего артикула ${market}.`;
        if(['fbs','fbo','ff','stock'].includes(c.key)) title=`Сток зафиксирован на неделю с ${row.stockWeek}: ${row.stockUpdated||'снимок понедельника ещё не сохранён'}`;
        if(isWb&&c.key==='stock') title+=`. Остатки FBO + FBS.${val!=null&&row.stockPartial?' Показана известная часть.':''}`;
        if(c.key==='ff') title+=`. Сумма остатков по всем фулфилментам ${market} этого товара в магазине.${val==null?' В снимке нет данных ФФ.':''}`;
        if(c.key==='inboundFbo') title='Текущие поставки WB по сохранённому снимку, включая остаток частично принятых поставок.';
        if(c.period&&c.period!=='dayTurnover'&&c.key==='fact') title=row[c.period].factNet?`Завершённая неделя: заказы − отмены. Источник — сохранённые заказы ${market}.`:
            config.marketplace==='WB'?'Заказы текущей недели за завершённые дни по Москве.':`Заказы текущей недели ${market}, с понедельника по сегодня.`;
        const field=(c.period&&c.period!=='dayTurnover'?c.period+'.':'')+c.key;
        const help=A.metricHelp(row,field,config,val==null);
        if(help) title+=(title?'. ':'')+help;
        const status=A.qualityLabel(A.metricQuality(row,field));
        const goal=config.marketplace==='WB'&&c.key==='fact'&&['previous','current'].includes(c.period)?row[c.period]?.goal:null;
        let tone=val==null?'e-muted':c.key==='roi'?val<0?'e-negative':'e-positive':'';
        if(val!=null&&goal>0) tone=val<goal?'e-negative':val*5>=goal*6?'e-over-goal':'e-positive';
        return `<span title="${escape(title)}" class="${tone}">${number(val,c.type==='percent')}</span>${status?`<small class="e-muted" style="display:block;white-space:normal;line-height:1.2">${escape(status)}</small>`:''}`;
    }
    function externalRows() {
        const q=query.toLocaleLowerCase('ru').trim();
        return data.rows.filter(row=>{
            const text=[row.name,row.article,row.barcode,row.project,row.manager,row.category,...Object.values(row.comments||{}).map(c=>c?.text||'')].join(' ').toLocaleLowerCase('ru');
            if(q&&!text.includes(q)) return false;
            if(segment==='negative'&&!(row.current?.roi!=null&&row.current.roi<0)) return false;
            if(segment==='below'&&!(row.current?.goal!=null&&row.current?.fact!=null&&row.current.fact<row.current.goal)) return false;
            return true;
        });
    }
    function applyFilters() {
        filtered=externalRows().filter(row=>[...filters].every(([key,set])=>!columnMap.has(key)||set.has(String(value(row,columnMap.get(key))??''))));
        if(sort&&columnMap.has(sort)) {
            const c=columnMap.get(sort), sortValue=c.type==='trend'?row=>M.trendChange(row[c.period]?.[c.key]):row=>value(row,c);
            filtered.sort((a,b)=>{const x=sortValue(a),y=sortValue(b);return x==null?y==null?0:1:y==null?-1:direction*(typeof x==='number'?x-y:String(x).localeCompare(String(y),'ru',{numeric:true}));});
        }
    }
    function renderSummary() {
        if(config.history) return;
        const toggle=$('summary-toggle'), action=summaryCollapsed?'Развернуть по магазинам':'Свернуть до общего итога';
        toggle.setAttribute('aria-expanded',String(!summaryCollapsed));
        toggle.setAttribute('aria-label',action);toggle.title=action;
        $('summary-arrow').textContent=summaryCollapsed?'▾':'▴';
        if(!data)return;
        const turnoverRows=filtered.map(row=>({...row,...(isWb?row.weekTurnover:row.dayTurnover)}));
        const stores=summaryCollapsed&&turnoverRows.length?[{project:'Все магазины',...A.turnoverTotals(turnoverRows)}]:A.storeTurnover(turnoverRows);
        $('summary').innerHTML=stores.map(store=>{
            const cells=['plan','fact','forecast','difference','deviation'].map(key=>{
                const partial=isWb&&store.partial[key]&&store[key]!=null;
                const marker=partial?'<span title="Неполные данные" aria-label="Неполные данные">*</span>':'';
                return `<td>${number(store[key],key==='deviation')}${marker}</td>`;
            }).join('');
            return `<tr class="${summaryCollapsed?'e-summary-total':''}"><td>${escape(store.project)}</td>${cells}</tr>`;
        }).join('')||'<tr><td colspan="6">Нет товаров</td></tr>';
    }
    function render() {
        if(!data) return;
        makeColumns();renderColumnSettings();applyFilters();renderSummary();
        const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.min(page,pages);
        $('cols').innerHTML=columns.map(c=>`<col style="width:${c.width}px">`).join('');$('table').style.width=columns.reduce((n,c)=>n+c.width,0)+'px';
        const sections=[];columns.forEach(c=>{const key=c.id==='product'?'frozen':c.group;const last=sections.at(-1);if(last?.key===key)last.count++;else sections.push({key,count:1,group:c.group});});
        const label=group=>data[group]?.number?'Неделя '+weekLabel(data[group]):groups[group];
        $('head').innerHTML=`<tr class="e-group">${sections.map(s=>`<th colspan="${s.count}" class="tone-${tones[s.group]} ${s.key==='frozen'?'e-sticky':''}">${s.key==='frozen'?'Товар':escape(label(s.group))}</th>`).join('')}</tr><tr class="e-labels">${columns.map(c=>`<th data-group="${c.group}" data-filter-column="${c.id}" ${['number','percent'].includes(c.type)?'data-filter-type="number"':''} class="tone-${tones[c.group]} ${c.id==='product'?'e-sticky':''}" aria-sort="${sort===c.id?direction===1?'ascending':'descending':'none'}"><button class="e-sort" data-sort="${c.id}">${escape(c.label)}${sort===c.id?direction===1?' ↑':' ↓':''}</button></th>`).join('')}</tr>`;
        $('table')._tfFilters=Object.fromEntries(filters);
        window.CheckStockTableFilter.refresh($('table'),{applyFilters:false});
        if(!config.history) $('head').insertAdjacentHTML('beforeend',`<tr class="e-totals">${columns.map(c=>{
            const total=M.summaryDetails(filtered,c.period,c.key,config.marketplace),status=A.qualityLabel(total);
            if(c.type==='trend') return `<td class="tone-${tones[c.group]} e-number">${trendHtml(total.value)}</td>`;
            const goalTotal=c.key==='newGoal'?' data-weekly-goal-total':'';
            return `<td${goalTotal} title="${escape(A.qualityHelp(total))}" class="tone-${tones[c.group]} ${c.id==='product'?'e-sticky':'e-number'}">${c.id==='product'?'Итого · '+filtered.length:total.value==null&&!status?'':number(total.value,c.type==='percent')+(status?`<small class="e-muted" style="display:block;white-space:normal;line-height:1.2">${escape(status)}</small>`:'')}</td>`;
        }).join('')}</tr>`);
        const shown=filtered.slice((page-1)*pageSize,page*pageSize);
        $('body').innerHTML=shown.map(row=>`<tr>${columns.map(c=>`<td class="tone-${tones[c.group]} ${c.id==='product'?'e-sticky':''} ${['number','percent'].includes(c.type)?'e-number':''}">${cell(row,c)}</td>`).join('')}</tr>`).join('');
        $('count').textContent=filtered.length;$('empty').hidden=!!filtered.length;$('pagination').textContent=filtered.length?`${(page-1)*pageSize+1}–${Math.min(page*pageSize,filtered.length)} из ${filtered.length}`:'0 товаров';$('page-number').textContent=`${page} / ${pages}`;$('prev-page').disabled=page===1;$('next-page').disabled=page===pages;
        $('filter-chips').innerHTML=[...filters.keys()].map(key=>`<button data-clear="${escape(key)}">${escape(columnMap.get(key)?.label||key)} ×</button>`).join('');
        $('jumps').innerHTML=(config.history?[]:['product','stock','control','previous','current','history']).filter(g=>visible.has(g)).map(g=>`<button data-jump="${g}">${escape(g==='product'?'Все поля':label(g))}</button>`).join('');
        bindComments($('body'));bindGoalEditors($('body'));updateSaveState();
    }
    function prefs() { try {localStorage.setItem(config.storageKey || 'ephemerides-view',JSON.stringify({separate,dense,summaryCollapsed,visible:[...visible],hiddenColumns:[...hiddenColumns]}));} catch {} }
    $('table')._tfAdapter={
        values: key=>columnMap.has(key)?externalRows().map(row=>String(value(row,columnMap.get(key))??'')):[],
        filter: selected=>{filters.clear();Object.entries(selected||{}).forEach(([key,values])=>filters.set(key,values));page=1;render();},
        sort: (key,order)=>{sort=key;direction=order==='desc'?-1:1;page=1;render();},
    };
    $('head').onclick=e=>{const button=e.target.closest('[data-sort]');if(button){direction=sort===button.dataset.sort?-direction:1;sort=button.dataset.sort;page=1;render();}};
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
        drawerRowId=row.id;
        $('drawer-title').textContent=row.name;
        let html=`<p>${escape(row.project)} · ${escape(market)} ${escape(row.article)}</p><p class="e-muted">${escape(row.manager)} · ${escape(row.category)}</p>`;
        if(!config.history) {
            html+=`<table><thead><tr><th>Показатель</th><th>${escape(weekLabel(data.previous))}</th><th>${escape(weekLabel(data.current))}</th></tr></thead><tbody>${[['fact','Заказы, шт'],['turnover','ТО по цели, ₽'],['roi','ROI, %'],['drr','ДРР, %'],['goal','Цель, шт'],['price','Цена, ₽']].map(([key,label])=>`<tr><td>${label}</td><td>${number(row.previous[key])}</td><td>${number(row.current[key])}</td></tr>`).join('')}</tbody></table><p>FBS ${number(row.fbs)} · FBO ${number(row.fbo)} · От клиента ${number(row.fromCustomer)} · На ФФ ${number(row.ff)} · ${escape(stockLabel)} ${number(row.stock)}${isWb?' · В пути на склады FBO '+number(row.inboundFbo):''}</p>`;
        }
        const start=config.history?data.weeks[0].start:data.current.start;
        Object.entries(kinds).forEach(([kind,label])=>{html+=`<h3>${escape(label)} · ${escape(start)}</h3>${commentHtml(row,{week:start,kind,readOnly:config.history&&kind==='decision'})}`;});
        $('drawer-body').innerHTML=html;bindComments($('drawer-body'));
    }
    async function copyArticle(element) {
        if(!element)return;
        try {await window.CheckStockUI.copyText(element.dataset.copyArticle);element.title='Артикул скопирован';}
        catch {element.title='Нажмите, чтобы скопировать артикул';}
    }
    $('body').addEventListener('mouseover',e=>{const item=e.target.closest('[data-copy-article]');if(item&&!item.contains(e.relatedTarget))copyArticle(item);});
    $('body').addEventListener('focusin',e=>copyArticle(e.target.closest('[data-copy-article]')));
    $('body').onclick=e=>{const article=e.target.closest('[data-copy-article]');if(article){copyArticle(article);return;}const button=e.target.closest('[data-product]');if(button)openDrawer(Number(button.dataset.product));};
    $('close-drawer').onclick=()=>{$('drawer').hidden=true;};
    $('retry').onclick=()=>flushChanges();
    $('search').oninput=()=>{query=$('search').value;page=1;render();};
    $('segments').onclick=e=>{const button=e.target.closest('[data-segment]');if(!button)return;segment=button.dataset.segment;$('segments').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});page=1;render();};
    $('reset').onclick=()=>{filters.clear();query='';$('search').value='';segment='all';sort=config.history?null:'today-turnover';direction=-1;$('segments').querySelector('[data-segment="all"]').click();};
    $('size').onchange=()=>{pageSize=Number($('size').value);page=1;render();};
    $('prev-page').onclick=()=>{page--;render();$('scroll').scrollTop=0;};$('next-page').onclick=()=>{page++;render();$('scroll').scrollTop=0;};
    $('kind').onchange=()=>{filters.clear();render();};
    $('summary-toggle').onclick=()=>{summaryCollapsed=!summaryCollapsed;prefs();renderSummary();};
    renderSummary();
    $('separate').checked=separate;$('dense').checked=dense;root.classList.toggle('e-dense',dense);
    function renderColumnSettings() {
        if(config.history)return;
        const expanded=new Set([...$('groups').querySelectorAll('details[open]')].map(item=>item.dataset.section));
        $('groups').innerHTML=Object.entries(groups).map(([id,label])=>`<details data-section="${id}" ${expanded.has(id)?'open':''}><summary>${escape(label)}</summary><label><input type="checkbox" data-visible="${id}" ${visible.has(id)?'checked':''} ${id==='product'?'disabled':''}>Показать блок</label><div class="e-column-choices">${[...columnMap.values()].filter(c=>c.group===id).map(c=>`<label><input type="checkbox" data-column="${escape(c.id)}" ${!hiddenColumns.has(c.id)||c.id==='product'?'checked':''} ${c.id==='product'||!visible.has(id)?'disabled':''}>${escape(c.label)}</label>`).join('')}</div></details>`).join('');
    }
    renderColumnSettings();
    $('groups').onchange=e=>{
        const id=e.target.dataset.visible, column=e.target.dataset.column;
        if(id&&id!=='product')e.target.checked?visible.add(id):visible.delete(id);
        if(column&&column!=='product')e.target.checked?hiddenColumns.delete(column):hiddenColumns.add(column);
        columnMap.forEach(c=>{if(!visible.has(c.group)||hiddenColumns.has(c.id))filters.delete(c.id);});
        prefs();render();
    };
    $('separate').onchange=()=>{separate=$('separate').checked;prefs();render();};$('dense').onchange=()=>{dense=$('dense').checked;root.classList.toggle('e-dense',dense);prefs();};
    $('columns').onclick=()=>{$('column-panel').hidden=!$('column-panel').hidden;$('columns').setAttribute('aria-expanded',String(!$('column-panel').hidden));};
    $('columns-done').onclick=()=>{$('column-panel').hidden=true;$('columns').setAttribute('aria-expanded','false');};
    $('export').onclick=()=>{
        if(!data)return;
        const label=c=>{
            if(c.type==='trend'&&data.trendDays?.length) return c.label+' · '+data.trendDays.join(' / ');
            if(c.period==='dayTurnover') return c.label+' · '+data.today;
            return c.period?weekLabel(data[c.period])+' · '+c.label:c.label;
        };
        const lines=A.qualityCsv(filtered,columns,value,label,c=>(c.period?c.period+'.':'')+c.key,config.qualityExport).map(row=>row.map(A.csvCell).join(';'));
        const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`${config.marketplace==='WB'?'':config.exportPrefix+'-'}ephemerides-${config.history?'comments-':''}${week}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    async function load(next=week, {refresh=false,applyGoals=false}={}) {
        if(refresh&&!applyGoals&&drafts.dirty())return;
        if(busy)return;busy=true;$('workspace').inert=true;applyGoalsButton.disabled=true;
        let applyPending=applyGoals;
        try {
            if(!await flushChanges()) { $('load').textContent='Сначала сохраните изменения или разрешите конфликт.';return; }
            if(applyPending) {
                $('load').textContent='Обновление целей…';
                const result=await jsonFetch(config.apiBase+'/ephemerides/goals/apply',{
                    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({week:next}),
                });
                applyPending=false;
                $('goal-apply-status').textContent=result.changed?'Обновлено целей: '+number(result.changed):'Нет новых целей';
            } else if(!refresh) {
                $('goal-apply-status').textContent='';
            }
            $('load').textContent='Загрузка данных…';
            const result=await jsonFetch(`${config.apiBase}/ephemerides${config.history?'/comments':''}?week=${encodeURIComponent(next)}`);
            const reopen=refresh&&!$('drawer').hidden?drawerRowId:null;
            data=result;week=next;drafts.items.clear();goalDrafts.items.clear();if(!refresh){filters.clear();page=1;$('drawer').hidden=true;}render();
            if(reopen) openDrawer(data.rows.findIndex(row=>row.id===reopen));
            $('week-label').textContent=config.history?`${weekLabel(data.weeks.at(-1))} → ${weekLabel(data.weeks[0])}`:`${weekLabel(data.previous)} → ${weekLabel(data.current)}`;
            $('other-page').href=`${config.pageBase}/ephemerides${config.history?'':'/comments'}?week=${week}`;
            $('load').textContent=config.history?'История за 12 недель. Выберите более раннюю дату для предыдущих записей.':'';
            const url=new URL(location.href);url.searchParams.set('week',week);history.replaceState(null,'',url);
        } catch(error) { $('load').replaceChildren(document.createTextNode(error.message+' '));const button=document.createElement('button');button.className='button';button.textContent='Повторить';button.onclick=()=>load(next,{refresh,applyGoals:applyPending});$('load').append(button); }
        finally {busy=false;$('workspace').inert=false;applyGoalsButton.disabled=!data;$('week').value=week;$('next-week').disabled=M.shift(week,7)>config.today;$('prev-week').disabled=week<='2000-01-03';}
    }
    $('week').max=config.today;$('week').value=week;
    $('week').onchange=()=>{if($('week').value && $('week').checkValidity())load(M.monday($('week').value));};
    $('prev-week').onclick=()=>load(M.shift(week,-7));$('next-week').onclick=()=>load(M.shift(week,7));
    applyGoalsButton.onclick=()=>load(week,{refresh:true,applyGoals:true});
    $('other-page').onclick=async e=>{e.preventDefault();if(await flushChanges())location.assign($('other-page').href);};
    window.addEventListener('beforeunload',e=>{if(drafts.dirty()||goalDrafts.dirty()){e.preventDefault();e.returnValue='';}});
    if(config.history) {$('title').textContent='История комментариев';$('summary-panel').hidden=true;$('segments').hidden=true;$('kind-wrap').hidden=false;$('other-page').textContent='← К эфемеридам';$('columns').hidden=true;}
    function refreshCoefficient() {
        if(config.marketplace!=='OZON'||config.history||document.hidden||busy||drafts.dirty()||!data?.coefficient_valid_until||Date.now()<coefficientRetryAfter||Date.now()<Date.parse(data.coefficient_valid_until))return;
        coefficientRetryAfter=Date.now()+60000;load(week,{refresh:true});
    }
    if(config.marketplace==='OZON'){setInterval(refreshCoefficient,1000);document.addEventListener('visibilitychange',refreshCoefficient);}
    load();
})();
