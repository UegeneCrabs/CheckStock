'use strict';
// Exercise the exact totals function served to WB/YM; no DOM or remote sources.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scope = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/economics/yandex/totals.js'), 'utf8'), scope);
const totals = scope.window.CheckStockYandexTotals;
function product(article, profit, purchase, complete) {
    return {article, current_economics: {
        margin: profit == null ? null : profit / 8, day_profit: profit, orders: 10,
        buyout_percent: 80, expected_buyouts: 8, purchase_value: purchase == null ? null : purchase / 8,
        day_purchase_value: purchase, complete, daily_complete: complete, messages: [],
    }, economics_7d: {margin: profit, purchase_value: purchase, roi_purchase_value: purchase,
        complete, margin_coverage: {complete}, messages: []}, advertising: {}, stock: {}};
}
const complete = product('complete', 100, 200, true);
const partial = product('partial', 150, 200, false);
const unavailable = product('unavailable', null, 900, false);
let result = totals([complete, partial, unavailable]);
assert.equal(result[5].value, 250);
assert.equal(result[6].value, 62.5);
assert.equal(result[3].value, 62.5);
assert.equal(result[2].value, 250 / 16);
assert.equal(result[5].partial, true);
assert.equal(result[6].partial, true);
assert.ok(result[5].messages.some(text => text.includes('unavailable')));

const unknownPurchase = product('unknown purchase', 150, null, false);
result = totals([complete, unknownPurchase]);
assert.equal(result[5].value, 250);
assert.equal(result[2].value, 250 / 16);
assert.equal(result[6].value, null);
assert.equal(result[3].value, null);

const noOrders = product('zero orders', -50, 0, true);
Object.assign(noOrders.current_economics, {margin: null, orders: 0, expected_buyouts: 0,
    purchase_value: null, complete: false, advertising_spend: 50});
result = totals([complete, noOrders]);
assert.equal(result[5].value, 50);
assert.equal(result[2].value, 50 / 8);
assert.equal(result[3].value, 25);
assert.equal(result[3].partial, false);
assert.equal(result[6].value, 25);
assert.equal(totals([noOrders])[3].value, null);

console.log('Economics totals: partial costs, ROI scope and zero orders passed.');
