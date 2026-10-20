// Unit-level DOM model, not browser/computer automation.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
async function run(preview=false,savedRange=null) {
  const elements=new Map(),posts=[],reports=[],timeouts=[],storage=new Map(),copied=[];
  if(savedRange)storage.set('rbe2-period',savedRange);
  function node(){return {value:'',textContent:'',children:[],hidden:false,disabled:false,dataset:{},classList:{add(){},remove(){}},
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    setAttribute(key,value){this[key]=value;}};}
  function element(key){if(!elements.has(key))elements.set(key,node());return elements.get(key);}
  const payload={date_from:'2026-10-12',date_to:'2026-10-18',can_sync:true,sync_running:false,
    values:{orders_turnover:557540,detail_redeemed_count:131,detail_returned_count:27,commission:null,storage:null,marginal_profit:null,marginal_profit_percent:null},
    quality:{orders_turnover:{complete:true},marginal_profit:{complete:false}},
    notes:['<script>untrusted</script>'],expenses:{known_subtotals:{commission:500},breakdown:[{store:'tris',date:'2026-10-12',sheet:'<b>placement</b>',order_id:'1',sku:'sku',amount:500}]},events:{returns:[{store:'tris',date:'2026-10-18',order_id:'1',return_id:10,sku:'<b>sku</b>',count:1,seller_amount:4051}]},campaigns:[]};
  if(preview){payload.values.marginal_profit=340;payload.values.buyout_buyer_turnover=500;payload.values.marginal_profit_percent=68;payload.quality.marginal_profit={complete:true};payload.quality.marginal_profit_percent={complete:true};}
  let admin=true;
  const context={document:{querySelector:element,createElement:node},window:{RBE2_PREVIEW_DATA:preview?payload:null,setTimeout:fn=>timeouts.push(fn)},
    sessionStorage:{getItem:key=>storage.get(key),setItem:(key,v)=>storage.set(key,v),removeItem:key=>storage.delete(key)},
    navigator:{clipboard:{writeText:async value=>{copied.push(value);}}},Intl,Date,URLSearchParams,Option:function(text,value){return {textContent:text,value};},
    fetch:async(url,options)=>{
      if(preview)throw Error('Preview must not request any URL');
      let result;
      if(url==='/api/stores')result={stores:['tris','rimili']};
      else if(url.startsWith('/api/rbe2/report?')){
        reports.push(url);
        const dates=new URLSearchParams(url.split('?')[1]);
        result={...payload,date_from:dates.get('date_from'),date_to:dates.get('date_to'),can_sync:admin,
          values:{...payload.values,orders_turnover:dates.get('date_to')==='2026-10-17'?12345:557540}};
      }
      else if(url==='/api/rbe2/sync-status')result={automatic:true,pending_jobs:12};
      else if(url==='/api/rbe2/sync'){posts.push(JSON.parse(options.body));result={run_id:7,detail:'Начато'};}
      else if(url==='/api/rbe2/sync/7')result={status:'completed',detail:'Загружено'};
      else throw Error('Unexpected URL '+url);
      return {ok:true,json:async()=>result};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/static/rbe2.js'),'utf8'),context);
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  if(savedRange){
    assert.equal(element('#from').value,'2026-10-12');
    assert.equal(element('#to').value,'2026-10-17');
    assert.equal(reports[0].includes('date_from=2026-10-12&date_to=2026-10-17'),true);
  }
  assert.equal(element('#metrics').children.length,26);
  if(!preview)assert.equal(element('#auto-status').textContent,'Автозагрузка включена · в очереди 12 заданий');
  const rbe1=fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/templates/index.html'),'utf8');
  const original=[...rbe1.matchAll(/<tr(?: class="total")?><th><span class="metric">([^<]+) <span class="metric-help"[^>]*>ⓘ<\/span><\/span><\/th><td data-key="([^"]+)"/g)].map(m=>[m[2],m[1]]);
  const actual=element('#metrics').children.filter((_,i)=>i%2===0).map(row=>[row.children[1].dataset.key,row.children[0].children[0].children[1].textContent]);
  assert.equal(original.length,13);
  assert.deepEqual(actual,original);
  for(const removed of ['#notes','#events','#expense-details'])assert.equal(elements.has(removed),false);
  const commission=element('#metrics').children[8];
  assert.equal(commission.children[1].children[0].children[0].textContent,'500');
  assert.equal(commission.children[1].children[1].textContent,'подтверждённая часть');
  assert.equal(commission.children[2].children[0].textContent,'Неполные');
  const profit=element('#metrics').children[22];
  assert.equal(profit.children[1].children[0].children[0].textContent,preview?'340':'—');
  assert.equal(profit.children[2].children[0].textContent,preview?'Полные':'Неполные');
  const percent=element('#metrics').children[24];
  assert.equal(percent.children[1].children[0].children[0].textContent,preview?'68%':'—');
  assert.equal(percent.className,'total');
  if(preview){await percent.children[1].children[0].children[1].onclick();assert.equal(copied.at(-1),'68');}
  assert.equal(element('#metrics').children[0].children[0].children[0].children[2].dataset.tip.includes('операционному API'),true);
  assert.equal(element('#metrics').children[0].children[1].children[0].children[1].className,'copy-number');
  await element('#metrics').children[0].children[1].children[0].children[1].onclick();
  assert.deepEqual(copied,preview?['68','557540']:[savedRange?'12345':'557540']);
  assert.equal(element('#copy-status').textContent,'Число скопировано');
  assert.equal(element('#metrics').children[1].hidden,true);
  element('#metrics').children[0].children[0].children[0].children[0].onclick();
  assert.equal(element('#metrics').children[1].hidden,false);
  assert.equal(element('#metrics').children[1].children[0].children[0].textContent.includes('операционному API'),true);
  if(preview){assert.equal(element('#refresh-rbe2').hidden,true);return;}
  element('#store').value='tris';element('#from').value='2026-10-12';element('#to').value='2026-10-18';
  await element('#refresh-rbe2').onclick();
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(posts,[{date_from:'2026-10-12',date_to:'2026-10-18',store_slug:'tris'}]);
  assert.equal(storage.has('rbe2-sync-run'),false);
  assert.equal(storage.has('rbe-sync-run'),false);
  admin=false;element('#from').value='2026-10-12';element('#to').value='2026-10-17';
  const applying=element('#apply').onclick();
  assert.equal(element('#metrics').children.length,1);
  assert.equal(element('#metrics').children[0].children[0].textContent.includes('12.10.2026 — 17.10.2026'),true);
  await applying;
  assert.equal(reports.at(-1).includes('date_from=2026-10-12&date_to=2026-10-17'),true);
  assert.equal(element('#period').textContent,'12.10.2026 — 17.10.2026');
  assert.equal(element('#metrics').children[0].children[1].children[0].children[0].textContent,'12 345');
  assert.equal(storage.get('rbe2-period'),'2026-10-12|2026-10-17');
  assert.equal(element('#refresh-rbe2').hidden,true);
}
function calendarWaitsForApply() {
  const elements=new Map();let applyClicks=0;
  function node(){return {value:'',textContent:'',children:[],hidden:false,append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;}};}
  function element(key){if(!elements.has(key))elements.set(key,node());return elements.get(key);}
  element('#from').value='2026-10-18';element('#to').value='2026-10-18';
  element('#apply').click=()=>{applyClicks++;};
  class FixedDate extends Date {constructor(...args){super(...(args.length?args:[2026,9,20]));}}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/static/calendar.js'),'utf8'),
    {document:{querySelector:element,createElement:node},Date:FixedDate});
  element('#range').onclick();
  const day=n=>element('#grid').children.find(child=>String(child.textContent)===String(n)&&typeof child.onclick==='function');
  day(12).onclick();day(17).onclick();
  assert.equal(element('#from').value,'2026-10-12');
  assert.equal(element('#to').value,'2026-10-17');
  assert.equal(applyClicks,0);
}
const html=fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/templates/rbe2.html'),'utf8');
for(const removed of ['Операционный расчёт · в разработке','Источники и правила расчёта','Что требует проверки','Товары и заказы расчёта','Комиссии, реклама и остальные начисления'])assert.equal(html.includes(removed),false);
run().then(()=>run(false,'2026-10-12|2026-10-17')).then(()=>run(true)).then(()=>{
  calendarWaitsForApply();
  console.log('RBE 2 UI unit checks passed: period selection, loading state, reload persistence, compact layout, viewer mode.');
}).catch(e=>{console.error(e);process.exitCode=1;});
