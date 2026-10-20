const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const window = {};
const source = readFileSync(path.join(__dirname, '../static/common/report-daily.js'), 'utf8');
vm.runInNewContext(source, { window });
const daily = window.CheckStockReportDaily;

test('dates round-trip and impossible dates are rejected', () => {
    assert.equal(daily.displayDate('2026-10-20'), '20.10.2026');
    assert.equal(daily.isoDate('20.10.2026'), '2026-10-20');
    assert.equal(daily.isoDate('31.02.2026'), null);
});

test('missing days break a chart line without hiding valid zeroes', () => {
    const days = [{ value: 4.4 }, { value: null }, { value: 0 }, { value: 4.2 }];
    const segments = daily.segments(days, (day) => day.value, 0, days.length - 1);
    assert.deepEqual(
        Array.from(segments, (segment) => Array.from(segment, (point) => [point.index, point.value])),
        [[[0, 4.4]], [[2, 0], [3, 4.2]]],
    );
});
