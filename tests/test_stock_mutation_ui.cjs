// Run: node --test tests/test_stock_mutation_ui.cjs. No browser, server or network required.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { webcrypto } = require('node:crypto');
const vm = require('node:vm');

const source = readFileSync('static/stock/mutation-request.js', 'utf8');

function setup() {
    const saved = new Map();
    const calls = [];
    let user = '1';
    let handler = async () => new Response(JSON.stringify({ ok: true }), { status: 200 });
    const context = {
        window: {}, crypto: webcrypto, TextEncoder, Uint8Array, FormData, Blob, Headers,
        document: { getElementById: () => ({ dataset: { mutationUser: user } }) },
        sessionStorage: {
            getItem: key => saved.get(key),
            setItem: (key, value) => saved.set(key, value),
            removeItem: key => saved.delete(key),
        },
        fetch: async (url, options) => {
            calls.push({ url, key: new Headers(options.headers).get('Idempotency-Key'), body: options.body });
            return handler();
        },
    };
    const reload = () => vm.runInNewContext(source, context);
    reload();
    return {
        saved, calls, reload, context,
        send: (body = '{"quantity":10}', url = '/stock/test/add-ff-items') => context.window.CheckStockMutation.fetch(url, { method: 'POST', body }),
        reply: fn => { handler = fn; },
        user: id => { user = id; },
    };
}

test('new intentional requests with identical content get independent keys', async () => {
    const env = setup();
    for (let i = 0; i < 3; i++) await env.send();
    assert.equal(new Set(env.calls.map(call => call.key)).size, 3);
    assert.equal(env.saved.size, 0);
});

test('lost response preserves key across reload and blocks changed input', async () => {
    const env = setup();
    env.reply(async () => { throw new Error('lost response'); });
    await assert.rejects(env.send(), /lost response/);
    const key = env.calls[0].key;
    env.reload();
    await assert.rejects(env.send('{"quantity":20}'), /предыдущей операции/);
    assert.equal(env.calls.length, 1);
    env.reply(async () => new Response('{"ok":true}'));
    await env.send();
    assert.equal(env.calls[1].key, key);
    await env.send();
    assert.notEqual(env.calls[2].key, key);
});

test('server errors, unreadable response and denied replay keep original key', async () => {
    const env = setup();
    for (const response of [new Response('{"ok":false}', { status: 500 }), new Response('gateway'), new Response('{"ok":false}', { status: 403 })]) {
        env.reply(async () => response);
        await env.send().catch(() => {});
    }
    assert.equal(new Set(env.calls.map(call => call.key)).size, 1);
    assert.equal(env.saved.size, 1);
});

test('preview has no key; uncertain confirmed import retains key and original calculation token', async () => {
    const env = setup();
    const preview = new FormData();
    preview.set('preview', '1');
    await env.send(preview, '/stock/test/upload-ff-stock');
    assert.equal(env.calls[0].key, null);
    function body(token) {
        const data = new FormData();
        data.set('sheet_url', 'https://example.invalid/sheet');
        data.set('confirmation_token', token);
        return data;
    }
    env.reply(async () => { throw new Error('timeout'); });
    await assert.rejects(env.send(body('original'), '/stock/test/upload-ff-stock'));
    env.reload();
    env.reply(async () => new Response('{"ok":true}'));
    await env.send(body('changed-preview'), '/stock/test/upload-ff-stock');
    assert.equal(env.calls[1].key, env.calls[2].key);
    assert.equal(env.calls[2].body.get('confirmation_token'), 'original');
    await env.send(body('original'), '/stock/test/upload-ff-stock');
    assert.notEqual(env.calls[2].key, env.calls[3].key);
});

