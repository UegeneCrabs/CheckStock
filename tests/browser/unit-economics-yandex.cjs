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
from app.domain import MOSCOW_TIMEZONE
from app.repositories import unit_economics_yandex as repository
from app.repositories import yandex_assortment, yandex_product_statuses, yandex_storefront
from datetime import datetime, timedelta
from unittest.mock import patch
from tests.unit.test_web_routes import WebRouteUnitTests, NOW
assortment_patch = patch.object(yandex_assortment, 'load_active_products', return_value={
    ('rimili', f'YM-{index}') for index in range(25)
} | {('tris', 'YM-special')})
assortment_patch.start()
case = WebRouteUnitTests(); case.setUp()
try:
    db.replace_catalog('rimili', 'YANDEX MARKET', [
        {'article': f'YM-{index}', 'name': f'Товар Маркета {index}', 'barcode': f'001{index}'}
        for index in range(25)
    ], NOW)
    db.replace_catalog('tris', 'YANDEX MARKET', [
        {'article': 'YM-special', 'name': '<img src=x onerror=alert(1)> & товар', 'barcode': '000123'}
    ], NOW)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    start, end = (today - timedelta(days=7)).isoformat(), (today - timedelta(days=1)).isoformat()
    db.upsert_mp_stock('rimili', 'YM-0', 'YANDEX MARKET', 'fbs', 10, NOW)
    db.upsert_mp_stock('rimili', 'YM-0', 'YANDEX MARKET', 'fbo', 20, NOW)
    db.upsert_ff_stock('rimili', 'YM-0', 'FF', 30, NOW, 'YANDEX MARKET')
    repository.save_snapshot('rimili', 'orders', [{'article': 'YM-0', 'day': end,
        'orders_count': 21, 'orders_amount': 10000, 'cancel_count': 2,
        'cancel_amount': 2000, 'sold_count': 6}],
        (today - timedelta(days=20)).isoformat(), today.isoformat(), NOW)
    repository.save_snapshot('rimili', 'advertising', [{'article': 'YM-0',
        'spend': 750, 'impressions': 2000, 'clicks': 50}], start, end, NOW)
    repository.save_snapshot('rimili', 'reputation', [{'sku': 'YM-0', 'rating': 4.8,
        'reviews_count': 125}], start, end, NOW)
    yandex_product_statuses.save_check('rimili', {'YM-0', 'YM-1'}, [{
        'article': 'YM-0', 'day': (today - timedelta(days=28)).isoformat(), 'orders_count': 1,
    }], today, NOW)
    price_target = {'store_slug': 'rimili', 'article': 'YM-0'}
    yandex_storefront.record(price_target, {'status': 'ok', 'buyer_price': 2883})
    yandex_storefront.seller_price(price_target, 3500)
    print(json.dumps({
        'html': case.client.get('${endpoint}').text,
        'listing': case.client.get('${endpoint}?data=1').json()
    }))
finally:
    case.tearDown()
    assortment_patch.stop()
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
    const productRow = page.locator('[data-product-id="yandex:rimili:YM-0"]');
    await productRow.locator('[data-product-open]').click();
    await page.locator('#ue1c-placeholder-calculator').waitFor();
    const priceInputs = page.locator('#ue1c-placeholder-calculator input');
    assert.equal(await priceInputs.nth(0).inputValue(), '3500');
    assert.equal(await priceInputs.nth(1).inputValue(), '2883');
    assert.match(await page.locator('#ue1c-placeholder-calculator').innerText(), /без Яндекс Пэй/);
    await page.locator('#ue1c-detail-close').click();
    const cells = (await productRow.locator('td').allTextContents()).map(text => text.replace(/\s/g, ''));
    assert.match(cells[0], /★4.8.*125отзывов/);
    assert.deepEqual(cells.slice(3, 6), ['—', '—', '17,63%']);
    assert.deepEqual(cells.slice(6, 13), ['8000₽', '—', '—', '10%', '750₽', '2,5%', '15,00₽']);
    const turnoverTitle = await productRow.locator('td').nth(6).locator('[title]').getAttribute('title');
    assert.match(turnoverTitle, /^ТО после отмен: данные за .*\(7 из 7 дней\)$/);
    assert.doesNotMatch(turnoverTitle, /данных для расчёта нет/);
    const zeroTurnoverTitle = await page.locator('[data-product-id="yandex:rimili:YM-1"]')
      .locator('td').nth(6).locator('[title]').getAttribute('title');
    assert.equal(zeroTurnoverTitle, turnoverTitle);
    assert.deepEqual(cells.slice(-5), ['60', '10', '20', '30', '60']);
    assert.match(await productRow.locator('td').last().locator('[title]').getAttribute('title'), /Заказы ЯМ за 21/);
    assert.equal(await productRow.locator('.ue1c-new-badge').innerText(), 'Обычный');
    assert.equal(await productRow.locator('.ue1c-newness small').count(), 0);
    assert.equal(await page.locator('[data-product-id="yandex:rimili:YM-1"] .ue1c-new-badge').innerText(), 'Новинка');
    assert.equal(await page.locator('[data-product-id="yandex:rimili:YM-2"] .ue1c-new-badge').innerText(), '—');
    assert.equal(await page.locator('#ue1c-period-days').isDisabled(), true);
    assert.equal(await page.locator('[data-state-filter="new"]').isDisabled(), false);
    assert.equal(await page.locator('[data-state-filter="negative"]').isDisabled(), true);
    await page.locator('[data-state-filter="new"]').click();
    assert.equal(await page.locator('[data-product-id]').count(), 1);
    assert.equal(await page.locator('.ue1c-new-badge').innerText(), 'Новинка');
    await page.locator('[data-state-filter="all"]').click();
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
    console.log('PASS Yandex metrics, unavailable calculations, search, stores, pagination, drawer, column preferences, empty/error/retry states and no WB requests');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
