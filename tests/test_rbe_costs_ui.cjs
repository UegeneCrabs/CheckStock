// Dependency-free behavior and layout contract tests for the costs page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '../rbe-yandex-site');

async function test() {
  const nodes = new Map(), requests = [], timers = [];
  function node() {
    return {value: '', textContent: '', innerHTML: '', disabled: false, setAttribute(k, v) { this[k] = v; },
      querySelector() { return this.button ||= node(); }};
  }
  function $(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); }
  const dates = [node(), node()];
  let fail = false, conflicts = [], pending, delayNext = false, running = false;
  const context = {
    document: {querySelector: $, querySelectorAll: () => dates, createElement() {
      const n = node(); Object.defineProperty(n, 'innerHTML', {get() {return n.textContent.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');}}); return n;
    }},
    FormData: class extends Map {constructor(form) {super(Object.entries(form.data));}},
    Date, Intl, URLSearchParams, setTimeout: fn => {timers.push(fn); return timers.length;}, clearTimeout() {},
    fetch: async (url, options) => {
      requests.push({url, options});
      if (url === '/api/costs/status') return {ok: true, json: async () => ({known_articles: 401, nightly_hour_msk: 2, sync_running: running})};
      if (options?.method === 'POST') return {ok: true, json: async () => ({accepted: 1, inserted: 1, updated: 0, detail: 'Загрузка запущена'})};
      if (delayNext) { delayNext = false; await new Promise(resolve => {pending = resolve;}); }
      const params = new URL(url, 'https://test.invalid').searchParams;
      const offset = Number(params.get('offset')), total = params.get('query') === 'none' ? 0 : 401;
      const rows = Array.from({length: Math.min(200, Math.max(0, total-offset))}, (_, i) => ({
        cost_date: params.get('date_from'), article: String(offset+i), cost: 10, store_slug: 'tris', source: 'google', sheet_name: '<script>bad</script>', imported_at: '2026-10-16T00:00:00Z',
      }));
      return {ok: !fail, json: async () => fail ? {detail: 'Ошибка проверки'} : {rows, total, conflicts}};
    },
  };
  const settle = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));};
  vm.runInNewContext(fs.readFileSync(path.join(root, 'static/costs.js'), 'utf8'), context);
  await settle();
  assert.equal(dates[0].value, $('#cost-from').value);
  assert.equal(dates[1].value, $('#cost-to').value);
  assert.match($('#cost-count').textContent, /1–200 из 401/);
  assert.match($('#costs').innerHTML, /&lt;script&gt;/);
  $('#cost-next').onclick(); await settle();
  assert.match($('#cost-count').textContent, /201–400/);
  $('#cost-next').onclick(); await settle();
  assert.match($('#cost-count').textContent, /401–401/);
  assert.equal($('#cost-next').disabled, true);
  $('#cost-prev').onclick(); await settle();
  assert.match($('#cost-count').textContent, /201–400/);
  $('#cost-from').value = '2026-10-12'; $('#cost-to').value = '2026-10-16';
  $('#cost-filter').onsubmit({preventDefault() {}}); await settle();
  assert.match(requests.at(-1).url, /date_from=2026-10-12&date_to=2026-10-16/);
  assert.match(requests.at(-1).url, /offset=0/);
  assert.match($('#costs').innerHTML, /12.10.2026/);
  const before = requests.length;
  $('#cost-from').value = '2026-10-17'; $('#cost-from').onchange(); await settle();
  assert.equal(requests.length, before);
  assert.match($('#message').textContent, /правильном порядке/);
  $('#cost-from').value = '2026-10-12';
  const manual = $('#manual-form'); manual.data = {article: '001234', cost: '12.50', cost_date: '2026-10-11'};
  await manual.onsubmit({preventDefault() {}, currentTarget: manual});
  const post = requests.find(r => r.url === '/api/costs' && r.options);
  assert.equal(JSON.parse(post.options.body).cost_date, '2026-10-11');
  assert.equal(JSON.parse(post.options.body).cost, 12.5);
  assert.match($('#message').textContent, /сохранена на 11.10.2026/);
  assert.equal(manual.button.disabled, false);
  const excel = $('#excel-form'); excel.data = {file: 'file', cost_date: '2026-10-10'};
  await excel.onsubmit({preventDefault() {}, currentTarget: excel});
  assert.ok(requests.some(r => r.url === '/api/costs/import-excel?cost_date=2026-10-10'));
  assert.match($('#message').textContent, /10.10.2026 импортировано/);
  fail = true; $('#cost-from').onchange(); await settle();
  assert.match($('#message').textContent, /Ошибка проверки/);
  assert.match($('#costs').innerHTML, /Не удалось загрузить/);
  fail = false; $('#query').value = 'none'; $('#cost-from').onchange(); await settle();
  assert.match($('#costs').innerHTML, /сохранённых цен нет/);
  assert.equal($('#cost-next').disabled, true);
  // A slower old filter response must not replace the currently selected range.
  delayNext = true; $('#query').value = ''; $('#cost-from').onchange(); await settle();
  $('#query').value = 'none'; $('#cost-from').onchange(); await settle();
  pending(); await settle();
  assert.match($('#costs').innerHTML, /сохранённых цен нет/);
  conflicts = [{}]; $('#cost-from').onchange(); await settle();
  assert.match($('#cost-warning').textContent, /расхождения/);
  running = true; await $('#sync-costs').onclick();
  assert.equal($('#sync-costs').disabled, true);
  assert.ok(timers.length);
  running = false; await timers.at(-1)();
  assert.equal($('#sync-costs').textContent, 'Загрузить принудительно');
  const html = fs.readFileSync(path.join(root, 'templates/costs.html'), 'utf8');
  assert.doesNotMatch(html, /Справочник закупочных цен|id="cost-date"/);
  assert.match(html, /<th>Обновлено<\/th><th>Себес, руб<\/th>/);
  assert.equal((html.match(/name="cost_date"/g) || []).length, 2);
  assert.ok(html.indexOf('id="cost-filter"') > html.indexOf('id="excel-form"'));
  assert.match(html, /costs-import-template.xlsx/);
  const css = fs.readFileSync(path.join(root, 'static/site.css'), 'utf8');
  assert.match(css, /#message:empty, #cost-warning:empty \{ display: none;/);
  console.log('Costs UI: range, paging, dates, errors, escaping, race handling, sync, and layout passed.');
}
test().catch(error => {console.error(error); process.exitCode = 1;});
