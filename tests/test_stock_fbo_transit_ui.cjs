const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

class Element {
    constructor(tag = 'div') {
        this.tag = tag;
        this.children = [];
        this.attrs = {};
        this.handlers = {};
        this.value = '';
        this.checked = false;
        this.dataset = {};
        this.classes = new Set();
        this.classList = {
            add: (name) => this.classes.add(name),
            remove: (name) => this.classes.delete(name),
            toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name),
        };
    }
    appendChild(child) { this.children.push(child); return child; }
    set innerHTML(value) { this.children = []; this.textContent = value; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    removeAttribute(name) { delete this.attrs[name]; }
    getAttribute(name) { return this.attrs[name]; }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    fire(name) { this.handlers[name]?.({ preventDefault() {} }); }
    all() { return this.children.flatMap((child) => [child, ...child.all()]); }
    querySelectorAll(selector) {
        return this.all().filter((node) => selector.startsWith('[')
            ? Object.hasOwn(node.attrs, selector.slice(1, -1))
            : node.classes.has(selector.slice(1)));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest() { return null; }
    focus() {}
    reset() {}
    showModal() { this.open = true; }
    close() { this.open = false; }
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const reply = (data) => Promise.resolve({ ok: data.ok !== false, json: () => Promise.resolve(data) });
function runScript(file, window, document) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), { window, document, FormData });
}

function shipmentUI(method = 'manual', setupMutation) {
    const nodes = Object.fromEntries([
        'sh-btn', 'sh-rows', 'store-layout', 'sh-ff', 'sh-mp', 'sh-note', 'sh-fbo', 'sh-trash',
        'sh-file', 'sh-url', 'sh-status', 'sh-io', 'sh-add-row',
        'sh-legacy-fbs', 'sh-legacy-fbs-option',
    ].map((id) => [id, new Element()]));
    nodes['store-layout'].dataset.store = 'rimili';
    nodes['store-layout'].dataset.mutationUser = '1';
    nodes['sh-ff'].value = 'Source';
    nodes['sh-mp'].value = 'WB';
    nodes['sh-note'].value = 'Shipment 123';
    const calls = [];
    const state = { tableRefreshes: 0, transitRefreshes: 0, allowNegative: false, message: '' };
    const editor = {
        onSourceChange() {}, reset() {}, loadSourceStock() {},
        validateRows: () => [], collectItems: () => [{ code: '001', quantity: 30 }],
        setAllowNegative: (value) => { state.allowNegative = value; },
        showServerMessage: (message) => { state.message = message; },
    };
    const window = {
        initIoBlock: () => ({ current: () => method }),
        CheckStockOperations: { initBlock: (_name, _status, callback) => callback(), requireRowEditor: () => () => editor },
        stockTable: { refresh: () => state.tableRefreshes++ },
        stockTransit: { refresh: () => state.transitRefreshes++ },
        CheckStockMutation: { hasPending: () => false, fetch: (url, options) => {
            calls.push({ url, data: Object.fromEntries(options.body) });
            return reply({ ok: true, transfer_id: 7, results: [{ article: '001', quantity: 30 }] });
        } },
    };
    const document = { getElementById: (id) => nodes[id] };
    if (setupMutation) setupMutation(window, document);
    runScript('static/stock/forms/shipment.js', window, document);
    return { nodes, calls, state, window, document };
}

function installMutation(window, document, storage, fetch) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'static/stock/mutation-request.js'), 'utf8'), {
        window, document, fetch, FormData, Blob, Headers, TextEncoder, crypto: webcrypto,
        sessionStorage: {
            getItem: (key) => storage.get(key) || null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: (key) => storage.delete(key),
        },
    });
}

async function submitShipment(ui) {
    const mutationFetch = ui.window.CheckStockMutation.fetch;
    let request;
    ui.window.CheckStockMutation.fetch = (...args) => (request = mutationFetch(...args));
    ui.nodes['sh-btn'].fire('click');
    assert.ok(request, 'the form must call the real mutation helper');
    await request.catch(() => {});
    await flush();
    ui.window.CheckStockMutation.fetch = mutationFetch;
}

