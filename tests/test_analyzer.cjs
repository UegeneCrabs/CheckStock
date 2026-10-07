const assert = require('node:assert/strict');
const test = require('node:test');
const M = require('../static/analytics/analyzer-model.js');
const row = (id, extras = {}) => ({id, name: 'Товар ' + id, article: id, barcode: '00' + id, barcodes: [], store_slug: 'rimili', project: 'RIMILI', manager: 'Анна', code: '', roi: null, notes: [[], [], [], [], [], [], []], week: [null, 0, 5, null, null, null, null], weights: {}, ...extras});
const state = extras => ({query: '', project: '', manager: '', segment: 'all', filters: {}, sort: null, direction: 1, ...extras});
const field = key => M.fields.find(column => column.key === key);

test('all fields including actual turnover have unique filter identifiers', () => {
    assert.equal(M.fields.length, 56);
    assert.equal(M.combined.index, 56);
    const indices = [...M.fields, M.combined].map(column => column.index);
    assert.equal(new Set(indices).size, 57);
    assert.deepEqual(indices, Array.from({length: 57}, (_, index) => index));
    assert.equal(field('fact').index, 45);
    assert.equal(field('stock').index, 7);
    assert.equal(field('code').index, 6);
    assert.equal(field('note6').index, 55);
});

test('combined view pins product and code beside it with the actual product width', () => {
    const columns = M.displayColumns(new Set(Object.keys(M.groups)), false, 360);
    assert.deepEqual(columns.slice(0, 2).map(column => [column.key, column.frozenLeft]), [['product', 0], ['code', 360]]);
    assert.equal(columns[0].width, 360);
    assert.equal(columns[0].index, M.combined.index);
    assert.equal(columns[1].index, field('code').index);
    assert.equal(columns.filter(column => column.key === 'code').length, 1);
    assert.ok(!columns.some(column => ['name', 'barcode', 'article', 'image'].includes(column.key)));
    assert.ok(columns.slice(2).every(column => column.frozenLeft === undefined));
    assert.equal(M.combined.width, 310);
    assert.equal(field('code').frozenLeft, undefined);
});

test('separate view pins name and code while retaining each field identity', () => {
    const columns = M.displayColumns(new Set(Object.keys(M.groups)), true);
    assert.equal(columns.length, M.fields.length);
    assert.deepEqual(columns.slice(0, 2).map(column => [column.key, column.frozenLeft]), [['name', 0], ['code', field('name').width]]);
    assert.equal(columns[0].index, field('name').index);
    assert.equal(columns[1].index, field('code').index);
    assert.deepEqual(columns.map(column => column.index).sort((a, b) => a - b), M.fields.map(column => column.index));
    assert.ok(columns.slice(2).every(column => column.frozenLeft === undefined));
    const metricsOnly = M.displayColumns(new Set(['base', 'turnover']));
    assert.ok(metricsOnly.every(column => ['base', 'turnover'].includes(column.group) && column.frozenLeft === undefined));
});

test('code and turnover header filters retain their binding when columns move', () => {
    const rows = [row('1', {code: 'NEW', fact: 0}), row('2', {code: 'NEW', fact: 100}), row('3', {code: 'OLD', fact: 0})];
    for (const separate of [false, true]) {
        const columns = M.displayColumns(new Set(Object.keys(M.groups)), separate);
        const code = columns.find(column => column.key === 'code');
        const fact = columns.find(column => column.key === 'fact');
        assert.equal(code.index, field('code').index);
        assert.equal(fact.index, field('fact').index);
        assert.deepEqual(M.tableRows(rows, state({filters: {[code.index]: new Set(['NEW']), [fact.index]: new Set(['0'])}})).map(product => product.id), ['1']);
    }
});
test('filters apply to full dataset before pagination, combine columns, retain zero', () => {
    const rows = Array.from({length: 150}, (_, i) => row(String(i), {manager: i < 100 ? 'Анна' : 'Иван'}));
    const col = M.fields.find(c => c.key === 'day1');
    const found = M.tableRows(rows, state({filters: {[col.index]: new Set(['0']), 0: new Set(['Иван'])}}));
    assert.equal(found.length, 50);
    assert.equal(found[0].id, '100');
});
test('text search includes barcode aliases; negative excludes missing ROI', () => {
    const rows = [row('1', {roi: null}), row('2', {roi: -5, barcodes: ['ABC']}), row('3', {roi: 0})];
    assert.deepEqual(M.tableRows(rows, state({segment: 'negative'})).map(r => r.id), ['2']);
    assert.deepEqual(M.filter(rows, state({query: 'abc'})).map(r => r.id), ['2']);
});
test('sorting keeps unknown numbers last and percentages use weighted totals', () => {
    const stockCol = M.fields.find(c => c.key === 'stock');
    const rows = [row('1', {stock: null}), row('2', {stock: 2}), row('3', {stock: 100})];
    assert.deepEqual(M.tableRows(rows, state({sort: stockCol.index, direction: -1})).map(r => r.id), ['3', '2', '1']);
    const drrCol = M.fields.find(c => c.key === 'drr');
    assert.equal(M.summary([row('1', {weights: {spend: 100, boughtAmount: 1000}}), row('2', {weights: {spend: 0, boughtAmount: 9000}})], drrCol), 1);
});

