const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/agents/google-sheets.gs'), 'utf8');

function setup(pages) {
  const state = {calls: [], writes: [], cleared: false, released: false};
  const sheet = {
    getMaxRows: () => 1000, getMaxColumns: () => 100,
    clearContents: () => {state.cleared = true;}, setFrozenRows: () => {},
    getRange: (...range) => ({
      setValue: value => state.writes.push({range, value}),
      setValues: value => state.writes.push({range, value}),
      setNumberFormat() {return this;},
    }),
  };
  const context = vm.createContext({
    LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {state.released = true;}})},
    PropertiesService: {getScriptProperties: () => ({getProperties: () => ({
      API_BASE_URL: 'https://example.com', API_TOKEN: 'test-key', API_STORE: 'gogol', API_REPORT: 'orders',
      API_PARAMS: '{"date_from":"2026-09-24","date_to":"2026-09-24"}',
    })})},
    UrlFetchApp: {fetch: (url, options) => {
      state.calls.push({url, options});
      const page = pages.shift();
      return {getResponseCode: () => page.code || 200, getContentText: () => JSON.stringify(page)};
    }},
    SpreadsheetApp: {getActiveSpreadsheet: () => ({getSheetByName: () => sheet})},
  });
  vm.runInContext(source, context);
  return {state, run: () => context.refreshCheckStock()};
}

test('loads every page and escapes external formulas while preserving zero', () => {
  const {state, run} = setup([
    {rows: [{article: '001', name: '=IMPORTXML("x")', quantity: 0}], next_offset: 100, warnings: ['partial']},
    {rows: [{article: '002', name: 'Product', quantity: null}], next_offset: null},
  ]);
  run();
  assert.equal(state.calls.length, 2);
  assert.match(state.calls[1].url, /offset=100/);
  assert.equal(state.calls[0].options.headers.Authorization, 'Bearer test-key');
  const data = state.writes.find(w => w.range[0] === 5).value;
  assert.equal(data[0][0], '001');
  assert.equal(data[0][1][0], "'");
  assert.equal(data[0][2], 0);
  assert.equal(data[1][2], '');
  assert.equal(state.released, true);
});

test('failure on a later page preserves existing sheet contents', () => {
  const {state, run} = setup([{rows: [{article: 'a'}], next_offset: 100}, {code: 503}]);
  assert.throws(run, /HTTP 503/);
  assert.equal(state.cleared, false);
  assert.equal(state.writes.length, 0);
  assert.equal(state.released, true);
});

test('invalid pagination stops before replacing data', () => {
  const {state, run} = setup([{rows: [], next_offset: 0}]);
  assert.throws(run, /пагинация/);
  assert.equal(state.cleared, false);
});
