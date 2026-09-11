const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const fixture = JSON.parse(execFileSync(python, ['-c', `
import json
from app.sync_catalog import job_definitions
from app import sync_settings
from tests.unit.test_web_routes import WebRouteUnitTests
case = WebRouteUnitTests(); case.setUp()
try:
    sync_settings.save_setting('catalog_sync', enabled=False)
    print(json.dumps({'html': case.client.get('/admin/integrations').text,
        'jobs': [job.name for job in job_definitions()]}))
finally:
    case.tearDown()
`], {cwd: root, encoding: 'utf8', env: {...process.env, PYTHONIOENCODING: 'utf-8'}}));

(async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
  const errors = [], posts = [], exportRequests = [];
  const states = Object.fromEntries(fixture.jobs.map(name => [name, {name, running: false}]));
  let rejectNext = true;
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const timeout = window.setTimeout;
    window.setTimeout = (fn, ms, ...args) => timeout(fn, ms === 5000 ? 100 : ms, ...args);
  });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) return route.fulfill({path: path.resolve(root, '.' + url.pathname)});
    if (url.pathname === '/admin/integrations') return route.fulfill({contentType: 'text/html', body: fixture.html});
    if (url.pathname === '/api/admin/integrations/sync-jobs') return route.fulfill({json: {ok: true, states: Object.values(states)}});
    if (url.pathname.startsWith('/admin/google-export/') && route.request().method() === 'POST') {
      exportRequests.push({path: url.pathname, body: route.request().postData()});
      return route.fulfill({json: {ok: true, report: {marketplaces: []}}});
    }
    if (url.pathname.endsWith('/run')) {
      const name = url.pathname.split('/').at(-2);
      posts.push(name);
      if (name === 'wb_token_check' && rejectNext) {
        rejectNext = false;
        return route.fulfill({status: 502, json: {ok: false, error: 'Не удалось запустить: тестовая ошибка'}});
      }
      states[name] = {name, running: true, status: 'running', last_trigger: 'manual', last_started_at: '2026-09-11T02:00:00Z'};
      return route.fulfill({status: 202, json: {ok: true, run_id: 'test-' + name, message: 'Выгрузка запущена в фоне'}});
    }
    if (url.pathname.endsWith('/history')) return route.fulfill({json: {ok: true, runs: []}});
    return route.fulfill({json: {ok: true}});
  });
  try {
    await page.goto('http://localhost:4180/admin/integrations');
    assert.equal(await page.locator('[data-sync-run]').count(), fixture.jobs.length);
    const catalog = page.locator('[data-sync-job="catalog_sync"]');
    assert.equal(await catalog.locator('[data-sync-setting-toggle]').isChecked(), false);
    await catalog.locator('[data-sync-run]').click();
    await page.waitForFunction(() => document.querySelector('[data-sync-run="catalog_sync"]').textContent === 'Выполняется…');
    assert.equal(await catalog.locator('[data-sync-run]').isDisabled(), true);
    assert.match(await catalog.locator('[data-sync-run-message]').innerText(), /фон/);
    const stock = page.locator('[data-sync-job="stock_sync"]');
    await stock.locator('[data-sync-run]').click();
    states.catalog_sync = {...states.catalog_sync, running: false, status: 'success', last_finished_at: '2026-09-11T02:01:00Z'};
    states.stock_sync = {...states.stock_sync, running: false, status: 'error', error: 'WB: частичная ошибка <script>unsafe</script>'};
    await page.waitForFunction(() => document.querySelector('[data-sync-job="catalog_sync"] .sync-status').textContent === 'Успешно');
    assert.equal(await catalog.locator('[data-sync-run]').isDisabled(), false);
    await page.waitForFunction(() => document.querySelector('[data-sync-job="stock_sync"] .sync-status').textContent === 'Ошибка');
    assert.match(await stock.locator('[data-sync-run-message]').innerText(), /<script>unsafe<\/script>/);
    assert.equal(await stock.locator('[data-sync-run-message] script').count(), 0);
    const token = page.locator('[data-sync-job="wb_token_check"]');
    await token.locator('[data-sync-run]').click();
    await page.waitForFunction(() => document.querySelector('[data-sync-job="wb_token_check"] [data-sync-run-message]').textContent.includes('тестовая ошибка'));
    assert.equal(await token.locator('[data-sync-run]').isDisabled(), false);
    await token.locator('[data-sync-run]').click();
    await page.waitForFunction(() => document.querySelector('[data-sync-run="wb_token_check"]').disabled);
    await page.reload();
    await page.waitForFunction(() => document.querySelector('[data-sync-run="wb_token_check"]').textContent === 'Выполняется…');
    assert.deepEqual(posts, ['catalog_sync', 'stock_sync', 'wb_token_check', 'wb_token_check']);
    await token.scrollIntoViewIfNeeded();
    if (process.env.CHECKSTOCK_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'manual-sync.png')});
    await page.evaluate(() => window.scrollTo(0, 0));
    assert.equal(await page.locator('a.nav-item[href="/admin/google-export"]').count(), 0);
    assert.equal(await page.locator('[data-integration-view-panel]:visible').count(), 1);
    await page.locator('[data-sync-search]').fill('несуществующая выгрузка');
    assert.equal(await page.locator('[data-sync-empty]').isVisible(), true);
    await page.locator('[data-sync-reset]').click();
    await page.locator('[data-sync-search]').fill('Заказы Яндекс');
    assert.equal(await page.locator('[data-sync-job]:visible').count(), 1);
    await page.locator('[data-sync-search]').fill('');
    await page.locator('[data-sync-filter]').selectOption('error', {force: true});
    assert.equal(await page.locator('[data-sync-job]:visible').count(), 1);
    await page.locator('[data-sync-filter]').selectOption('all', {force: true});
    if (process.env.CHECKSTOCK_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'integrations-sync.png')});

    await page.locator('[data-integration-view="keys"]').click();
    await page.locator('[data-integration-store-tab="rimili"]').click();
    const key = page.locator('[data-credential-form][data-store="rimili"][data-marketplace="wb"]');
    await key.locator('[name="api_key"]').fill('test-unsaved-key');
    await page.locator('[data-integration-view="google"]').click();
    await page.locator('[data-export-store-tab="rimili"]').click();
    const form = page.locator('[data-export-form][data-store="rimili"]');
    assert.equal(await form.isVisible(), true);
    await form.locator('[name="wb_spreadsheet_url"]').fill('https://docs.google.com/spreadsheets/d/test-only/edit');
    await form.locator('[name="wb_sheet_name"]').fill('Тестовый лист');
    await form.locator('[data-schedule-kind]').selectOption('weekly', {force: true});
    assert.equal(await form.locator('[data-weekday-field]').isVisible(), true);
    await form.locator('[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('[data-export-form][data-store="rimili"] [data-export-status]').textContent === 'Настройки сохранены');
    assert.equal(exportRequests[0].path, '/admin/google-export/rimili');
    assert.match(exportRequests[0].body, /Тестовый лист/);
    await form.locator('[data-export-scope][data-marketplace="WB"][data-export-kind="stocks"]').click();
    await page.waitForFunction(() => document.querySelector('[data-export-form][data-store="rimili"] [data-export-status]').textContent === 'Выбранная выгрузка завершена');
    assert.match(exportRequests[1].body, /stocks/);
    await page.evaluate(() => window.scrollTo(0, 0));
    if (process.env.CHECKSTOCK_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'integrations-google.png')});
    await page.locator('[data-integration-view="keys"]').click();
    assert.equal(await key.locator('[name="api_key"]').inputValue(), 'test-unsaved-key');
    await page.goBack();
    assert.equal(await form.isVisible(), true);
    await page.goto('http://localhost:4180/admin/integrations#yandex-buyout-settings');
    assert.equal(await page.locator('[data-integration-view-panel="cabinets"]').isVisible(), true);
    await page.goto('http://localhost:4180/admin/integrations?tab=google');
    assert.equal(await page.locator('[data-integration-view-panel="google"]').isVisible(), true);
    await page.setViewportSize({width: 390, height: 844});
    for (const view of ['google', 'keys', 'sync']) {
      await page.locator('[data-integration-view="' + view + '"]').click();
      await page.evaluate(() => window.scrollTo(0, 0));
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true, 'No horizontal page overflow: ' + view);
      if (process.env.CHECKSTOCK_SCREENSHOT_DIR) await page.screenshot({path: path.join(process.env.CHECKSTOCK_SCREENSHOT_DIR, 'integrations-mobile-' + view + '.png')});
    }
    assert.deepEqual(errors, []);
    console.log('PASS unified views, Google settings/export, search, status filter, deep links, mobile, all manual buttons, async progress, disabled auto schedule, duplicates, partial errors, retry and reload');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
