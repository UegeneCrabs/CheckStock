// Small regression checks for report-only layout rules.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../rbe-yandex-site');
const css = fs.readFileSync(path.join(root, 'static/site.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
assert.match(css, /#error:empty\s*\{\s*display:\s*none;/);
assert.match(html, /<p id="error" role="alert"><\/p>/); // Nonempty errors remain available.
assert.match(css, /td\.status-cell\s*\{[^}]*white-space:\s*nowrap;/);
assert.match(css, /\.status-cell \.repair\s*\{[^}]*height:\s*24px;[^}]*margin:\s*0 0 0 6px;/);
assert.match(html, /site\.css\?v=20261016-compact-status/);
console.log('Report compact layout checks passed');
assert.match(css, /\.report-head \.sync-tools > summary\s*\{\s*height:\s*35px;/);
assert.match(css, /@media \(min-width: 900px\)\s*\{\s*\.report-head \.filters\s*\{\s*flex-wrap:\s*nowrap;/);
assert.match(css, /\.report-head \.range-wrap\s*\{\s*flex:\s*1 1 290px;\s*min-width:\s*180px;/);
console.log('Desktop toolbar alignment checks passed');
