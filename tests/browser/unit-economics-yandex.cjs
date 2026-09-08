const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..');
const python = process.env.CHECKSTOCK_PYTHON || path.join(root, '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const endpoint = '/sales/unit-economics-1c/yandex-market';
const bundle = JSON.parse(execFileSync(python, ['-c', `
import json
from app import db
from tests.unit.test_web_routes import WebRouteUnitTests, NOW
case = WebRouteUnitTests(); case.setUp()
try:
    db.replace_catalog('rimili', 'YANDEX MARKET', [
        {'article': f'YM-{index}', 'name': f'Товар Маркета {index}', 'barcode': f'001{index}'}
        for index in range(25)
    ], NOW)
    db.replace_catalog('tris', 'YANDEX MARKET', [
        {'article': 'YM-special', 'name': '<img src=x onerror=alert(1)> & товар', 'barcode': '000123'}
    ], NOW)
    print(json.dumps({
        'html': case.client.get('${endpoint}').text,
        'listing': case.client.get('${endpoint}?data=1').json()
    }))
finally:
    case.tearDown()
`], { cwd: root, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  const economicsRequests = [];
  let listingMode = 'normal';
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname.includes('unit-economics') && !url.pathname.startsWith('/static/')) {
        economicsRequests.push({ path: url.pathname, method: route.request().method() });
      }
      if (url.pathname.startsWith('/static/')) return route.fulfill({ path: path.resolve(root, '.' + url.pathname) });
      if (url.pathname === endpoint && url.searchParams.get('data') === '1') {
        if (listingMode === 'error') return route.fulfill({ status: 503, json: { ok: false, error: 'Каталог недоступен' } });
        return route.fulfill({ json: listingMode === 'empty' ? { ok: true, products: [] } : bundle.listing });
      }
      if (url.pathname === endpoint) return route.fulfill({ contentType: 'text/html', body: bundle.html });
      return route.fulfill({ json: { ok: true } });
    });
    await page.addInitScript(() => {
      localStorage.setItem('checkstock.unit-economics-1c.price-jobs.1', JSON.stringify(['wb-pending-job']));
    });
    await page.goto('http://localhost:4180' + endpoint);
    await page.locator('[data-product-id]').first().waitFor();
    assert.equal(await page.locator('[data-product-id]').count(), 20);
    assert.match(await page.locator('#ue1c-pagination-summary').innerText(), /из 26/);
    const cells = await page.locator('[data-product-id]').first().locator('td').allTextContents();
    for (const cell of cells.slice(3)) assert.equal(cell.trim(), '—');
    assert.equal(await page.locator('.ue1c-new-badge').first().innerText(), '—');
    assert.equal(await page.locator('#ue1c-period-days').isDisabled(), true);
    assert.equal(await page.locator('[data-state-filter="new"]').isDisabled(), true);
    assert.equal(await page.locator('[data-state-filter="negative"]').isDisabled(), true);
    await page.locator('#ue1c-page-next').click();
    assert.equal(await page.locator('[data-product-id]').count(), 6);
    await page.locator('#ue1c-page-size').selectOption('50');
    assert.equal(await page.locator('[data-product-id]').count(), 26);
    await page.locator('#ue1c-store-filter').selectOption('tris');
    assert.equal(await page.locator('[data-product-id]').count(), 1);
    assert.equal(await page.locator('[data-product-open]').innerText(), '<img src=x onerror=alert(1)> & товар');
    assert.equal(await page.locator('[data-product-id] img').count(), 0);
    await page.locator('#ue1c-store-filter').selectOption('all');
    await page.locator('#ue1c-search').fill('00124');
    assert.equal(await page.locator('[data-product-id]').count(), 1);
    assert.equal(await page.locator('[data-product-open]').innerText(), 'Товар Маркета 24');
    await page.locator('[data-product-open]').click();
    await page.locator('#ue1c-placeholder-calculator').waitFor();
    assert.equal(await page.locator('#ue1c-drawer-title').innerText(), 'Товар Маркета 24');
    for (const input of await page.locator('#ue1c-placeholder-calculator input').all()) {
      assert.equal(await input.inputValue(), '');
      assert.equal(await input.isDisabled(), true);
    }
    assert.equal(await page.locator('#ue1c-placeholder-calculator .ue1c-save-price').isDisabled(), true);
    assert.match(await page.locator('#ue1c-placeholder-calculator').innerText(), /Данных для графика пока нет/);
    await page.locator('[data-detail-tab="params"]').click();
    assert.match(await page.locator('#ue1c-parameter-groups').innerText(), /00124/);
    await page.locator('#ue1c-detail-close').click();
    await page.locator('#ue1c-search').fill('');
    await page.locator('#ue1c-columns-toggle').click();
    await page.locator('[data-column-key="comments"] input').uncheck();
    assert.equal(await page.locator('th[data-column-group="comments"]').count(), 0);
    await page.locator('#ue1c-columns-toggle').click();
    await page.reload();
    await page.locator('[data-product-id]').first().waitFor();
    assert.equal(await page.locator('th[data-column-group="comments"]').count(), 0);
    assert.equal(await page.evaluate(() => localStorage.getItem('checkstock.unit-economics-1c.columns.1')), null);
    assert.ok(economicsRequests.every(request => request.path === endpoint && request.method === 'GET'));

    listingMode = 'empty';
    await page.reload();
    await page.locator('#ue1c-empty').waitFor();
    assert.equal(await page.locator('[data-product-id]').count(), 0);
    listingMode = 'error';
    await page.reload();
    await page.locator('#ue1c-products-error').waitFor();
    assert.match(await page.locator('#ue1c-products-error').innerText(), /Каталог недоступен/);
    assert.doesNotMatch(await page.locator('#ue1c-products-error').innerText(), /WB/);
    listingMode = 'normal';
    await page.locator('#ue1c-products-retry').click();
    await page.locator('[data-product-id]').first().waitFor();
    if (process.env.CHECKSTOCK_SCREENSHOT_DIR) {
      await page.screenshot({ path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'yandex-unit-economics.png') });
      await page.locator('[data-product-open]').first().click();
      await page.locator('#ue1c-placeholder-calculator').waitFor();
      await page.locator('#ue1c-detail').evaluate(async element => {
        await Promise.all(element.getAnimations().map(animation => animation.finished));
      });
      await page.screenshot({ path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'yandex-unit-economics-detail.png') });
    }
    assert.deepEqual(errors, []);
    console.log('PASS Yandex catalog table, nulls, search, stores, pagination, drawer, column preferences, empty/error/retry states and no WB requests');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
