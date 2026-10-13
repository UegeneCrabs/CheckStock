// Dependency-free DOM harness; run with node tests/test_rbe_report_ui.cjs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function test() {
  const elements = new Map();
  function node() {
    return {
      value:'', textContent:'', hidden:false, disabled:false, style:{}, children:[], dataset:{},
      classList:{add() {}, remove() {}},
      setAttribute(name, value) { this[name] = value; },
      append(child) { this.children.push(child); },
      replaceChildren(...children) { this.children = children; },
      select() {}, setSelectionRange() {},
      remove() { context.document.body.children = context.document.body.children.filter(child => child !== this); },
    };
  }
  function element(id) {
    if (!elements.has(id)) elements.set(id, node());
    return elements.get(id);
  }
  const cell = element('value'); cell.dataset.key = 'orders_turnover';
  let admin = true, state = 'running', value = 204008, fallbackOK = true;
  const copied = [];
  const posts = [], timeouts = [], storage = new Map();
  const context = {
    document: {
      querySelector: element,
      querySelectorAll: selector => selector === '[data-key]' ? [cell] : [],
      createElement: node,
      body: node(),
      execCommand: command => { assert.equal(command, 'copy'); if (fallbackOK) copied.push(context.document.body.children.at(-1).value); return fallbackOK; },
    },
    navigator:{clipboard:{writeText:async text => copied.push(text)}},
    window: {setTimeout: fn => timeouts.push(fn), setInterval: () => {}},
    sessionStorage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v), removeItem: k => storage.delete(k)},
    Intl, URLSearchParams, Date, String, Number, Boolean, Error,
    fetch: async (url, options) => {
      let payload;
      if (url === '/api/stores') payload = {stores:['tris','rimili']};
      else if (url.startsWith('/api/report?')) payload = {can_sync:admin,sync_running:false,values:{orders_turnover:value},quality:{orders_turnover:{complete:true}}};
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
  assert.match(html,/report\.js\?v=20261013-copy-number/);
  const copyButton = () => cell.children[0].children[1];
  for (const [input, format, expected] of [[2181892,'','2181892'], [740340.1,'','740340,1'], [-125.5,'','-125,5'], [0,'','0'], [303,'integer','303'], [-4.7,'percent','-4,7']]) {
    value = input; cell.dataset.format = format; await element('#apply').onclick();
    assert.equal(copyButton().type, 'button');
    assert.match(copyButton()['aria-label'], /Копировать число/);
    await copyButton().onclick();
    assert.equal(copied.at(-1), expected);
    assert.equal(element('#copy-status').textContent, 'Число скопировано');
  }
  for (const missing of [null, undefined]) {
    value = missing; await element('#apply').onclick();
    assert.equal(cell.children[0].children.length, 1);
    assert.equal(cell.children[0].children[0].textContent, '—');
  }
  value = 1234.5; cell.dataset.format = ''; context.navigator.clipboard = undefined;
  await element('#apply').onclick(); await copyButton().onclick();
  assert.equal(copied.at(-1), '1234,5');
  assert.equal(context.document.body.children.length, 0);
  context.navigator.clipboard = {writeText:async () => {throw Error('Denied');}};
  await copyButton().onclick(); assert.equal(copied.at(-1), '1234,5');
  fallbackOK = false; await copyButton().onclick();
  assert.match(element('#copy-status').textContent, /Не удалось/);
  assert.equal(context.document.body.children.length, 0);
  assert.equal(copyButton().disabled, false);
  console.log('UI logic OK: selected/all stores, polling, retry result, session recovery key, admin visibility');
  console.log('Copy OK: integers, decimals, negatives, zero, percentages, empty values, HTTP fallback and failures');
}
test().catch(error => {console.error(error);process.exitCode = 1;});
