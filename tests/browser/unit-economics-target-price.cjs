// Run with NODE_PATH pointing at the bundled dependencies.
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..');
const python = process.env.CHECKSTOCK_PYTHON || path.join(root, '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const bundle = JSON.parse(execFileSync(python, ['-c', `
import json
from tests.unit.test_web_routes import WebRouteUnitTests
case = WebRouteUnitTests(); case.setUp()
try:
    listing = case.client.get('/sales/unit-economics-1c?data=1').json()
    item = listing['products'][0]
    detail = case.client.get('/sales/unit-economics-1c', params={
        'data': '1', 'store': item['store_slug'], 'article': item['article']
    }).json()
    print(json.dumps({'html': case.client.get('/sales/unit-economics-1c').text,
                     'listing': listing, 'detail': detail}))
finally:
    case.tearDown()
`], { cwd: root, encoding: 'utf8', maxBuffer: 8e6, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

const product = bundle.detail.product;
Object.assign(product.price, { current: 2000, with_spp: 1600, with_wallet: 1568 });
Object.assign(product.advertising, { drr: 8, buyout_percent: 80, spend_per_order: 100 });
Object.assign(product.details, {
  retail_price_used: 2000, customer_price_used: 1600, purchase_cost: 700,
  commission_percent: 20, commission_value: 400, acquiring: 3.8,
  team_commission_percent: 2, fulfillment_cost: 50, delivery_with_returns: 80,
  storage_wb_rub: 1, storage_days: 21, storage_sum: 21,
  vat_percent: 9, usn_percent: 6, osno_percent: 0, tax_system: 'usn',
  vat_value: null, usn_value: null
});
product.data_errors = [];
const second = structuredClone(product);
second.id = 'target-test-second'; second.article = 'target-test-second'; second.name = 'Второй товар';
bundle.listing.products = [product, second];

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [], mutations = [], requests = [];
  let deferred = true, queued = [], target = 1500, fail = false;
  page.on('pageerror', error => errors.push(error.message));
  async function reply(route) {
    if (fail) return route.fulfill({ status: 503, json: { ok: false } });
    const query = new URL(route.request().url()).searchParams;
    return route.fulfill({ json: { ok: true, rows: [{
      store_slug: query.get('store'), article: query.get('article'),
      target_price: target, target_warnings: target === null ? ['Нет данных для расчёта'] : []
    }] } });
  }
  await page.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (!['GET', 'HEAD'].includes(request.method()) && url.pathname.includes('unit-economics')) mutations.push(url.pathname);
    if (url.pathname.startsWith('/static/')) return route.fulfill({ path: path.resolve(root, '.' + url.pathname) });
    if (url.pathname === '/api/unit-economics-1c/reports/target-price') {
      requests.push(Object.fromEntries(url.searchParams));
      if (deferred) { queued.push(route); return; }
      return reply(route);
    }
    if (url.pathname === '/sales/unit-economics-1c') {
      if (url.searchParams.get('commissions')) return route.fulfill({ json: { ok: true, items: [] } });
      if (url.searchParams.get('article')) return route.fulfill({ json: {
        ok: true, product: url.searchParams.get('article') === second.article ? second : product
      } });
      if (url.searchParams.get('data')) return route.fulfill({ json: bundle.listing });
      return route.fulfill({ contentType: 'text/html', body: bundle.html });
    }
    return route.fulfill({ json: { ok: true } });
  });
  const opener = article => page.locator('[data-product-open]').filter({ hasText: article === second.article ? second.name : product.name }).first();
  const tile = page.locator('.ue1c-target-price-value');
  const wallet = page.locator('#ue1c-wallet-price-input');
  const highlighted = () => wallet.evaluate(input => input.classList.contains('is-target-price-different'));
  const values = () => page.locator('[data-calculator-input]').evaluateAll(inputs => inputs.map(input => input.value));
  const metrics = () => page.locator('#ue1c-price-metrics strong').evaluateAll(items => items.slice(0, 2).map(item => item.textContent));
  async function waitForQuoteRequest() {
    const deadline = Date.now() + 10000;
    while (!queued.length && Date.now() < deadline) await page.waitForTimeout(20);
    assert.ok(queued.length, 'Expected a product target quote request');
  }
  async function open() {
    await opener(product.article).click();
    await tile.waitFor();
  }
  async function reopen(newTarget, failure = false) {
    target = newTarget; fail = failure;
    await page.locator('#ue1c-detail-close').click();
    await open();
    await page.waitForFunction(() => {
      const quote = document.querySelector('.ue1c-target-price-value');
      return quote && !quote.title.includes('Загружаем');
    });
  }
  try {
    await page.goto('http://localhost:4180/sales/unit-economics-1c');
    await open();
    assert.equal(await tile.innerText(), '—');
    const beforeValues = await values(), beforeMetrics = await metrics();
    assert.ok(beforeMetrics.every(value => value !== '—'));
    await waitForQuoteRequest();
    deferred = false;
    await reply(queued.shift());
    await page.waitForFunction(() => document.querySelector('.ue1c-target-price-value').textContent !== '—');
    assert.deepEqual(await values(), beforeValues, 'Fetching target must not change any calculator inputs');
    assert.deepEqual(await metrics(), beforeMetrics, 'Fetching target must not change profit or ROI');
    assert.equal(await highlighted(), true);
    await page.waitForFunction(() => getComputedStyle(document.querySelector('#ue1c-wallet-price-input')).backgroundColor === 'rgb(255, 240, 240)');
    assert.deepEqual(requests[0], { store: product.store_slug, article: product.article });
    if (process.env.CHECKSTOCK_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'target-price-compact.png') });
    await page.locator('label[for="ue1c-calculator-mode"]').click();
    assert.equal(await page.locator('#ue1c-calculator-mode').isChecked(), true);
    assert.equal(await tile.isVisible(), true);
    assert.equal(await highlighted(), true);
    if (process.env.CHECKSTOCK_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'target-price-expanded.png') });
    await page.locator('label[for="ue1c-calculator-mode"]').click();
    assert.equal(await page.locator('#ue1c-calculator-mode').isChecked(), false);
    assert.equal(await tile.isVisible(), true);
    const currentWallet = Number(await wallet.inputValue());
    for (const [difference, expected] of [[2, false], [-2, false], [2.01, true], [-2.01, true], [0, false]]) {
      await reopen(currentWallet + difference);
      assert.equal(await highlighted(), expected, 'Target difference ' + difference);
      assert.deepEqual(await metrics(), beforeMetrics);
    }
    await reopen(null);
    assert.equal(await tile.innerText(), '—');
    assert.equal(await highlighted(), false);
    assert.match(await tile.getAttribute('title'), /Нет данных/);
    await reopen(0);
    assert.match(await tile.innerText(), /0,00/);
    assert.equal(await highlighted(), true);
    await reopen(1500, true);
    assert.equal(await tile.innerText(), '—');
    assert.equal(await highlighted(), false);
    assert.deepEqual(await metrics(), beforeMetrics);
    fail = false;
    deferred = true;
    await page.locator('#ue1c-detail-close').click();
    await open();
    await waitForQuoteRequest();
    const oldRoute = queued.shift();
    await page.locator('#ue1c-detail-close').click();
    deferred = false; target = 1234;
    await opener(second.article).click();
    await page.waitForFunction(() => document.querySelector('.ue1c-target-price-value')?.textContent.includes('234'));
    target = 9999;
    await reply(oldRoute).catch(() => {});
    assert.match(await tile.innerText(), /234/);
    assert.doesNotMatch(await tile.innerText(), /999/);
    assert.deepEqual(errors, []);
    assert.deepEqual(mutations, [], 'Viewing quotes must not save anything');
    console.log('PASS target quote parity, untouched inputs/profit/ROI, both calculator modes, ±2 threshold, zero/null/error and stale request isolation');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
