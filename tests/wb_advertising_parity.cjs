const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/economics/shared/dashboard.js'), 'utf8');
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/wb_advertising_parity.json'), 'utf8'));
// Execute the actual browser functions without the dashboard's DOM startup.
const functions = ['finite', 'productTaxSystem', 'walletDiscountPercent', 'databaseCalculatorValues', 'calculatePrice'];
const context = {};
vm.createContext(context);
for (const name of functions) {
  const start = source.indexOf(`    function ${name}(`);
  assert.ok(start >= 0, `Missing browser function: ${name}`);
  const end = source.indexOf('\n    function ', start + 1);
  assert.ok(end > start);
  vm.runInContext(source.slice(start, end), context);
}

for (const fixture of cases) {
  test(`browser advertising contract: ${fixture.name}`, () => {
    const values = context.databaseCalculatorValues(fixture.product);
    const result = context.calculatePrice(fixture.product);
    assert.equal(values.advertisingRub, fixture.advertising);
    assert.equal(result.advertising, fixture.advertising);
    assert.equal(result.margin, fixture.current_margin);
    assert.equal(result.roi, fixture.current_roi);
  });
}
