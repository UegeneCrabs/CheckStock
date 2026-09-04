// Run with NODE_PATH pointing at the bundled dependencies.
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..');
const python = process.env.CHECKSTOCK_PYTHON || path.join(
  root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'
);
const bundle = JSON.parse(execFileSync(python, ['-c', `
import json
from tests.unit.test_web_routes import WebRouteUnitTests
case = WebRouteUnitTests(); case.setUp()
try:
    page = case.client.get('/sales/unit-economics-1c').text
    listing = case.client.get('/sales/unit-economics-1c', params={'data': '1'}).json()
    product = listing['products'][0]
    detail = case.client.get('/sales/unit-economics-1c', params={
        'data': '1', 'store': product['store_slug'], 'article': product['article']
    }).json()
    commissions = case.client.get('/sales/unit-economics-1c', params={
        'data': '1', 'commissions': '1'
    }).json()
    print(json.dumps({'html': page, 'listing': listing, 'detail': detail, 'commissions': commissions}, ensure_ascii=False))
finally:
    case.tearDown()
`], { cwd: root, encoding: 'utf8', maxBuffer: 8e6, env: {...process.env, PYTHONIOENCODING: 'utf-8'} }));

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
  const context = await browser.newContext({viewport: {width: 1440, height: 950}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const original = window.setInterval.bind(window);
    window.setInterval = (callback, delay, ...args) => {
      if (delay === 5 * 60 * 1000) {
        window.__runUnitEconomicsRefresh = callback;
        return 1;
      }
      return original(callback, delay, ...args);
    };
  });
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.startsWith('/static/')) {
      return route.fulfill({path: path.resolve(root, '.' + url.pathname)});
    }
    if (url.pathname === '/sales/unit-economics-1c') {
      if (url.searchParams.get('commissions') === '1') return route.fulfill({json: bundle.commissions});
      if (url.searchParams.get('article')) return route.fulfill({json: bundle.detail});
      if (url.searchParams.get('data') === '1') return route.fulfill({json: bundle.listing});
      return route.fulfill({contentType: 'text/html', body: bundle.html});
    }
    return route.fulfill({json: {ok: true}});
  });

  try {
    await page.goto('http://localhost:4180/sales/unit-economics-1c');
    const opener = page.locator('[data-product-open]').first();
    await opener.waitFor();
    await opener.click();
    await page.locator('#ue1c-detail.is-open:not(.is-detail-loading)').waitFor();
    const price = page.locator('#ue1c-price-input');
    const drr = page.locator('[data-calculator-input=drr]');
    const purchase = page.locator('[data-calculator-input=purchase]');
    const databasePrice = await price.inputValue();
    const databaseDrr = await drr.inputValue();
    const databasePurchase = await purchase.inputValue();

    await price.fill('77777');
    await drr.fill('12.34');
    await purchase.fill('3333.33');
    await page.evaluate(() => document.activeElement.blur());
    const listRefresh = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/sales/unit-economics-1c'
        && url.searchParams.get('data') === '1' && !url.searchParams.get('article');
    });
    const detailRefresh = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/sales/unit-economics-1c' && Boolean(url.searchParams.get('article'));
    });
    await page.evaluate(() => window.__runUnitEconomicsRefresh());
    await listRefresh;
    await detailRefresh;
    assert.equal(await price.inputValue(), '77777');
    assert.equal(await drr.inputValue(), '12.34');
    assert.equal(await purchase.inputValue(), '3333.33');

    await page.locator('#ue1c-calculator-reset').click();
    assert.equal(await price.inputValue(), databasePrice);
    assert.equal(await drr.inputValue(), databaseDrr);
    assert.equal(await purchase.inputValue(), databasePurchase);
    await price.fill('88888');
    await page.locator('#ue1c-detail-close').click();
    await opener.click();
    await page.locator('#ue1c-detail.is-open:not(.is-detail-loading)').waitFor();
    assert.equal(await price.inputValue(), databasePrice);
    assert.deepEqual(errors, []);
    console.log('PASS calculator values survive background refresh and reset only on reset or drawer exit');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