for (const mode of ['shipment', 'write-off', 'FBS']) {
    const toTrash = mode === 'write-off';
    const toFbs = mode === 'FBS';
    for (const method of ['manual', 'file', 'sheet']) {
        test(`pending legacy ${mode} survives reload (${method})`, async () => {
            const storage = new Map();
            const requests = [];
            const committed = new Map();
            const fetch = async (url, options) => {
                const key = options.headers.get('Idempotency-Key');
                requests.push({ key, url, body: Object.fromEntries(options.body) });
                if (!committed.has(key)) committed.set(key, { ok: true, results: [{ article: '001', quantity: 30 }] });
                if (requests.length === 1) throw new Error('Lost response after commit');
                return new Response(JSON.stringify(committed.get(key)), { status: 200 });
            };
            const file = new File(['same Excel content'], 'shipment.xlsx');
            const sheetUrl = 'https://docs.google.com/spreadsheets/d/shipment';
            const legacy = new FormData();
            for (const [key, value] of Object.entries({
                fulfillment: 'Source', marketplace: 'WB', note: 'Shipment 123',
                to_fbs: toFbs ? '1' : '', to_trash: toTrash ? '1' : '',
            })) legacy.append(key, value);
            if (method === 'file') legacy.append('file', file);
            else if (method === 'sheet') legacy.append('sheet_url', sheetUrl);
            else legacy.append('items', JSON.stringify([{ code: '001', quantity: 30 }]));

            const oldWindow = {};
            const oldDocument = { getElementById: () => ({ dataset: { mutationUser: '1' } }) };
            installMutation(oldWindow, oldDocument, storage, fetch);
            await assert.rejects(oldWindow.CheckStockMutation.fetch('/stock/rimili/shipment', {
                method: 'POST', body: legacy,
            }), /Lost response/);
            assert.equal(storage.size, 1);

            // A fresh page/VM retains only sessionStorage, just like a reload after deployment.
            const ui = shipmentUI(method, (window, document) => installMutation(window, document, storage, fetch));
            assert.equal(ui.nodes['sh-legacy-fbs-option'].hidden, false);
            ui.nodes['sh-trash'].checked = toTrash;
            ui.nodes['sh-legacy-fbs'].checked = toFbs;
            ui.nodes['sh-file'].files = [file];
            ui.nodes['sh-url'].value = sheetUrl;

            // Neither edited data nor switching to FBO may bypass the pending request.
            const pending = [...storage.values()][0];
            ui.nodes['sh-note'].value = 'Different shipment';
            await submitShipment(ui);
            assert.match(ui.state.message, /Результат предыдущей операции неизвестен/);
            ui.nodes['sh-note'].value = 'Shipment 123';
            ui.nodes['sh-trash'].checked = false;
            ui.nodes['sh-fbo'].checked = true;
            ui.nodes['sh-fbo'].fire('change');
            await submitShipment(ui);
            assert.equal(requests.length, 1);
            assert.equal([...storage.values()][0], pending);

            ui.nodes['sh-trash'].checked = toTrash;
            ui.nodes['sh-fbo'].checked = false;
            ui.nodes['sh-legacy-fbs'].checked = toFbs;
            await submitShipment(ui);
            assert.doesNotMatch(ui.state.message, /Ошибка/);
            assert.equal(requests.length, 2);
            assert.equal(requests[1].key, requests[0].key);
            assert.deepEqual(requests[1].body, requests[0].body);
            if (toFbs) assert.match(ui.state.message, /Перемещено на FBS/);
            assert.equal(committed.size, 1, 'retry must not create another stock operation');
            assert.equal(storage.size, 0, 'successful replay clears the pending request');
            assert.equal(ui.state.tableRefreshes, 1);
            assert.equal(ui.nodes['sh-legacy-fbs-option'].hidden, true);

            ui.nodes['sh-note'].value = 'Shipment 123';
            ui.nodes['sh-trash'].checked = toTrash;
            ui.nodes['sh-file'].files = [file];
            ui.nodes['sh-url'].value = sheetUrl;
            await submitShipment(ui);
            assert.equal(requests.length, 3);
            assert.notEqual(requests[2].key, requests[0].key);
            assert.equal(committed.size, 2, 'a deliberate next shipment gets a fresh key');
        });
    }
}

