const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/economics/wb/standard-costs.js'), 'utf8');

function setup(fetch) {
  const nodes = new Map();
  function element() {
    const children = new Map(), classes = new Set();
    return {
      hidden: false, disabled: false, value: '', textContent: '', dataset: {},
      options: [{value: ''}], style: {setProperty() {}},
      classList: {add: x=>classes.add(x), remove: x=>classes.delete(x), contains: x=>classes.has(x)},
      addEventListener() {}, setAttribute(k,v) {this[k]=v;}, removeAttribute() {},
      showModal() {}, appendChild() {}, prepend() {}, remove() {}, click() {},
      querySelector(key) {if(!children.has(key))children.set(key, element());return children.get(key);},
      querySelectorAll() {return [];},
    };
  }
  const get = id => {if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const testWindow = {addEventListener() {}};
  vm.runInNewContext(source.replace("  function valueHelp", "  window.testDetail=(...args)=>detail(...args);window.testValueHelp=(...args)=>valueHelp(...args);\n  function valueHelp"), {
    document: {getElementById:get,createElement:element,body:element(),addEventListener() {}},
    window: testWindow, fetch, AbortSignal: {timeout:()=>({})},
    URL: {createObjectURL:()=> 'blob:test', revokeObjectURL() {}},
    setTimeout:()=>0, clearTimeout() {},
  });
  return id=>id==='detailFunction'?testWindow.testDetail:id==='valueHelp'?testWindow.testValueHelp:get(id==='report'?'standard-costs':'sc-'+id);
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const response = stores=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>({stores})});

test('loading stays visible until response, then renders empty report', async()=>{
  let resolve;
  const el=setup(()=>new Promise(r=>{resolve=r;}));
  assert.equal(el('loading').hidden,false);
  assert.equal(el('report')['aria-busy'],'true');
  resolve(response([]));await settle();
  assert.equal(el('loading').hidden,true);
  assert.equal(el('report')['aria-busy'],'false');
  assert.match(el('table').querySelector('tbody').innerHTML,/Нет товаров/);
  assert.equal(el('export').disabled,true);
});

for (const name of ['TimeoutError','TypeError']) {
  test(`${name} removes spinner and allows retry`,async()=>{
    let attempts=0;
    const el=setup(()=>++attempts===1?Promise.reject(Object.assign(new Error('failed'),{name})):Promise.resolve(response([])));
    await settle();
    assert.equal(el('loading').hidden,true);
    assert.equal(el('error').hidden,false);
    assert.equal(el('retry').hidden,false);
    await el('retry').onclick();
    assert.equal(attempts,2);
    assert.equal(el('error').hidden,true);
    assert.equal(el('retry').hidden,true);
  });
}

test('expired session does not leave an endless spinner',async()=>{
  const el=setup(async()=>({ok:true,headers:{get:()=> 'text/html'}}));
  await settle();
  assert.match(el('error').textContent,/Сессия истекла/);
  assert.equal(el('loading').hidden,true);
});

test('losses and signed differences keep their semantic highlighting; zero and missing stay neutral',async()=>{
  const rows=[
    {article:'1',scheme:'FBW',profit:{margin:-12,roi:-3},current_profit:{margin:10,roi:2},delta:{margin:-22,roi:1}},
    {article:'2',scheme:'FBW',profit:{margin:0,roi:0},current_profit:{margin:0,roi:0},delta:{margin:0,roi:0}},
    {article:'3',scheme:'FBW',profit:{margin:null,roi:null},current_profit:{margin:null,roi:null},delta:{margin:null,roi:null}},
  ];
  const el=setup(async()=>response([{slug:'tris',name:'TRIS',report:{rows}}]));
  await settle();
  const html=el('table').querySelector('tbody').innerHTML;
  assert.equal((html.match(/class="[^"]*\bsc-loss\b/g)||[]).length,2);
  assert.equal((html.match(/class="[^"]*\bsc-delta-negative\b/g)||[]).length,1);
  assert.equal((html.match(/class="[^"]*\bsc-delta-positive\b/g)||[]).length,1);
  assert.match(html,/>-12</);
  assert.match(html,/>\+1</);
});

