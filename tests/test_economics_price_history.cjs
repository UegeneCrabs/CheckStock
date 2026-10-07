'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../static/economics/shared/dashboard.js'), 'utf8');
const lineSource = source.slice(source.indexOf('        function line(items,'), source.indexOf('        var renderedLines ='));
const pathSource = source.slice(source.indexOf('    function chartPath('), source.indexOf('    function renderChartDailySales('));
const finiteSource = source.slice(source.indexOf('    function finite('), source.indexOf('    function negativeValueClass('));

function render(prices) {
    const calls = [];
    const scope = {window: {CheckStockUI: {render: (name, args) => {
        calls.push({name: name.split('/').at(-1), ...args});
        return name;
    }}}, x: index => index * 10};
    vm.runInNewContext(finiteSource + pathSource + lineSource + '\nthis.line = line;', scope);
    scope.line(prices.map(price => ({price})), 'price', value => value, 'is-price', 'price');
    return calls.filter(call => call.name.startsWith('visual'));
}

test('price changes use each daily value with a visible line between adjacent observations', () => {
    const result = render([700, 800, 750]);
    assert.equal(result.length, 1);
    assert.equal(result[0].path, 'M0.0 700.0 L10.0 800.0 L20.0 750.0');
});
test('missing days break the price line, including a single isolated observation', () => {
    const result = render([null, 700, undefined, 720, 760, null]);
    assert.equal(result.length, 2);
    assert.equal(result[0].name, 'visual');
    assert.equal(result[0].content, 10);
    assert.equal(result[0].content_2, 700);
    assert.equal(result[1].path, 'M30.0 720.0 L40.0 760.0');
});
test('an absent price history has no artificial zero line', () => {
    assert.equal(render([null, undefined, '', NaN]).length, 0);
});

test('price series uses the ruble axis rather than order, stock or percentage scales', () => {
    const calls = [];
    const chart = {querySelectorAll: () => [], innerHTML: ''};
    const scope = {
        window: {CheckStockUI: {render: (name, args) => {
            calls.push({name: name.split('/').at(-1), ...args});
            return name;
        }}},
        nodes: {chart, chartWrap: {querySelector: () => null}},
        config: {}, chartPreferences: {series: ['price'], compare: false},
        integer: new Intl.NumberFormat('ru-RU'), decimal: new Intl.NumberFormat('ru-RU'),
        preciseMoney: new Intl.NumberFormat('ru-RU', {style: 'currency', currency: 'RUB'}),
        renderChartDailySales: () => {},
    };
    const renderSource = source.slice(source.indexOf('    function renderChart(product)'), source.indexOf('    function showChartSeriesTooltip('));
    vm.runInNewContext(finiteSource + pathSource + renderSource + '\nthis.renderChart = renderChart;', scope);
    scope.renderChart({history: [100, 200].map(price => ({
        price_with_spp_rub: price, orders_count: 3000, stock_units: 50000, drr_percent: 700,
        margin_rub: 15000, advertising_rub: 5000,
    }))});
    assert.equal(calls.find(call => call.name === 'grid').value, '200');
    const output = calls.find(call => call.name === 'render-chart');
    assert.match(output.content, />₽<\/text>/);
    assert.ok(output.hit_4.includes('hit-2'));
    assert.equal(calls.filter(call => call.name === 'visual-2').length, 1);
    assert.equal(calls.find(call => call.name === 'visual-2').className, 'is-price');
});
