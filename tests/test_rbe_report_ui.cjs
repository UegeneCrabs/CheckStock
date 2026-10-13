// Dependency-free DOM harness; run with node tests/test_rbe_report_ui.cjs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function test() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value:'', textContent:'', hidden:false, disabled:false, style:{}, children:[], dataset:{},
      append(child) { this.children.push(child); },
      replaceChildren(...children) { this.children = children; },
    });
    return elements.get(id);
  }
  const cell = element('value'); cell.dataset.key = 'orders_turnover';
  let admin = true, state = 'running';
  const posts = [], timeouts = [], storage = new Map();
  const context = {
    document: {
      querySelector: element,
      querySelectorAll: selector => selector === '[data-key]' ? [cell] : [],
      createElement: () => ({style:{},dataset:{}}),
    },
    window: {setTimeout: fn => timeouts.push(fn), setInterval: () => {}},
    sessionStorage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)},
    Intl, URLSearchParams, Date, String, Number, Boolean, Error,
    fetch: async (url, options) => {
      let payload;
      if (url === '/api/stores') payload = {stores:['tris','rimili']};
      else if (url.startsWith('/api/report?')) payload = {can_sync:admin,sync_running:false,values:{orders_turnover:204008},quality:{orders_turnover:{complete:true}}};
      else if (url === '/api/sync-status') payload = {automatic:true,interval_minutes:30,lookback_days:14,pending_store_days:6,running:false};
      else if (url === '/api/sync') { posts.push(JSON.parse(options.body)); payload={run_id:7}; }
      else if (url === '/api/targeted-sync/7') payload = {run:{status:state,detail:'Даты сохранены для автоповтора'}};
      else throw Error('Unexpected URL ' + url);
      return {ok:true,json:async () => payload};
    },
  };
  const settle = async () => { for(let i=0;i<8;i++) await new Promise(resolve=>setImmediate(resolve)); };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/static/report.js'),'utf8'),context);
  await settle();
  const button = element('#refresh-period');
  assert.equal(button.hidden,false);
  element('#store').value = 'tris';
  element('#from').value = element('#to').value = '2026-10-12';
  button.onclick(); await settle();
  assert.deepEqual(posts[0],{date_from:'2026-10-12',date_to:'2026-10-12',store_slug:'tris'});
  assert.equal(button.disabled,true);
  assert.equal(storage.get('rbe-sync-run'),'7');
  state = 'waiting'; timeouts.shift()(); await settle();
  assert.equal(button.disabled,false);
  assert.equal(storage.has('rbe-sync-run'),false);
  assert.equal(element('#error').textContent,'Даты сохранены для автоповтора');
  element('#store').value = ''; button.onclick(); await settle();
  assert.equal(posts[1].store_slug,undefined);
  assert.equal(posts[1].metric,undefined);
  admin = false; await element('#apply').onclick();
  assert.equal(button.hidden,true);
  const html = fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/templates/index.html'),'utf8');
  assert.match(html,/id="refresh-period"[^>]*hidden/);
  assert.match(html,/report\.js\?v=20261013-period-refresh/);
  console.log('UI logic OK: selected/all stores, polling, retry result, session recovery key, admin visibility');
}
test().catch(error => {console.error(error);process.exitCode = 1;});
