// Unit-level DOM model, not browser/computer automation.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
async function run(preview=false) {
  const elements=new Map(),posts=[],timeouts=[],storage=new Map();
  function node(){return {value:'',textContent:'',children:[],hidden:false,disabled:false,
    append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
    setAttribute(key,value){this[key]=value;}};}
  function element(key){if(!elements.has(key))elements.set(key,node());return elements.get(key);}
  const payload={date_from:'2026-10-12',date_to:'2026-10-18',can_sync:true,sync_running:false,
    values:{orders_turnover:557540,detail_redeemed_count:131,detail_returned_count:27,commission:null,marginal_profit:null},
    quality:{orders_turnover:{complete:true},marginal_profit:{complete:false}},
    notes:['<script>untrusted</script>'],expenses:{known_subtotals:{commission:500},breakdown:[{store:'tris',date:'2026-10-12',sheet:'<b>placement</b>',order_id:'1',sku:'sku',amount:500}]},events:{returns:[{store:'tris',date:'2026-10-18',order_id:'1',return_id:10,sku:'<b>sku</b>',count:1,seller_amount:4051}]},campaigns:[]};
  let admin=true;
  const context={document:{querySelector:element,createElement:node},window:{RBE2_PREVIEW_DATA:preview?payload:null,setTimeout:fn=>timeouts.push(fn)},
    sessionStorage:{getItem:key=>storage.get(key),setItem:(key,v)=>storage.set(key,v),removeItem:key=>storage.delete(key)},
    navigator:{clipboard:{writeText:async()=>{}}},Intl,Date,URLSearchParams,Option:function(text,value){return {textContent:text,value};},
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
  assert.equal(element('#metrics').children.length,19);
  assert.equal(element('#notes').children[0].textContent,'<script>untrusted</script>');
  assert.equal(element('#events').children[0].children[4].textContent,'<b>sku</b>');
  assert.equal(element('#expense-details').children[0].children[2].textContent,'<b>placement</b>');
  const commission=element('#metrics').children[10];
  assert.equal(commission.children[1].textContent,'500');
  assert.equal(commission.children[1].children[0].textContent,'подтверждённая часть');
  assert.equal(commission.children[2].children[0].textContent,'Не готово');
  const profit=element('#metrics').children.at(-1);
  assert.equal(profit.children[1].textContent,'—');
  assert.equal(profit.children[2].children[0].textContent,'Не готово');
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
run().then(()=>run(true)).then(()=>console.log('RBE 2 UI unit checks passed: isolated endpoints, viewer mode, no preview requests, escaped text, unknown profit.')).catch(e=>{console.error(e);process.exitCode=1;});
