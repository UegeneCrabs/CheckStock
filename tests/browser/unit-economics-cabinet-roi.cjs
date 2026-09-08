// Run with NODE_PATH pointing at the bundled Playwright dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../..');
const template = fs.readFileSync(path.join(root, 'templates/unit_economics_1c_cabinet_settings_content.html'), 'utf8');
const defaults = {A:20, B:30, C:50, D:0, F:50, NEW:50, U:20};
const common = {target_drr_percent:8, target_roi_percent:75, buyout_period_days:14, default_buyout_percent:null,
  acceptance_coefficient:0, wb_extra_tariff_percent:0, acquiring_percent:3.8, team_commission_percent:4,
  vat_percent:9, usn_percent:6, osno_percent:0, tax_system:'usn', store_color:'#daf6eb', store_text:'#236041'};
const items = [{...common, store_slug:'rimili', store_name:'RIMILI', store_initials:'R', target_roi_by_code:{...defaults}},
  {...common, store_slug:'tris', store_name:'ТРИС', store_initials:'Т', target_roi_by_code:{...defaults, A:45}}];

(async () => {
  const browser = await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
  const context = await browser.newContext({viewport:{width:1440,height:1100}});
  const page = await context.newPage();
  const errors = [], saves = [];
  let canEdit = true;
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    assert.equal(url.origin, 'http://localhost:4180');
    if (url.pathname.startsWith('/static/')) return route.fulfill({path:path.resolve(root, '.' + url.pathname)});
    if (request.method() === 'PUT') {
      const payload = JSON.parse(request.postData());
      const item = items.find(item => url.pathname.endsWith('/' + item.store_slug));
      assert.ok(item);
      saves.push({store:item.store_slug, payload});
      Object.assign(item, payload);
      return route.fulfill({json:{ok:true,settings:item}});
    }
    const content = template.replace('$cabinet_settings_config', JSON.stringify({items, canEdit}));
    return route.fulfill({contentType:'text/html', body:'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
      + '<style>:root{--text:#172b4d;--text-muted:#617087;--border:#dce3eb;--surface:#fff;--bg:#f4f6f8}body{font-family:Arial;margin:20px}</style>'
      + '</head><body>' + content + '</body></html>'});
  });
  try {
    await page.goto('http://localhost:4180/cabinets');
    const card = page.locator('[data-store="rimili"]');
    const other = page.locator('[data-store="tris"]');
    const input = code => card.locator('[data-setting="target_roi_by_code_' + code + '"]');
    assert.equal(await card.locator('.ue1cs-roi-group legend').textContent(), 'Целевой ROI по коду товара');
    assert.equal(await card.locator('[data-setting="target_roi_percent"]').count(), 0);
    for (const [code, value] of Object.entries(defaults)) assert.equal(await input(code).inputValue(), String(value));
    assert.equal(await other.locator('[data-setting="target_roi_by_code_A"]').inputValue(), '45');
    await input('A').fill('');
    await card.locator('[data-save]').click();
    assert.equal(saves.length, 0);
    assert.equal(await input('A').getAttribute('aria-invalid'), 'true');
    await input('A').fill('-1');
    await card.locator('[data-save]').click();
    assert.equal(saves.length, 0);
    await input('A').fill('42.5');
    await input('D').fill('0');
    await card.locator('[data-setting="target_drr_percent"]').fill('7');
    await card.locator('[data-save]').click();
    await page.waitForFunction(() => document.getElementById('ue1cs-toast').textContent.includes('параметры сохранены'));
    assert.deepEqual(saves, [{store:'rimili', payload:{target_roi_by_code:{...defaults,A:42.5},
      buyout_period_days:14, default_buyout_percent:null, target_drr_percent:7,
      acceptance_coefficient:0, wb_extra_tariff_percent:0, acquiring_percent:3.8,
      vat_percent:9, usn_percent:6, tax_system:'usn', osno_percent:0}}]);
    await page.reload();
    assert.equal(await input('A').inputValue(), '42.5');
    assert.equal(await input('D').inputValue(), '0');
    assert.equal(await other.locator('[data-setting="target_roi_by_code_A"]').inputValue(), '45');
    await page.setViewportSize({width:390,height:850});
    const fits = await card.locator('.ue1cs-roi-group').evaluate(element => element.scrollWidth <= element.clientWidth);
    assert.ok(fits, 'ROI code group must fit on mobile');
    canEdit = false;
    await page.reload();
    for (const code of Object.keys(defaults)) assert.equal(await input(code).isDisabled(), true);
    assert.equal(await card.locator('[data-save]').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('Cabinet ROI browser checks passed: defaults, validation, save, cabinet scope, mobile and read-only.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
