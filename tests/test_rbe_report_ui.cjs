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
      prepend(child) { this.children.unshift(child); },
      after(child) { this.sibling = child; },
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
  const metric = element('metric'); metric.textContent = 'Заказы ⓘ';
  const row = element('row'); row.querySelector = selector => selector === '.metric' ? metric : element('help');
  cell.closest = () => row;
  let admin = true, automatic = true, state = 'running', value = 204008, fallbackOK = true, sign = '+', detailNote = '';
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
      else if (url.startsWith('/api/report?')) payload = {can_sync:admin,sync_running:false,values:{orders_turnover:value},quality:{orders_turnover:{complete:true}},details:{orders_turnover:{description:'Заказы включая отменённые',available:true,rows:[{sign,label:'Заказы',value}],note:detailNote}}};
      else if (url === '/api/sync-status') payload = {automatic,interval_minutes:45,lookback_days:10,max_cycle_jobs:8,max_batch_days:7,cooldown_seconds:180,cutoff:'2026-07-15',pending_store_days:1,running:false,queue:[{store:'tris',date:'2026-10-12',type:'rate_limit',title:'Пауза по ограничению Яндекса',detail:'Нужна пауза. <script>bad</script>',last_attempt:null}]};
      else if (url === '/api/sync') { posts.push(JSON.parse(options.body)); payload={run_id:7}; }
      else if (url === '/api/targeted-sync/7') payload = {run:{status:state,detail:'Даты сохранены для автоповтора'}};
      else throw Error('Unexpected URL ' + url);
      return {ok:true,json:async () => payload};
    },
  };
  const settle = async () => { for(let i=0;i<8;i++) await new Promise(resolve=>setImmediate(resolve)); };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../rbe-yandex-site/static/report.js'),'utf8'),context);
  await settle();
  assert.match(element('#sync-queue-summary').textContent, /Очередь проверок \(1\)/);
  assert.match(element('#sync-guide-state').textContent, /Автопроверка включена/);
  for (const [name, value] of [['interval','45'], ['lookback','10'], ['jobs','8'], ['batch','7'], ['cooldown','180'], ['cutoff','15.07.2026']]) {
    assert.equal(element('#sync-guide-' + name).textContent, value);
  }
  const queueGroup = element('#sync-queue-items').children[0];
  assert.match(queueGroup.children[0].textContent, /Пауза по ограничению Яндекса/);
  assert.match(queueGroup.children[1].children[0].textContent, /TRIS.*12.10.2026/);
  assert.match(queueGroup.children[1].children[0].textContent, /<script>bad<\/script>/); // text, never HTML
  const toggle = metric.children[0];
  assert.equal(toggle['aria-expanded'], 'false');
  assert.equal(row.sibling.hidden, true);
  toggle.onclick();
  assert.equal(toggle['aria-expanded'], 'true');
  assert.equal(row.sibling.hidden, false);
  assert.equal(element('help').dataset.tip, 'Заказы включая отменённые');
  const button = element('#refresh-period');
  assert.equal(button.hidden,false);
  element('#store').value = 'tris';
  element('#from').value = element('#to').value = '2026-10-12';
  button.onclick(); await settle();
  assert.deepEqual(posts[0],{date_from:'2026-10-12',date_to:'2026-10-12',store_slug:'tris'});
  assert.equal(button.disabled,true);
  assert.equal(storage.get('rbe-sync-run'),'7');
  automatic = false;
  state = 'waiting'; timeouts.shift()(); await settle();
  assert.match(element('#sync-guide-state').textContent, /Автопроверка сейчас выключена/);
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
  assert.match(html,/report\.js\?v=20261013-sync-queue/);
  assert.match(html, /<details id="sync-guide"/);
  assert.match(html, /Как работает автообновление/);
  assert.match(html, /Это не число потерянных дней/);
  assert.match(html, /не запрашиваем и убираем из очереди/);
  assert.doesNotMatch(html, /<p>Финансовый отчёт<\/p>|<h1>RBE<\/h1>/);
  assert.match(html, /class="brand"/); // Header branding is unchanged.
  assert.equal(row.sibling.children[0].children.length, 1); // Only the calculation list, no description.
  for (const [operation, input, effect] of [['+',20,'positive'], ['−',20,'negative'], ['+',-20,'negative'], ['−',-20,'positive'], ['+',0,undefined], ['−',null,undefined], ['÷',20,undefined], ['×',100,undefined]]) {
    sign = operation; value = input; await element('#apply').onclick();
    const list = row.sibling.children[0].children[0];
    assert.equal(list.children[0].children[1].dataset.effect, effect);
    assert.equal(list.children[1].children[1].dataset.effect, undefined); // Total stays neutral.
    assert.equal(element('help').dataset.tip, 'Заказы включая отменённые');
  }
  detailNote = 'Не хватает данных'; await element('#apply').onclick();
  assert.equal(row.sibling.children[0].children[0].textContent, detailNote);
  detailNote = ''; sign = '+';
  assert.equal(metric.children.length, 1); // Refreshing must not duplicate toggles.
  assert.equal(row.sibling.hidden, false); // Preserve expanded state after refresh.
  toggle.onclick(); assert.equal(row.sibling.hidden, true);
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
