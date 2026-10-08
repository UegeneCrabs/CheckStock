const test = require('node:test');
const assert = require('node:assert/strict');
const sales = require('../static/analytics/sales-api-model.js');
require('../static/analytics/ephemerides-model.js');
const ephemerides = globalThis.EphemeridesModel;

test('today column keeps stable IDs through year-long periods and display modes', () => {
    for (const separate of [true, false]) {
        const fields = sales.columns(Array.from({length:366},(_,i)=>`day${i}`), separate);
        assert.equal(new Set(fields.map(c=>c.index)).size, fields.length);
        assert.equal(fields.find(c=>c.key==='day365').index, 372);
        assert.equal(fields.find(c=>c.key==='today_turnover').index, sales.turnover.index);
    }
});

test('today sorting respects zero and unknown, independent of period totals', () => {
    const rows = [null,0,100,500].map((amount,i)=>({id:i,today_turnover:amount,orders:100-i,complete:false,article_aliases:[],days:[null]}));
    const state = {query:'',segment:'all',filters:{},sort:sales.turnover.index,direction:-1};
    assert.deepEqual(sales.filteredRows(rows,state,sales.columns(['past'])).map(r=>r.today_turnover),[500,100,0,null]);
    assert.deepEqual(sales.total(rows.slice(1),sales.turnover),{value:600,partial:false});
    state.sort=5;
    assert.deepEqual(sales.filteredRows(rows,state,sales.columns(['past'])).map(r=>r.orders),[100,99,98,97]);
});

test('ephemerides today total counts sizes once and keeps stores separate', () => {
    const rows = [
        {store_slug:'a',article:'1 / S',dayTurnover:{fact:100}},
        {store_slug:'a',article:'1 / M',dayTurnover:{fact:100}},
        {store_slug:'b',article:'1',dayTurnover:{fact:50}},
    ];
    assert.equal(ephemerides.summary(rows,'dayTurnover','fact'),150);
    assert.equal(ephemerides.summary(rows,undefined,'product'),null);
});