test('store turnover groups by cabinet, sums known amounts and weights only paired products', () => {
    const rows = [
        row('1', {project: 'Магазин', plan: 100, fact: 20, forecast: 20, difference: 80, deviation: 80}),
        row('2', {project: 'Магазин', plan: 900, fact: 300, forecast: 300, difference: 600, deviation: 600 / 900 * 100}),
        row('3', {project: 'Магазин', plan: 500, fact: null, forecast: null, difference: null, deviation: null}),
        row('4', {project: 'Магазин', plan: null, fact: 100, forecast: 100, difference: 50, deviation: null}),
        row('5', {store_slug: 'tris', project: 'Магазин', plan: 200, fact: 150, forecast: 150, difference: 50, deviation: 25}),
    ];
    const totals = M.storeTurnover(rows);
    assert.equal(totals.length, 2);
    assert.deepEqual(totals.find(store => store.store === 'rimili'), {
        store: 'rimili', project: 'Магазин', plan: 1500, fact: 420, forecast: 420, difference: 680, deviation: 68,
        partial: {plan: true, fact: true, forecast: true, difference: true, deviation: true},
    });
    assert.deepEqual(totals.find(store => store.store === 'tris'), {
        store: 'tris', project: 'Магазин', plan: 200, fact: 150, forecast: 150, difference: 50, deviation: 25,
        partial: {plan: false, fact: false, forecast: false, difference: false, deviation: false},
    });
    assert.equal(M.summary(rows.slice(0, 4), field('deviation')), 68);
});

test('store turnover distinguishes unknown values, actual zero and unavailable percentages', () => {
    const unknown = M.storeTurnover([row('1', {plan: null, fact: null, forecast: null, difference: null})])[0];
    for (const key of ['plan', 'fact', 'forecast', 'difference', 'deviation']) {
        assert.equal(unknown[key], null);
        assert.equal(unknown.partial[key], true);
    }
    const zero = M.storeTurnover([row('1', {plan: 0, fact: 0, forecast: 0, difference: 0})])[0];
    for (const key of ['plan', 'fact', 'forecast', 'difference']) {
        assert.equal(zero[key], 0);
        assert.equal(zero.partial[key], false);
    }
    assert.equal(zero.deviation, null);
    assert.equal(zero.partial.deviation, false);
    const unpaired = M.storeTurnover([row('1', {plan: 100, difference: null}), row('2', {plan: null, difference: 20})])[0];
    assert.equal(unpaired.deviation, null);
    assert.equal(unpaired.partial.deviation, true);
    assert.deepEqual(M.storeTurnover([]), []);
});

test('turnover values and coefficient one retain supplied units, signs, zero and null', () => {
    const product = row('1', {coeff: 1, fact: 123.45, plan: 100, forecast: 123.45, difference: -23.45, deviation: -23.45});
    for (const key of ['coeff', 'fact', 'plan', 'forecast', 'difference', 'deviation']) {
        assert.equal(M.value(product, field(key)), product[key]);
        assert.equal(M.filterValue(product, field(key)), String(product[key]));
    }
    assert.equal(M.summary([product], field('coeff')), null);
    for (const key of ['fact', 'plan', 'forecast', 'difference']) {
        assert.ok(Math.abs(M.summary([product], field(key)) - product[key]) < 1e-10);
        assert.equal(M.value(row('zero', {[key]: 0}), field(key)), 0);
        assert.equal(M.value(row('unknown', {[key]: null}), field(key)), null);
        assert.equal(M.filterValue(row('unknown', {[key]: null}), field(key)), '');
    }
});

