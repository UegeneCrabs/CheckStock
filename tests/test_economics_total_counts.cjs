'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scope = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/economics/yandex/totals.js'), 'utf8'), scope);
const totals = scope.window.CheckStockYandexTotals;
function product(article, store = 'store') {
    const coverage = {complete: true, missing_parameters: {}};
    return {article, store_slug: store,
        current_economics: {margin: 10, orders: 10, buyout_percent: 80, expected_buyouts: 8,
            day_profit: 80, purchase_value: 20, day_purchase_value: 160, daily_complete: true, daily_messages: []},
        economics_7d: {margin: 80, turnover: 200, roi_purchase_value: 160, complete: true,
            margin_coverage: coverage, roi_coverage: coverage, turnover_coverage: coverage, messages: []},
        advertising: {spend: 0, drr_spend: 0, orders_amount: 200, buyout_percent: 80,
            clicks: 0, impressions: 0, coverage, drr_coverage: coverage},
        stock: {total: 10, fbs: 10, fbo: 0, fulfillment: 0, inbound: 0, orders_21d: 0,
            average_daily_orders: 0, coverage}, tag_data: {goal_day: 0, goal_week: 0, fact: 0, plan: 0}};
}
const good = product('good');
const missing = product('missing');
Object.assign(missing.current_economics, {purchase_value: null, day_purchase_value: null, daily_complete: false,
    daily_messages: ['Нет закупа', 'Нет фулфилмента', 'Нет закупа']});
const missingDays = {complete: false, missing_parameters: {'2026-09-28': ['purchase_price', 'fulfillment_cost'],
    '2026-09-29': ['purchase_price']}};
Object.assign(missing.economics_7d, {roi_purchase_value: null, complete: false,
    margin_coverage: missingDays, roi_coverage: missingDays, messages: ['Нет закупа', 'Нет фулфилмента']});
const noAds = product('no-ads');
noAds.advertising.coverage = {complete: false};
let result = totals([good, missing, noAds]);
for (const i of [2, 3, 5, 6]) {
    assert.equal(result[i].problemCount, 1);
    assert.equal(result[i].missingPurchaseCount, 1);
    assert.match(result[i].title, /Проблемных артикулов: 1/);
    assert.match(result[i].title, /Без закупа: 1/);
}
assert.equal(result[4].problemCount, 0); // Costs do not affect turnover.
assert.equal(result[8].problemCount, 1);
assert.equal(result[9].problemCount, 1);
assert.equal(result[10].problemCount, 1);
assert.equal(result[7].problemCount, 0); // DRR has its own coverage.
assert.equal(result[3].value, null);
assert.equal(result[5].value, 240);

// Repeated days, messages and duplicate rows cannot multiply a problem count.
result = totals([good, missing, JSON.parse(JSON.stringify(missing))]);
assert.equal(result[5].problemCount, 1);
assert.equal(result[5].missingPurchaseCount, 1);
const otherStore = JSON.parse(JSON.stringify(missing));
otherStore.store_slug = 'second';
assert.equal(totals([missing, otherStore])[5].problemCount, 2);

// Recompute from the caller's filtered set; nothing leaks from other products.
result = totals([good]);
for (const metric of Object.values(result)) if (metric.problemCount !== undefined) assert.equal(metric.problemCount, 0);
assert.equal(result[2].missingPurchaseCount, 0);
assert.equal(result[9].value, null); // No impressions is not a loading error.
assert.equal(result[22].value, null); // No orders is not a loading error.
assert.equal(totals([])[2].problemCount, 0);

// Current purchase data must not fill or invalidate historical purchase data.
const historicalMissing = product('history');
historicalMissing.economics_7d.margin_coverage = missingDays;
historicalMissing.economics_7d.roi_coverage = missingDays;
historicalMissing.economics_7d.complete = false;
result = totals([historicalMissing]);
assert.equal(result[2].missingPurchaseCount, 0);
assert.equal(result[5].missingPurchaseCount, 1);
const currentMissing = product('today');
currentMissing.current_economics.purchase_value = null;
result = totals([currentMissing]);
assert.equal(result[2].missingPurchaseCount, 1);
assert.equal(result[5].missingPurchaseCount, 0);
const zeroPurchase = product('zero');
zeroPurchase.current_economics.purchase_value = 0;
assert.equal(totals([zeroPurchase])[2].missingPurchaseCount, 0);

const unavailable = product('unavailable');
Object.assign(unavailable.current_economics, {day_profit: null, margin: null});
unavailable.economics_7d.margin = null;
assert.equal(totals([good, unavailable])[2].problemCount, 1);
assert.equal(totals([good, unavailable])[5].problemCount, 1);
console.log('Economics problem counts: per metric, purchase dates, deduplication, filters and zero activity passed.');