test('files use contents for pending attempt matching, never for permanent delivery deduplication', async () => {
    const env = setup();
    const body = content => {
        const data = new FormData();
        data.set('file', new Blob([content]), 'delivery.xlsx');
        data.set('confirmation_token', 'calculation');
        return data;
    };
    env.reply(async () => { throw new Error('timeout'); });
    await assert.rejects(env.send(body('first')));
    await assert.rejects(env.send(body('different')), /предыдущей операции/);
    env.reply(async () => new Response('{"ok":true}'));
    await env.send(body('first'));
    await env.send(body('first'));
    assert.equal(env.calls[0].key, env.calls[1].key);
    assert.notEqual(env.calls[1].key, env.calls[2].key);
});

test('pending requests are scoped by current account', async () => {
    const env = setup();
    env.reply(async () => { throw new Error('timeout'); });
    await assert.rejects(env.send());
    env.user('2');
    await assert.rejects(env.send());
    assert.notEqual(env.calls[0].key, env.calls[1].key);
    assert.equal(env.saved.size, 2);
});

test('simultaneous clicks share the in-flight promise and key', async () => {
    const env = setup();
    let release;
    env.reply(() => new Promise(resolve => { release = resolve; }));
    const first = env.send();
    const second = env.send();
    while (!release) await new Promise(resolve => setTimeout(resolve, 1));
    await new Promise(resolve => setTimeout(resolve, 10));
    release(new Response('{"ok":true}'));
    const results = await Promise.all([first, second]);
    assert.equal(env.calls.length, 1);
    for (const result of results) assert.equal((await result.json()).ok, true);
});

test('upload form retries uncertain import without fetching a fresh Google preview', async () => {
    const env = setup();
    const body = new FormData();
    body.set('fulfillment', 'Test FF');
    body.set('marketplace', 'WB');
    body.set('note', 'Delivery');
    body.set('sheet_url', 'https://example.invalid/sheet');
    body.set('confirmation_token', 'original');
    env.reply(async () => { throw new Error('lost response'); });
    await assert.rejects(env.send(body, '/stock/test/upload-ff-stock'));
    let click;
    const nodes = {
        'ff-upload-select': { value: 'Test FF' },
        'ff-upload-note': { value: 'Delivery' },
        'ff-upload-file': { files: [] },
        'ff-upload-url': { value: 'https://example.invalid/sheet' },
        'ff-upload-btn': { addEventListener: (name, fn) => { click = fn; } },
        'ff-upload-status': { classList: { remove: () => {} } },
        'store-layout': { dataset: { store: 'test', mutationUser: '1' } },
    };
    env.context.document.getElementById = id => nodes[id];
    env.context.window.stockTable = { currentMp: () => 'WB' };
    env.context.window.initIoBlock = () => ({ current: () => 'sheet' });
    env.reply(async () => new Response('{"ok":false,"error":"Still unavailable"}', { status: 500 }));
    vm.runInNewContext(readFileSync('static/stock/forms/upload.js', 'utf8'), env.context);
    click();
    for (let i = 0; i < 50 && env.calls.length < 2; i++) {
        await new Promise(resolve => setTimeout(resolve, 1));
    }
    assert.equal(env.calls.length, 2);
    assert.equal(env.calls[1].body.get('preview'), null);
    assert.equal(env.calls[1].body.get('confirmation_token'), 'original');
    assert.equal(env.calls[1].key, env.calls[0].key);
});

test('HTTP browser fallback matches Web Crypto fingerprints and preserves retries', async () => {
    for (const body of ['', 'a', 'x'.repeat(55), 'x'.repeat(56), 'Товар'.repeat(500)]) {
        const env = setup();
        env.reply(async () => { throw new Error('timeout'); });
        await assert.rejects(env.send(body));
        env.context.crypto = { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) };
        env.reload();
        env.reply(async () => new Response('{"ok":true}'));
        await env.send(body);
        assert.equal(env.calls[0].key, env.calls[1].key);
        await env.send(body);
        assert.match(env.calls[2].key, /^[a-f0-9]{32}$/);
        assert.notEqual(env.calls[1].key, env.calls[2].key);
    }
});