test('legacy FBS recovery cannot start a new operation without a pending key', async () => {
    const storage = new Map();
    let requests = 0;
    const ui = shipmentUI('manual', (window, document) => installMutation(window, document, storage, () => {
        requests++;
        return reply({ ok: true });
    }));
    assert.equal(ui.nodes['sh-legacy-fbs-option'].hidden, true);
    // Covers a stale selection after a pending request has been resolved elsewhere.
    ui.nodes['sh-legacy-fbs'].checked = true;
    await submitShipment(ui);
    assert.match(ui.state.message, /Нет незавершённой операции/);
    assert.equal(requests, 0);
    assert.equal(storage.size, 0);
});

test('legacy FBS recovery excludes other modes and negative quantities', () => {
    const { nodes, state } = shipmentUI();
    nodes['sh-trash'].checked = true;
    nodes['sh-trash'].fire('change');
    nodes['sh-legacy-fbs'].checked = true;
    nodes['sh-legacy-fbs'].fire('change');
    assert.equal(nodes['sh-trash'].checked, false);
    assert.equal(state.allowNegative, false);
    assert.equal(nodes['sh-btn'].textContent, 'Повторить перемещение на FBS');
    nodes['sh-fbo'].checked = true;
    nodes['sh-fbo'].fire('change');
    assert.equal(nodes['sh-legacy-fbs'].checked, false);
});

test('a pending retry is not blocked by stock already consumed by the original request', async () => {
    const controls = Object.fromEntries(['mv-code', 'mv-qty', 'mv-hint', 'mv-remove', 'suggest-box']
        .map((name) => [`.${name}`, new Element()]));
    const row = new Element();
    row.querySelector = (selector) => controls[selector];
    const rowsBox = new Element();
    rowsBox.querySelectorAll = (selector) => selector === '.mv-row' ? [row] : [controls['.mv-qty']];
    const submitBtn = new Element();
    let pendingRetry = false;
    const window = { CheckStockSuggestions: { close() {} }, CheckStockUI: { render: () => '' } };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'static/stock/row-editor.js'), 'utf8'), {
        window, document: { createElement: () => row },
        fetch: () => reply({ stock: { '001': 10 } }),
    });
    const editor = window.createRowEditor({
        rowsBox, submitBtn, status: new Element(), storeSlug: 'rimili',
        getSource: () => ({ ff: 'Source', mp: 'WB' }), isPendingRetry: () => pendingRetry,
    });
    controls['.mv-code'].value = '001';
    controls['.mv-qty'].value = '30';
    editor.loadSourceStock();
    await flush();
    assert.equal(submitBtn.disabled, true);
    pendingRetry = true;
    editor.setAllowNegative(false);
    assert.equal(editor.validateRows().length, 0);
    assert.equal(submitBtn.disabled, false);
    assert.equal(controls['.mv-qty'].getAttribute('max'), undefined);
    controls['.mv-qty'].value = '-30';
    assert.equal(editor.validateRows().length, 1);
    controls['.mv-qty'].value = '30';
    pendingRetry = false;
    editor.setAllowNegative(false);
    assert.equal(submitBtn.disabled, true);
});

