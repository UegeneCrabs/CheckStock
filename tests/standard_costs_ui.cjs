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
      appendChild() {}, prepend() {}, remove() {}, click() {},
      querySelector(key) {if(!children.has(key))children.set(key, element());return children.get(key);},
      querySelectorAll() {return [];},
    };
  }
  const get = id => {if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  vm.runInNewContext(source, {
    document: {getElementById:get,createElement:element,body:element(),addEventListener() {}},
    window: {addEventListener() {}}, fetch, AbortSignal: {timeout:()=>({})},
    URL: {createObjectURL:()=> 'blob:test', revokeObjectURL() {}},
    setTimeout:()=>0, clearTimeout() {},
  });
  return id=>get(id==='report'?'standard-costs':'sc-'+id);
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
