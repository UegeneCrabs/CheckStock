const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '../static/common/report-select.js'), 'utf8');

function setup(filterAttribute) {
    const created = [];
    const makeElement = () => ({
        setAttribute() {}, addEventListener() {}, append() {},
    });
    const container = {
        hasAttribute: (name) => name === filterAttribute,
        querySelector: () => ({ textContent: 'Магазин' }),
        append() {}, addEventListener() {},
    };
    const select = {
        id: 'store', selectedOptions: [{ textContent: 'Все' }],
        closest: () => container, addEventListener() {},
    };
    const document = {
        createElement: () => { const element = makeElement(); created.push(element); return element; },
        addEventListener() {},
    };
    const window = {};
    vm.runInNewContext(source, { document, window });
    window.CheckStockReportSelect(select);
    return created;
}

test('searchable filter keeps SPP styles for the existing report', () => {
    const [control, , toggle, list] = setup('data-spp-filter');
    assert.equal(control.className, 'spp-filter__control');
    assert.equal(toggle.className, 'spp-filter__toggle');
    assert.equal(list.className, 'spp-filter__options');
});

test('searchable filter uses neutral WB report styles for rating', () => {
    const [control, , toggle, list] = setup('data-wb-filter');
    assert.equal(control.className, 'wb-filter__control');
    assert.equal(toggle.className, 'wb-filter__toggle');
    assert.equal(list.className, 'wb-filter__options');
});