test('export includes all filtered rows and periods, and blocks duplicate clicks',async()=>{
  let finishExport, submitted, calls=0;
  const rows=Array.from({length:60},(_,i)=>({
    article:String(i),scheme:'FBW',name:'Product',profit:{margin:-0.1},
    calculator_turnover:{value:i},
  }));
  const el=setup(async(url,options)=>{
    if(!options.method)return response([{slug:'tris',name:'TRIS',report:{
      date_from:'2026-10-12',date_to:'2026-10-18',rows,
    }}]);
    calls++;submitted=JSON.parse(options.body);
    return new Promise(resolve=>{finishExport=resolve;});
  });
  await settle();
  assert.doesNotMatch(el('table').querySelector('tbody').innerHTML,/>-0</);
  const pending=el('export').onclick();
  await el('export').onclick();
  assert.equal(calls,1);
  assert.equal(submitted.rows.length,60);
  assert.equal(submitted.rows[0].article,'59');
  assert.equal(submitted.rows[0].period_to,'2026-10-18');
  finishExport({ok:true,headers:{get:()=> 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},blob:async()=>({})});
  await pending;
  assert.equal(el('export').disabled,false);
});

test('logistics help shows shared storage volumes and weighted return rates',async()=>{
  const rows=[{article:'1',scheme:'FBW',profit:{logistics:60},logistics:51,
    logistics_calculation:{basis:'stock',terms:[{warehouse:'WB',volume:.665,volume_base:30,coefficient:170,rate:51,quantity:2}],weighted_sum:102,weight:2,localization_index:1},
    logistics_returns:{volume_source:'stock',volume_terms:[{volume:.665,quantity:2,rate:30},{volume:1.035,quantity:3,rate:46.49}],reverse:39.894,buyout_percent:90,missing:[]}}];
  const el=setup(async()=>response([{slug:'tris',name:'TRIS',report:{rows}}]));
  await settle();
  const html=el('valueHelp')(rows[0],'logistics');
  assert.match(html,/отчёт хранения WB за период/);
  assert.match(html,/0,665 л → 30 ₽; вес 2/);
  assert.match(html,/1,035 л → 46,49 ₽; вес 3/);
});

test('storage help divides by funnel net orders, not financial sales',async()=>{
  const row={article:'1',scheme:'FBW',sales:141,storage:1366.314,profit:{storage_total:9.554643},storage_calculation:{basis:'funnel_net_orders',net_orders_count:143,orders_complete:true}};
  const el=setup(async()=>response([]));await settle();
  const help=el('valueHelp')(row,'storage_total');
  assert.match(help,/143/);
  assert.match(help,/orders_count − cancel_count/);
  assert.doesNotMatch(help,/141/);
});


test('detail table exposes every metric and both explanations with precise acquiring inputs',async()=>{
  const row={name:'<unsafe>',article:'51875518',scheme:'FBW',store_name:'RIMILI',profit:{retail:151.35,acquiring:4.041045},current_profit:{retail:151.35,acquiring:4.041045},calculation_inputs:{acquiring:2.67}};
  const el=setup(async()=>response([]));await settle();el('detailFunction')(row);
  const html=el('detail-body').innerHTML;
  assert.equal((html.match(/scope="row"/g)||[]).length,14);
  assert.equal((html.match(/class="sc-detail-explanation"/g)||[]).length,28);
  assert.equal((html.match(/<details class="sc-detail-disclosure">/g)||[]).length,28);
  assert.doesNotMatch(html,/<details[^>]*\bopen\b/);
  assert.match(html,/Стандартные условия WB/);
  assert.match(html,/Данные калькулятора/);
  assert.match(html,/151,35 × 2,67 ÷ 100 = 4,041045/);
  assert.match(html,/&lt;unsafe&gt;/);
  assert.doesNotMatch(html,/<unsafe>/);
});

test('current storage and tax explain saved values instead of invented formulas',async()=>{
  const el=setup(async()=>response([]));await settle();
  const row={current_profit:{storage_total:0,vat:19},calculator_evidence:{storage_sum:0,vat_value:19},calculation_inputs:{vat_percent:7,client_price:296}};
  assert.match(el('valueHelp')(row,'storage_total','current_profit'),/details.storage_sum/);
  assert.doesNotMatch(el('valueHelp')(row,'storage_total','current_profit'),/заказы − отмены/);
  assert.match(el('valueHelp')(row,'vat','current_profit'),/готовая сумма налога/);
});

test('difference tooltip retains fractional result so the displayed subtraction agrees',async()=>{
  const el=setup(async()=>response([]));
  await settle();
  const r={profit:{margin:2.33,roi:2.14},current_profit:{margin:68.12,roi:63.25},delta:{margin:-65.79,roi:-61.11}};
  assert.match(el('valueHelp')(r,'margin','delta'),/2,33 − 68,12 = -65,79/);
  assert.match(el('valueHelp')(r,'roi','delta'),/2,14 − 63,25 = -61,11/);
});

test('missing commission date has a readable explanation',async()=>{
  const el=setup(async()=>response([]));
  await settle();
  const text=el('valueHelp')({profit:{commission:null},scheme:'FBW',subject:'Игрушки'},'commission');
  assert.match(text,/дата неизвестна/);
  assert.doesNotMatch(text,/undefined/);
});