test('turnover counts funnel article once while adding size targets in both summaries', () => {
    const products = [
        row('s', {article: '123 / S', plan: 100, fact: 80, forecast: 80, difference: 20, deviation: 20}),
        row('m', {article: '123 / M', plan: 200, fact: 80, forecast: 80, difference: 120, deviation: 60}),
        row('other', {article: '456', plan: 100, fact: 50, forecast: 50, difference: 50, deviation: 50}),
    ];
    const before = structuredClone(products);
    const store = M.storeTurnover(products)[0];
    const expected = {plan: 400, fact: 130, forecast: 130, difference: 270, deviation: 67.5};
    for (const [key, value] of Object.entries(expected)) {
        assert.equal(store[key], value);
        assert.equal(M.summary(products, field(key)), value);
        assert.equal(store.partial[key], false);
    }
    assert.deepEqual(products, before);
    assert.equal(M.value(products[0], field('difference')), 20);
});

test('unknown target for one size excludes the whole article from difference and deviation', () => {
    const products = [
        row('s', {article: '123 / S', plan: 100, fact: 80, forecast: 80, difference: 20}),
        row('m', {article: '123 / M', plan: null, fact: 80, forecast: 80, difference: null}),
        row('complete', {article: '456', plan: 200, fact: 150, forecast: 150, difference: 50}),
    ];
    const store = M.storeTurnover(products)[0];
    assert.deepEqual(store, {
        store: 'rimili', project: 'RIMILI', plan: 300, fact: 230, forecast: 230, difference: 50, deviation: 25,
        partial: {plan: true, fact: false, forecast: false, difference: true, deviation: true},
    });
    assert.equal(M.summary(products, field('difference')), 50);
    assert.equal(M.summary(products, field('deviation')), 25);
    assert.equal(M.summary(products.slice(0, 2), field('difference')), null);
    assert.equal(M.summary(products.slice(0, 2), field('deviation')), null);
});

test('same nmID in separate cabinets remains separate, with a project fallback for missing slug', () => {
    const products = [
        row('r-s', {article: '123 / S', plan: 100, fact: 80, forecast: 80}),
        row('r-m', {article: '123 / M', plan: 200, fact: 80, forecast: 80}),
        row('t-s', {store_slug: 'tris', project: 'RIMILI', article: '123 / S', plan: 100, fact: 40, forecast: 40}),
    ];
    assert.equal(M.storeTurnover(products).length, 2);
    assert.equal(M.summary(products, field('fact')), 120);
    assert.equal(M.summary(products, field('forecast')), 120);
    assert.equal(M.summary(products, field('difference')), 280);
    assert.equal(M.summary(products, field('deviation')), 70);
    const fallback = [
        row('a', {store_slug: null, project: 'Первый', article: '123 / S', plan: 100, fact: 80, forecast: 80}),
        row('b', {store_slug: null, project: 'Первый', article: '123 / M', plan: 200, fact: 80, forecast: 80}),
        row('c', {store_slug: null, project: 'Второй', article: '123', plan: 100, fact: 40, forecast: 40}),
    ];
    assert.equal(M.storeTurnover(fallback).length, 2);
    assert.equal(M.summary(fallback, field('fact')), 120);
    assert.equal(M.summary(fallback, field('difference')), 280);
});

test('conflicting shared turnover becomes unknown independent of row ordering', () => {
    const products = [
        row('s', {article: '123 / S', plan: 100, fact: 80, forecast: 80}),
        row('m', {article: '123 / M', plan: 200, fact: 90, forecast: 90}),
    ];
    for (const order of [products, [...products].reverse()]) {
        const store = M.storeTurnover(order)[0];
        assert.equal(store.plan, 300);
        for (const key of ['fact', 'forecast', 'difference', 'deviation']) {
            assert.equal(store[key], null);
            assert.equal(store.partial[key], true);
            assert.equal(M.summary(order, field(key)), null);
        }
    }
    const conflictingFact = products.map(product => ({...product, forecast: 80}));
    assert.equal(M.storeTurnover(conflictingFact)[0].fact, null);
    assert.equal(M.summary(conflictingFact, field('deviation')), null);
});
test('notes are filterable, unavailable forecast stays empty, CSV text cannot become a formula', () => {
    const product = row('1', {notes: [[{note: 'Проверено'}, {note: 'Готово'}]]});
    assert.equal(M.value(product, M.fields.find(c => c.key === 'note0')), 'Проверено\nГотово');
    assert.equal(M.summary([product], M.fields.find(c => c.key === 'forecast')), null);
    assert.equal(M.csvCell('=1+1'), '"\'=1+1"');
    assert.equal(M.csvCell(-5), '"-5"');
    assert.equal(M.csvCell('00123'), '"00123"');
});
