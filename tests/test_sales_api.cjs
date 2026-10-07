const {test} = require('node:test');
const assert = require('node:assert/strict');
const M = require('../static/analytics/sales-api-model.js');
const dates = ['2026-09-30', '2026-10-01'];
const rows = [
    {id:'a', project:'RIMILI', category:'Дом', name:'Первый', article:'123', barcode:'000123', article_aliases:['alias123'], days:[10,4], orders:14, cancels:3, complete:true},
    {id:'b', project:'RIMILI', category:'', name:'Второй', article:'456', barcode:'000456', article_aliases:[], days:[0,0], orders:0, cancels:0, complete:true},
    {id:'c', project:'TRIS', category:'', name:'Третий', article:'123', barcode:'009999', article_aliases:[], days:[null,null], orders:null, cancels:null, complete:false},
    {id:'d', project:'TRIS', category:'Дом', name:'Четвёртый', article:'789', barcode:'00789', article_aliases:[], days:[0,null], orders:0, cancels:0, complete:false},
];
const state = changes => ({query:'', project:'', category:'', segment:'all', filters:{}, sort:null, direction:1, ...changes});
const fields = [...M.columns(dates, true), M.combined];
test('daily filters use all rows and stable IDs across view modes', () => {
    const filter = state({filters:{7:new Set(['0'])}});
    assert.deepEqual(M.filteredRows(rows, filter, fields).map(r=>r.id), ['b','d']);
    const exactFields = M.columns(dates, true);
    assert.equal(exactFields.find(c=>c.index===7).key, M.columns(dates).find(c=>c.index===7).key);
    assert.deepEqual(M.filteredRows(rows, state({filters:{2:new Set(['000123'])}}), fields).map(r=>r.id), ['a']);
});
test('zero-order segment excludes missing and incomplete data', () => {
    assert.deepEqual(M.externalRows(rows, state({segment:'zero'})).map(r=>r.id), ['b']);
    assert.deepEqual(M.externalRows(rows, state({segment:'orders'})).map(r=>r.id), ['a']);
    assert.equal(M.filterValue(rows[2], fields.find(c=>c.key==='orders')), '');
});
test('search, category and project scope work before header filters', () => {
    assert.deepEqual(M.externalRows(rows, state({query:'alias123', project:'RIMILI'})).map(r=>r.id), ['a']);
    assert.deepEqual(M.externalRows(rows, state({category:'__empty__'})).map(r=>r.id), ['b','c']);
    assert.deepEqual(M.filteredRows(rows, state({project:'TRIS', filters:{7:new Set(['0'])}}), fields).map(r=>r.id), ['d']);
});
test('totals expose missing data and sorting keeps null below zero', () => {
    const orders = fields.find(c=>c.key==='orders');
    assert.deepEqual(M.total(rows, orders), {value:14, partial:true});
    assert.deepEqual(M.total([rows[2]], orders), {value:null, partial:true});
    assert.deepEqual(M.total([rows[0],rows[1]], orders), {value:14, partial:false});
    for(const direction of [-1,1]) assert.equal(M.filteredRows(rows,state({sort:5,direction}),fields).at(-1).id,'c');
});
test('daily keys and filter IDs support year-long periods without shifting across months', () => {
    const full = M.columns(Array.from({length:366},(_,i)=>'day'+i), true);
    assert.equal(full.length,373); assert.equal(full.at(-1).index,372);
    assert.equal(full.at(-1).key,'day365');
    assert.deepEqual(M.columns(dates,true).slice(-2).map(c=>c.key),dates);
});
test('CSV retains unknown values, barcodes, orders and protects formula strings', () => {
    assert.equal(M.csvCell(M.value(rows[2],fields.find(c=>c.key==='orders'))),'""');
    assert.equal(M.csvCell(rows[0].barcode),'"000123"');
    assert.equal(M.csvCell('=SUM(A1)'), '"\'=SUM(A1)"');
    assert.equal(M.csvCell(14),'"14"');
    assert.equal(M.csvCell('a"b'),'"a""b"');
});
