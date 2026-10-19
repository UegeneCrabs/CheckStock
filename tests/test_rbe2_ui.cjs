// Unit-level DOM model, not browser/computer automation.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
async function run(preview=false) {
  const elements=new Map(),posts=[],timeouts=[],storage=new Map(),copied=[];
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
      else if(url.startsWith('/api/rbe2/report?'))result={...payload,can_sync:admin};
      else if(url==='/api/rbe2/sync'){posts.push(JSON.parse(options.body));result={run_id:7,detail:'Начато'};}
      else if(url==='/api/rbe2/sync/7')result={status:'completed',detail:'Загружено'};
      else throw Error('Unexpected URL '+url);
      return {ok:true,json:async()=>result};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/static/rbe2.js'),'utf8'),context);
  for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.equal(element('#metrics').children.length,26);
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
  assert.deepEqual(copied,preview?['68','557540']:['557540']);
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
  admin=false;await element('#apply').onclick();
  assert.equal(element('#refresh-rbe2').hidden,true);
}
const html=fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/templates/rbe2.html'),'utf8');
for(const removed of ['Операционный расчёт · в разработке','Источники и правила расчёта','Что требует проверки','Товары и заказы расчёта','Комиссии, реклама и остальные начисления'])assert.equal(html.includes(removed),false);
run().then(()=>run(true)).then(()=>console.log('RBE 2 UI unit checks passed: compact layout, isolated endpoints, viewer mode, no preview requests, unknown profit.')).catch(e=>{console.error(e);process.exitCode=1;});