test('FBO retry after reload uses the saved key despite reduced stock', async () => {
    const storage = new Map();
    const requests = [];
    const committed = new Map();
    let available = 40;
    const send = async (url, options) => {
        const key = options.headers.get('Idempotency-Key');
        const items = JSON.parse(options.body.get('items'));
        requests.push(key);
        if (!committed.has(key)) {
            available -= items[0].quantity;
            committed.set(key, { ok: true, transfer_id: committed.size + 1, results: items });
        }
        if (requests.length === 1) throw new Error('Lost response after commit');
        return new Response(JSON.stringify(committed.get(key)), { status: 200 });
    };
    const createUI = () => shipmentUI('manual', (window, document) => {
        installMutation(window, document, storage, send);
        const rowsBox = document.getElementById('sh-rows');
        rowsBox.querySelectorAll = (selector) => selector === '.mv-row'
            ? rowsBox.children : rowsBox.children.map((row) => row.querySelector(selector));
        document.createElement = () => {
            const row = new Element();
            const controls = Object.fromEntries(['mv-code', 'mv-qty', 'mv-hint', 'mv-remove', 'suggest-box']
                .map((name) => [`.${name}`, new Element()]));
            row.querySelector = (selector) => controls[selector];
            return row;
        };
        window.CheckStockSuggestions = { close() {} };
        window.CheckStockUI = { render: () => '' };
        const initIoBlock = window.initIoBlock;
        vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'static/stock/row-editor.js'), 'utf8'), {
            window, document, fetch: () => reply({ stock: { '001': available } }),
        });
        window.initIoBlock = initIoBlock;
        window.CheckStockOperations.requireRowEditor = () => window.createRowEditor;
    });
    const fillForm = async (ui, quantity = 30) => {
        ui.nodes['sh-note'].value = 'Shipment 123';
        const row = ui.nodes['sh-rows'].children[0];
        row.querySelector('.mv-code').value = '001';
        row.querySelector('.mv-qty').value = String(quantity);
        ui.nodes['sh-fbo'].checked = true;
        ui.nodes['sh-fbo'].fire('change');
        ui.nodes['sh-ff'].fire('change');
        await flush();
    };
    let ui = createUI();
    await fillForm(ui);
    assert.equal(ui.nodes['sh-btn'].disabled, false);
    await submitShipment(ui);
    assert.equal(available, 10);
    assert.equal(storage.size, 1);

    ui = createUI();
    await fillForm(ui);
    assert.equal(ui.nodes['sh-btn'].disabled, false, 'the committed FBO shipment must remain retryable');
    await fillForm(ui, 31);
    await submitShipment(ui);
    assert.match(ui.nodes['sh-status'].textContent, /Результат предыдущей операции неизвестен/);
    assert.equal(requests.length, 1);
    await fillForm(ui);
    ui.nodes['sh-fbo'].checked = false;
    ui.nodes['sh-fbo'].fire('change');
    await submitShipment(ui);
    assert.match(ui.nodes['sh-status'].textContent, /Результат предыдущей операции неизвестен/);
    assert.equal(requests.length, 1);
    await fillForm(ui, -30);
    assert.equal(ui.nodes['sh-btn'].disabled, true, 'a pending FBO request still forbids negative quantities');

    await fillForm(ui);
    await submitShipment(ui);
    assert.equal(requests.length, 2);
    assert.equal(requests[1], requests[0]);
    assert.equal(committed.size, 1);
    assert.equal(available, 10);
    assert.equal(storage.size, 0);
    assert.equal(ui.state.tableRefreshes, 1);
    assert.equal(ui.state.transitRefreshes, 1);
    await fillForm(ui);
    assert.equal(ui.nodes['sh-btn'].disabled, true, 'new shipments must validate the remaining stock again');
    await fillForm(ui, 10);
    assert.equal(ui.nodes['sh-btn'].disabled, false);
    await submitShipment(ui);
    assert.notEqual(requests[2], requests[0]);
    assert.equal(committed.size, 2);
    assert.equal(available, 0);
});

test('FBO shipment submits the new flag and refreshes stock and movement cards', async () => {
    const { nodes, calls, state } = shipmentUI();
    nodes['sh-fbo'].checked = true;
    nodes['sh-fbo'].fire('change');
    assert.equal(nodes['sh-btn'].textContent, 'Отгрузить на склады FBO');
    nodes['sh-btn'].fire('click');
    await flush();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].data.to_fbo, '1');
    assert.equal(calls[0].data.to_fbs, undefined);
    assert.deepEqual(JSON.parse(calls[0].data.items), [{ code: '001', quantity: 30 }]);
    assert.equal(state.tableRefreshes, 1);
    assert.equal(state.transitRefreshes, 1);
    assert.match(state.message, /партия №7/);
    assert.equal(nodes['sh-fbo'].checked, false);
});

