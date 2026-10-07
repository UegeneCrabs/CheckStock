// Exercises the real page controller with deferred HTTP responses, without network.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {readFileSync}=require('node:fs');
const source=readFileSync('static/finance/yandex.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Element {
  constructor(){this.hidden=false;this.open=false;this.textContent='';this.innerHTML='';this.dataset={};this.listeners={};this.children={};this.elements={};this.classList={toggle(){}};}
  addEventListener(name,fn){this.listeners[name]=fn;}
  querySelector(name){return this.children[name] ||= new Element();}
  replaceChildren(){this.innerHTML='';}
  insertAdjacentHTML(_,html){this.innerHTML+=html;}
  querySelectorAll(){return [];}
  close(){if(this.open){this.open=false;this.listeners.close?.();}}
  showModal(){this.open=true;}
  reset(){}
  submit(){this.listeners.submit({preventDefault(){},target:this});}
}
function report(marker){
  const metric={label:marker,value:'100.00',known_value:'100.00',complete:true,reasons:[]};
  const keys=['seller_turnover','expenses','profit','margin_percent','net_cost','buyout_count'];
  return {date_from:marker,date_to:marker,stores:['rimili'],version:marker,metrics:Object.fromEntries(keys.map(k=>[k,metric])),status:[],daily:[],stale:false};
}
async function setup({adminVisible=false}={}){
  const elements=new Map();
  const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  get('finance-admin').hidden=!adminVisible;
  get('finance-filters').elements=Object.fromEntries(['store','date_from','date_to'].map(key=>[key,Object.assign(new Element(),{value:''})]));
  get('finance-run-form').elements={date_from:new Element(),date_to:new Element()};
  get('finance-connection-form').elements={store_slug:{value:'rimili'},api_key:{value:'test-key'}};
  const calls=[];
  const context={document:{getElementById:get},window:{},URLSearchParams,AbortController,Intl,Date,FormData:class{constructor(form){this.form=form;}*[Symbol.iterator](){for(const [key,item]of Object.entries(this.form.elements))yield [key,item.value];}},fetch:(url,options)=>{
    if(url.endsWith('/filters'))return Promise.resolve(new Response(JSON.stringify({stores:[{id:'rimili',name:'RIMILI'}],date_from:'2026-08-01',date_to:'2026-08-31',today:'2026-10-07'})));
    if(url.endsWith('/connections'))return Promise.resolve(new Response(JSON.stringify({connections:[]})));
    return new Promise((resolve,reject)=>calls.push({url,options,resolve:body=>resolve(new Response(JSON.stringify(body))),error:message=>resolve(new Response(JSON.stringify({detail:message}),{status:409})),reject}));
  }};
  vm.runInNewContext(source,context);
  await flush();
  return {get,calls};
}
test('U01 slow first response cannot overwrite the second selection',async()=>{
  const {get,calls}=await setup();
  assert.equal(calls.length,1);
  get('finance-filters').elements.store.value='rimili';get('finance-filters').submit();
  calls[1].resolve(report('new'));await flush();
  assert.equal(get('finance-period').textContent,'new — new');
  calls[0].resolve(report('old'));await flush();
  assert.equal(get('finance-period').textContent,'new — new');
  assert.equal(calls[0].options.signal.aborted,true);
});
test('U02 new request hides old figures and preserves a visible error',async()=>{
  const {get,calls}=await setup();calls[0].resolve(report('old'));await flush();
  assert.equal(get('finance-report').hidden,false);
  get('finance-filters').submit();
  assert.equal(get('finance-report').hidden,true);
  calls[1].error('Источник временно недоступен');await flush();
  assert.equal(get('finance-report').hidden,true);
  assert.match(get('finance-message').textContent,/Источник/);
});
test('U03 details send summary version and surface 409 without stale rows',async()=>{
  const {get,calls}=await setup();calls[0].resolve(report('revision-1'));await flush();
  get('finance-report').listeners.click({target:{closest:()=>({dataset:{metric:'profit',day:''}})}});
  assert.match(calls[1].url,/version=revision-1/);
  calls[1].error('Данные обновились');await flush();
  assert.equal(get('finance-detail-rows').innerHTML,'');
  assert.equal(get('finance-detail-message').textContent,'Данные обновились');
});
test('provider text is escaped and ordinary reading uses no mutation requests',async()=>{
  const {get,calls}=await setup();calls[0].resolve(report('<img onerror="evil">'));await flush();
  assert.doesNotMatch(get('finance-cards').innerHTML,/<img/);
  assert.match(get('finance-cards').innerHTML,/&lt;img/);
  assert.equal(calls[0].options.method,undefined);
  assert.equal(calls[0].options.cache,'no-store');
});
test('closing details prevents a late response from populating the dialog',async()=>{
  const {get,calls}=await setup();calls[0].resolve(report('v'));await flush();
  get('finance-report').listeners.click({target:{closest:()=>({dataset:{metric:'profit',day:''}})}});
  get('finance-detail').close();
  calls[1].resolve({known_sum:'100',unknown_count:0,total_count:0,rows:[],page:1,page_size:50});await flush();
  assert.equal(get('finance-detail-rows').innerHTML,'');
  assert.equal(calls[1].options.signal.aborted,true);
});

test('large financial amounts retain exact decimal digits in the interface',async()=>{
  const {get,calls}=await setup();
  const data=report('large');
  data.metrics.seller_turnover={...data.metrics.seller_turnover,value:'9007199254740993.17',known_value:'9007199254740993.17'};
  calls[0].resolve(data);await flush();
  const cards=get('finance-cards').innerHTML.replace(/[\s\u00a0\u202f]/g,'');
  assert.match(cards,/9007199254740993,17/);
});

test('discovery displays the normalized campaign id returned by the provider',async()=>{
  const {get,calls}=await setup({adminVisible:true});
  calls[0].resolve(report('v'));await flush();
  get('finance-discover').listeners.click();
  calls[1].resolve({campaigns:[{business_id:11,campaign_id:101,business_name:'Demo'}]});await flush();
  assert.match(get('finance-discovery').textContent,/Business 11: Campaign 101 Demo/);
  assert.doesNotMatch(get('finance-discovery').textContent,/undefined/);
});
