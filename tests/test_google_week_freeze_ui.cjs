// Local browser-behavior checks with a small DOM double; no server or Google connection.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/integrations/google-week-update.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup() {
    const calls = [];
    const document = {hidden: false, activeElement: null};
    class Element {
        constructor(tag, attrs = {}) {
            this.tag = tag; this.children = []; this.attrs = attrs; this.dataset = {};
            this.listeners = {}; this._text = ''; this.name = attrs.name || '';
            this.type = attrs.type || ''; this.value = attrs.value || ''; this.checked = false;
            this.disabled = false; this.hidden = false; this.scrollTop = 0;
            const classes = new Set();
            this.classList = {add: v => classes.add(v), remove: v => classes.delete(v),
                contains: v => classes.has(v), toggle: (v, on) => on ? classes.add(v) : classes.delete(v)};
            for (const [key, value] of Object.entries(attrs)) {
                if (key.startsWith('data-')) this.dataset[camel(key.slice(5))] = value;
            }
        }
        set textContent(value) {this._text = value; this.children = [];}
        get textContent() {return this._text + this.children.map(child => child.textContent).join('');}
        set innerHTML(value) {this.markup = value; this.children = [];}
        append(...nodes) {nodes.forEach(node => {node.parent = this; this.children.push(node);});}
        replaceChildren() {this.children = [];}
        querySelectorAll(selector) {
            const out = [];
            for (const child of this.children) {
                if (selector.split(',').some(s => matches(child, s.trim()))) out.push(child);
                out.push(...child.querySelectorAll(selector));
            }
            return out;
        }
        querySelector(selector) {return this.querySelectorAll(selector)[0] || null;}
        addEventListener(name, callback) {(this.listeners[name] ||= []).push(callback);}
        dispatch(name, target = this) {
            for (const fn of this.listeners[name] || []) fn({target, preventDefault() {}});
            if (this.parent) this.parent.dispatch(name, target);
        }
        setAttribute(key, value) {this.attrs[key] = value;}
        removeAttribute(key) {delete this.attrs[key];}
        focus() {document.activeElement = this;}
        closest() {return null;}
    }
    function camel(value) {return value.replace(/-([a-z])/g, (_, char) => char.toUpperCase());}
    function matches(node, selector) {
        const found = selector.match(/^(\w+)?(?:\[([\w-]+)(?:="([^"]*)")?\])?$/);
        if (!found) throw new Error('Unsupported test selector: ' + selector);
        const [, tag, attr, value] = found;
        if (tag && tag !== node.tag) return false;
        if (!attr) return true;
        let actual = attr.startsWith('data-') ? node.dataset[camel(attr.slice(5))] : node[attr] ?? node.attrs[attr];
        return value === undefined ? actual !== undefined && actual !== '' && actual !== false : String(actual) === value;
    }
    const form = new Element('form', {'data-week-update-form': '1'});
    const add = (tag, attrs) => {const element = new Element(tag, attrs); form.append(element); return element;};
    const by = name => form.querySelector('[data-' + name + ']');
    const field = (name, value, type = 'text') => add('input', {name, value, type});
    field('spreadsheet_url', 'https://docs.google.com/spreadsheets/d/doc/edit');
    field('sheet_name', 'Orders'); field('cells', 'F4'); field('run_time', '00:05');
    add('select', {name: 'weekday', value: '0'});
    field('freeze_sheet_ids', '[11]', 'hidden'); field('freeze_selection_initialized', '1', 'hidden');
    field('freeze_spreadsheet_id', 'doc', 'hidden'); field('freeze_enabled', '1', 'checkbox');
    const catalog = {spreadsheet_id: 'doc', sheets: [{sheet_id: 11, title: 'Сток'}, {sheet_id: 12, title: 'Прогноз'}],
        selected_sheet_ids: [11], selection_initialized: true, updated_at: '2026-10-06T10:00:00+03:00', last_error: null, refresh_due: false};
    add('div', {'data-freeze-catalog': '1', 'data-catalog': JSON.stringify(catalog)});
    for (const name of ['week-run', 'week-search', 'week-sales', 'week-stock', 'week-freeze', 'week-freeze-preview',
        'freeze-refresh', 'freeze-select-all', 'freeze-clear-found', 'freeze-clear']) add('button', {['data-' + name]: '1'});
    add('button', {type: 'submit'});
    add('input', {type: 'search', 'data-freeze-search': '1'});
    for (const name of ['week-status', 'week-search-status', 'week-sales-status', 'week-stock-status', 'week-freeze-status',
        'week-unsaved', 'week-save-status', 'freeze-catalog-status', 'freeze-selected-count', 'freeze-options', 'freeze-empty',
        'freeze-selection-hint', 'week-value', 'week-schedule', 'week-search-schedule', 'week-search-results',
        'week-sales-schedule', 'week-sales-results', 'week-stock-schedule', 'week-stock-results', 'week-freeze-schedule',
        'week-freeze-results', 'week-sheet-link']) add('div', {['data-' + name]: '1'});
    for (const name of ['week-search-panel', 'week-sales-report', 'week-stock-report', 'week-freeze-report']) add('details', {['data-' + name]: '1'});
    document.querySelector = selector => selector === '[data-week-update-form]' ? form : null;
    document.createElement = tag => new Element(tag);
    class FormData {
        constructor(form) {this.fields = new Map();
            if (form) form.querySelectorAll('input[name], select[name]').forEach(input => {
                if (!input.disabled && (input.type !== 'checkbox' || input.checked)) this.fields.set(input.name, input.value);
            });
        }
        get(name) {return this.fields.get(name);}
    }
    const state = {ok: true, value: 'W40 2026', status_text: 'Ready', schedule_text: 'Schedule', sheet_catalog: catalog,
        freeze_enabled: false};
    for (const kind of ['search', 'sales', 'stock', 'freeze']) Object.assign(state, {
        [kind + '_html']: '<p>Report</p>', [kind + '_status_text']: 'Ready', [kind + '_schedule_text']: 'Schedule',
    });
    const env = {reply: () => ({...state}), interval: null};
    const fetch = async (url, options) => {calls.push({url, options});
        const body = await env.reply(url, options);
        return {ok: true, json: async () => body};
    };
    vm.runInNewContext(source, {document, window: {setInterval: callback => {env.interval = callback;}},
        fetch, FormData, Set, WeakMap, Date, JSON, Array, Number});
    return {form, by, calls, env, state, catalog, document,
        input: name => form.querySelector('[name="' + name + '"]'),
        selected: () => JSON.parse(form.querySelector('[name="freeze_sheet_ids"]').value)};
}

test('search does not dirty the form; select/remove affect only filtered sheets and disable runs until saved', async () => {
    const ui = setup();
    ui.by('freeze-search').value = 'Прогноз'; ui.by('freeze-search').dispatch('input');
    assert.equal(ui.by('week-run').disabled, false);
    ui.by('freeze-select-all').dispatch('click');
    assert.deepEqual(ui.selected(), [11, 12]);
    assert.equal(ui.by('week-freeze').disabled, true);
    ui.by('freeze-clear-found').dispatch('click');
    assert.deepEqual(ui.selected(), [11]);
    assert.equal(ui.by('week-run').disabled, false);
    ui.by('freeze-clear').dispatch('click');
    assert.deepEqual(ui.selected(), []);
    ui.env.reply = () => ({...ui.state, sheet_catalog: {...ui.catalog, selected_sheet_ids: []}});
    ui.form.dispatch('submit'); await tick();
    assert.equal(ui.calls[0].options.body.get('freeze_sheet_ids'), '[]');
    assert.equal(ui.calls[0].options.body.get('freeze_selection_initialized'), '1');
    assert.equal(ui.by('week-run').disabled, false);
    assert.equal(ui.by('week-freeze').disabled, true);
});

test('dirty selection and search survive a catalog refresh; renamed selected ID stays selected', async () => {
    const ui = setup();
    ui.by('freeze-search').value = 'Прогноз'; ui.by('freeze-search').dispatch('input');
    ui.by('freeze-select-all').dispatch('click');
    ui.env.reply = () => ({ok: true, sheet_catalog: {...ui.catalog, sheets: [
        {sheet_id: 11, title: 'Сток переименован'}, {sheet_id: 12, title: 'Прогноз'},
    ]}});
    ui.by('freeze-refresh').dispatch('click');
    assert.equal(ui.by('freeze-search').disabled, true);
    await tick();
    assert.deepEqual(ui.selected(), [11, 12]);
    assert.equal(ui.by('freeze-search').value, 'Прогноз');
    assert.equal(ui.by('freeze-search').disabled, false);
    assert.equal(ui.by('week-run').disabled, true);
    assert.match(ui.by('freeze-options').textContent, /Сток переименован/);
});

test('polling preserves search/focus and skips unsaved edits; removed sheets remain deselectable', async () => {
    const ui = setup();
    const search = ui.by('freeze-search'); search.value = 'сток'; search.focus(); search.dispatch('input');
    await ui.env.interval();
    assert.equal(search.value, 'сток');
    assert.equal(ui.document.activeElement, search);
    ui.env.reply = () => ({...ui.state, sheet_catalog: {...ui.catalog, sheets: [{sheet_id: 12, title: 'Прогноз'}]}});
    await ui.env.interval();
    assert.deepEqual(ui.selected(), [11]);
    assert.match(ui.by('freeze-selection-hint').textContent, /нет в текущем списке/);
    search.value = ''; search.dispatch('input');
    ui.by('freeze-clear').dispatch('click');
    const before = ui.calls.length;
    await ui.env.interval();
    assert.equal(ui.calls.length, before);
    assert.deepEqual(ui.selected(), []);
});

test('changing document clears old selection and disables the schedule using the saved response', async () => {
    const ui = setup();
    ui.input('freeze_enabled').checked = true;
    ui.input('spreadsheet_url').value = 'https://docs.google.com/spreadsheets/d/other/edit';
    ui.input('spreadsheet_url').dispatch('input');
    assert.equal(ui.by('freeze-refresh').disabled, true);
    ui.env.reply = () => ({...ui.state, freeze_enabled: false, sheet_catalog: {...ui.catalog,
        spreadsheet_id: 'other', sheets: [], selected_sheet_ids: [], selection_initialized: false}});
    ui.form.dispatch('submit'); await tick();
    assert.deepEqual(ui.selected(), []);
    assert.equal(ui.input('freeze_spreadsheet_id').value, 'other');
    assert.equal(ui.input('freeze_enabled').checked, false);
    assert.equal(ui.by('week-run').disabled, false);
    assert.equal(ui.by('week-freeze').disabled, true);
});

test('preview survives status polling until the week or saved selection changes', async () => {
    const ui = setup();
    ui.env.reply = () => ({...ui.state, freeze_result: {dry_run: true}, freeze_html: '<p>Preview ranges</p>'});
    ui.by('week-freeze-preview').dispatch('click'); await tick();
    assert.equal(ui.calls[0].url, '/admin/google-week-update/freeze/preview');
    assert.equal(ui.by('week-freeze-results').markup, '<p>Preview ranges</p>');
    ui.env.reply = () => ({...ui.state});
    await ui.env.interval();
    assert.equal(ui.by('week-freeze-results').markup, '<p>Preview ranges</p>');
    ui.env.reply = () => ({...ui.state, value: 'W41 2026'});
    await ui.env.interval();
    assert.equal(ui.by('week-freeze-results').markup, '<p>Report</p>');
});
