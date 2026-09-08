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
    print(json.dumps({
        'html': case.client.get('/sales/unit-economics-1c').text,
        'listing': case.client.get('/sales/unit-economics-1c?data=1').json()
    }))
finally:
    case.tearDown()
`], { cwd: root, encoding: 'utf8', maxBuffer: 8e6, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

const amounts = [10, -12.5, 100, 0, 2, null];
const original = bundle.listing.products[0];
bundle.listing.products = amounts.map((amount, index) => {
  const product = structuredClone(original);
  product.id = 'sort-' + index;
  product.article = String(index);
  product.name = ['Б', 'А', 'Я', 'В', 'Г', 'Д'][index];
  for (const group of ['current_economics', 'economics_7d', 'advertising', 'stock', 'tag_data']) {
    for (const key of Object.keys(product[group] || {})) {
      if (key !== 'state') product[group][key] = amount;
    }
  }
  return product;
});

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
  try {
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/static/')) return route.fulfill({path: path.resolve(root, '.' + url.pathname)});
      if (url.searchParams.get('data') === '1') return route.fulfill({json: bundle.listing});
      return route.fulfill({contentType: 'text/html', body: bundle.html});
    });
    await page.goto('http://localhost:4180/sales/unit-economics-1c');
    await page.locator('[data-product-id]').first().waitFor();
    for (const column of [2, 3, 4, 5, 6, 7, 8, 9, 10, 18, 19, 20, 21, 22]) {
      for (const [direction, label] of [['asc', 'По возрастанию'], ['desc', 'По убыванию']]) {
        const filter = page.locator('th[data-filter-column="' + column + '"] .tf-btn');
        await filter.scrollIntoViewIfNeeded();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await filter.click();
        await page.getByRole('button', {name: label, exact: true}).click();
        const ids = await page.locator('[data-product-id]').evaluateAll(rows => rows.map(row => row.dataset.productId));
        const actual = ids.map(id => amounts[Number(id.replace('sort-', ''))]).filter(value => value !== null);
        const expected = amounts.filter(value => value !== null).sort((a, b) => direction === 'asc' ? a - b : b - a);
        assert.deepEqual(actual, expected, 'column ' + column + ' ' + direction);
      }
    }
    console.log('PASS ascending and descending UI sorting for 14 numeric columns, negatives, zero and blanks');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