test('trash and FBO controls exclude one another and FBO disallows negative quantities', () => {
    const { nodes, state } = shipmentUI();
    nodes['sh-trash'].checked = true;
    nodes['sh-trash'].fire('change');
    assert.equal(state.allowNegative, true);
    nodes['sh-fbo'].checked = true;
    nodes['sh-fbo'].fire('change');
    assert.equal(nodes['sh-trash'].checked, false);
    assert.equal(state.allowNegative, false);
    nodes['sh-trash'].checked = true;
    nodes['sh-trash'].fire('change');
    assert.equal(nodes['sh-fbo'].checked, false);
});

function batch(kind, id) {
    return {
        id, kind, status: 'partial', from_fulfillment: 'Source', to_fulfillment: kind === 'ff_transfer' ? 'Destination' : 'Склады FBO',
        from_marketplace: 'WB', to_marketplace: 'WB', sent_units: 30, received_units: 10, remaining_units: 20,
        can_receive: true, can_reopen: true, can_cancel: false,
        items: [{ id: id * 10, to_article: '001', sent_quantity: 30, received_quantity: 10, remaining_quantity: 20 }],
    };
}

async function transitUI() {
    const panel = new Element();
    const list = new Element();
    const status = new Element();
    const nodes = {
        '[data-stock-transit-panel]': panel,
        ...Object.fromEntries(['dialog', 'form', 'reason', 'description', 'quantity', 'effect', 'error', 'submit']
            .map((name) => [`[data-stock-transit-reopen-${name}]`, new Element()])),
    };
    panel.querySelector = (selector) => ({ '[data-stock-transit-list]': list, '[data-stock-transit-status]': status })[selector];
    const calls = [];
    let refreshes = 0;
    const window = {
        location: { pathname: '/stock/rimili' }, setTimeout: (callback) => callback(),
        stockTable: { refresh: () => refreshes++ },
        CheckStockMutation: { fetch: (url, options) => {
            calls.push({ url, options });
            return reply(options ? { ok: true, status: 'partial' } : { ok: true, batches: [batch('fbo_shipment', 1), batch('ff_transfer', 2)] });
        } },
    };
    runScript('static/stock/transit.js', window, {
        querySelector: (selector) => nodes[selector], getElementById: () => null,
        createElement: (tag) => new Element(tag),
    });
    await flush();
    return { list, nodes, calls, getRefreshes: () => refreshes };
}

test('cards distinguish FBO and FF movements and explain receipt behavior', async () => {
    const { list } = await transitUI();
    assert.equal(list.children.length, 2);
    const labels = list.all().map((node) => node.textContent || '').join('\n');
    assert.match(labels, /Отгрузка на FBO · партия №1/);
    assert.match(labels, /Перемещение между ФФ · партия №2/);
    assert.match(labels, /Остатки ФФ не увеличиваются/);
});

test('partial receipt submits the entered quantity and refreshes stock', async () => {
    const { list, calls, getRefreshes } = await transitUI();
    const card = list.children[0];
    card.querySelector('[data-transit-item-id]').value = '7';
    card.all().find((node) => node.textContent === 'Принять указанное').fire('click');
    await flush();
    const received = calls.find((call) => call.url.endsWith('/receive'));
    assert.equal(received.url, '/stock/rimili/transfers/1/receive');
    assert.deepEqual(JSON.parse(received.options.body).items, [{ item_id: 10, quantity: 7 }]);
    assert.equal(getRefreshes(), 1);
});

test('reopening an FBO receipt promises no stock deduction; FF still shows a deduction', async () => {
    const { list, nodes } = await transitUI();
    for (let index = 0; index < 2; index++) {
        list.children[index].all().find((node) => node.textContent === 'Вернуть приёмку в путь').fire('click');
        const effect = nodes['[data-stock-transit-reopen-effect]'].textContent;
        assert.match(effect, index === 0 ? /без изменения остатков/ : /Со склада назначения будет снято/);
        assert.equal(nodes['[data-stock-transit-reopen-quantity]'].textContent, '10 ед.');
    }
});
