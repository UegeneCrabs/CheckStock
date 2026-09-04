// Run with NODE_PATH pointing at the bundled dependencies.
const assert = require('node:assert/strict');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '../..');
const python = process.env.CHECKSTOCK_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const html = execFileSync(python, ['-c', `
from tests.unit.test_web_routes import WebRouteUnitTests
case=WebRouteUnitTests(); case.setUp()
try: print(case.client.get('/sales/unit-economics-1c/reports/target-price').text)
finally: case.tearDown()
`], { cwd: root, encoding: 'utf8', maxBuffer: 3e6, env:{...process.env,PYTHONIOENCODING:'utf-8'} });
const calculator = {retail:3086.25,client:2469,wallet:2345,spp:20,wallet_percent:5,drr:7.5,
  target_roi:50,target_overridden:false,cabinet_target_drr:7.5,cabinet_target_roi:50,
  advertising_base:3086.25,buyout_percent:80,advertising_rub:185.18,
  delivery_wb_rub:60,return_cost_rub:20,paid_acceptance_cost:0,
  acquiring_percent:3.8,delivery_with_returns:80,
  storage_wb_rub:2,turnover_days:21,wb_commission_percent:20,purchase_price:700,
  fulfillment_cost:50,team_commission_percent:2,vat_percent:9,usn_percent:6,osno_percent:0,tax_system:'usn'};
const baseRow = {store_slug:'rimili',store_name:'RIMILI',article:'949558341 / 42+',name:'Лампа «Тест»',image_url:'http://localhost:4180/product.png',
  current_price:1520,current_drr:0,current_roi:15,target_price:2345,target_retail_price:3086.25,
  target_spp_price:2469,target_actual_roi:50,target_drr:7.5,target_roi:50,target_overridden:false,
  cabinet_target_drr:7.5,cabinet_target_roi:50,advertising_base:1800,
  current_drr_warnings:[],current_drr_notes:[],current_warnings:[],current_notes:[],target_warnings:[],calculator,
  weekly:{period_from:'2026-08-27',period_to:'2026-09-02',days:7,spend:0,orders_amount:0,orders_count:0,buyout_percent:80,advertising_per_unit:0}};

(async () => {
  const browser = await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
  const context = await browser.newContext({viewport:{width:1440,height:950},acceptDownloads:true});
  const page = await context.newPage(), errors=[], exports=[], targetMutations=[];
  let targetDrr=7.5, targetRoi=50, targetOverridden=false;
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem('checkstock-theme','dark');
    Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>window.copied=value}});
  });
  await context.route('**/*', async route => {
    const request=route.request(), url=new URL(request.url()); assert.equal(url.origin,'http://localhost:4180');
    if(url.pathname.startsWith('/static/')) return route.fulfill({path:path.resolve(root,'.'+url.pathname)});
    if(url.pathname==='/product.png') return route.fulfill({body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X2NDWQAAAABJRU5ErkJggg==','base64'),contentType:'image/png'});
    if(url.pathname.endsWith('target-price.xlsx')) { exports.push(JSON.parse(request.postData())); return route.fulfill({body:Buffer.from('xlsx'),headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','Content-Disposition':'attachment; filename="target.xlsx"'}}); }
    if(url.pathname.match(/^\/api\/unit-economics-1c\/reports\/target-price\/[^/]+\/targets$/)) {
      if(request.method()==='PUT') {
        const payload=JSON.parse(request.postData()); targetDrr=payload.target_drr_percent; targetRoi=payload.target_roi_percent; targetOverridden=true;
        targetMutations.push({method:'PUT',payload});
      } else if(request.method()==='DELETE') {
        targetDrr=7.5; targetRoi=50; targetOverridden=false; targetMutations.push({method:'DELETE',article:url.searchParams.get('article')});
      }
      return route.fulfill({json:{ok:true,settings:{target_drr_percent:targetOverridden?targetDrr:null,target_roi_percent:targetOverridden?targetRoi:null}}});
    }
    if(url.pathname==='/api/unit-economics-1c/reports/target-price') {
      const current={...baseRow,target_drr:targetDrr,target_roi:targetRoi,target_overridden:targetOverridden,
        calculator:{...calculator,drr:targetDrr,target_roi:targetRoi,target_overridden:targetOverridden}};
      return route.fulfill({json:{ok:true,period_from:'2026-08-27',period_to:'2026-09-02',rows:[current,{...current,article:'other',name:'Другой товар'}]}});
    }
    if(url.pathname.endsWith('/reports/target-price')) return route.fulfill({contentType:'text/html',body:html});
    return route.fulfill({json:{ok:true}});
  });
  try {
    await page.goto('http://localhost:4180/sales/unit-economics-1c/reports/target-price');
    await page.locator('#uetp-rows tr').first().waitFor();
    assert.equal(await page.locator('#uetp-filter').count(),0);
    assert.equal((await page.locator('#uetp-rows').textContent()).includes('≈'),false);
    const article=page.locator('#uetp-rows .copy-identifier').first(); await article.hover();
    await page.getByText('Нажмите, чтобы скопировать',{exact:true}).waitFor(); await article.click();
    assert.equal(await page.evaluate(()=>window.copied),baseRow.article);
    const before=page.url(); await page.locator('#uetp-rows .uetp-product-link').first().click();
    await page.locator('#uetp-drawer.is-open').waitFor(); assert.equal(page.url(),before);
    await page.locator('#uetp-drawer-thumb img').waitFor();
    assert.ok(await page.locator('#uetp-drawer').evaluate(element => getComputedStyle(element).backgroundColor === 'rgb(255, 255, 255)'));
    assert.ok(await page.locator('#uetp-drawer-title').evaluate(element => {
      const channels=value=>value.match(/\d+/g).slice(0,3).map(Number);
      const foreground=channels(getComputedStyle(element).color), background=channels(getComputedStyle(element.closest('header')).backgroundColor);
      return Math.abs(foreground.reduce((a,b)=>a+b,0)-background.reduce((a,b)=>a+b,0))>250;
    }));
    assert.equal(await page.locator('#uetp-drawer .uetp-calculator').count(),1);
    assert.equal(await page.locator('#uetp-drawer [class*=chart], #uetp-drawer [role=tab]').count(),0);
    assert.equal((await page.locator('#uetp-calculator-results').textContent()).includes('Чистая выручка'),false);
    assert.equal(await page.locator('[data-calc=drr]').inputValue(),'7.5');
    assert.equal(await page.locator('[data-calc=buyout_percent]').inputValue(),'80');
    assert.equal(await page.locator('[data-calc=target_roi]').inputValue(),'50');
    assert.equal(await page.locator('[data-calc=advertising_rub]').inputValue(),'185.18');
    await page.locator('[data-calc=drr]').fill('10');
    const advertisingFormula = await page.locator('#uetp-drawer').evaluate(drawer => {
      const value=key=>Number(drawer.querySelector(`[data-calc=${key}]`).value);
      return {actual:value('advertising_rub'),expected:value('retail')*value('drr')/100*value('buyout_percent')/100};
    });
    assert.ok(Math.abs(advertisingFormula.actual-advertisingFormula.expected)<0.011);
    const oldRetail=await page.locator('[data-calc=retail]').inputValue();
    await page.locator('[data-calc=target_roi]').fill('20');
    assert.notEqual(await page.locator('[data-calc=retail]').inputValue(),oldRetail);
    await page.locator('#uetp-target-save').click();
    await page.getByText('Индивидуальные цели товара сохранены.',{exact:true}).waitFor();
    assert.deepEqual(targetMutations[0],{method:'PUT',payload:{article:baseRow.article,target_drr_percent:10,target_roi_percent:20}});
    assert.equal(await page.locator('[data-calc=drr]').inputValue(),'10');
    assert.equal(await page.locator('[data-calc=target_roi]').inputValue(),'20');
    await page.locator('#uetp-calculator-reset').click();
    await page.waitForFunction(()=>document.querySelector('[data-calc=drr]').value==='7.5');
    assert.equal(await page.locator('[data-calc=drr]').inputValue(),'7.5');
    assert.equal(await page.locator('[data-calc=target_roi]').inputValue(),'50');
    assert.deepEqual(targetMutations[1],{method:'DELETE',article:baseRow.article});
    await page.locator('#uetp-drawer-close').click(); await page.locator('#uetp-search').fill('949558341');
    const download=page.waitForEvent('download'); await page.locator('#uetp-export').click(); await download;
    assert.equal(exports.length,1); assert.equal(exports[0].rows.length,1); assert.equal(exports[0].rows[0].article,baseRow.article);
    await page.setViewportSize({width:390,height:844}); await page.locator('#uetp-rows .uetp-product-link').click();
    await page.waitForFunction(()=>{const r=document.getElementById('uetp-drawer').getBoundingClientRect();return r.left>=0&&Math.abs(r.right-innerWidth)<1});
    assert.deepEqual(errors,[]);
    console.log('PASS calculator drawer, no selector/approximation marker, filtered XLSX and responsive layout');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1});
