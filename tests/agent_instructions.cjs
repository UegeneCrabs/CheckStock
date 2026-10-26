const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup({ gpt = false, ok = true, value = '<b>СПП</b>\nТекст', rejectFetch = false, rejectCopy = false, legacyCopy = false } = {}) {
    const element = () => ({ textContent: '', disabled: false, children: [], style: {}, dataset: {},
        append(...items) { this.children.push(...items); }, focus() {}, select() {}, remove() {}, setAttribute() {} });
    const ids = Object.fromEntries(['agent-instruction-text', 'agent-copy-visible-instructions', 'agent-instruction-status'].map(id => [id, element()]));
    if (gpt) ids['agent-copy-instructions'] = element();
    const instructionUrl = gpt ? '/static/agents/agent-instructions.txt' : '/static/agents/agent-instructions-full.txt';
    for (const id of ['agent-copy-visible-instructions', 'agent-copy-instructions']) {
        if (ids[id]) ids[id].dataset.instructionUrl = instructionUrl;
    }
    const copied = [], fetched = [];
    const context = { document: { getElementById: id => ids[id] || null, body: element(), activeElement: element(),
        createElement: element, execCommand: () => legacyCopy },
        navigator: { clipboard: { writeText: async text => { if (rejectCopy) throw Error('blocked'); copied.push(text); } } },
        fetch: async (url, options) => { fetched.push([url, options]); if (rejectFetch) throw Error('offline'); return { ok, text: async () => value }; } };
    context.window = context;
    vm.createContext(context);
    for (const file of ['clipboard.js', 'instructions.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/agents', file), 'utf8'), context);
    return { ids, copied, fetched };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

// Страница получает нужную версию инструкции; кнопки копируют загруженный текст без преобразования.
for (const gpt of [false, true]) test(`instruction loads and all buttons copy identical text: gpt=${gpt}`, async () => {
    const app = setup({ gpt });
    const initiallyDisabled = app.ids['agent-copy-visible-instructions'].disabled;
    await flush();
    const buttons = ['agent-copy-visible-instructions', ...(gpt ? ['agent-copy-instructions'] : [])];
    const enabled = buttons.map(id => !app.ids[id].disabled);
    for (const id of buttons) await app.ids[id].onclick();

    assert.equal(initiallyDisabled, true);
    assert.ok(enabled.every(Boolean));
    assert.equal(app.ids['agent-instruction-text'].textContent, '<b>СПП</b>\nТекст');
    assert.equal(app.fetched.length, 1);
    assert.equal(app.fetched[0][0], gpt ? '/static/agents/agent-instructions.txt' : '/static/agents/agent-instructions-full.txt');
    assert.equal(app.fetched[0][1].cache, 'no-store');
    assert.equal(app.copied.length, gpt ? 2 : 1);
    assert.ok(app.copied.every(value => value === app.ids['agent-instruction-text'].textContent));
});
// Ошибка сети, HTTP или пустой текст оставляют копирование недоступным и показывают ошибку.
for (const options of [{ ok: false }, { value: '' }, { rejectFetch: true }]) test(`loading error keeps copy disabled ${JSON.stringify(options)}`, async () => {
    const app = setup({ ...options, gpt: true });
    await flush();
    assert.match(app.ids['agent-instruction-status'].textContent, /Не удалось загрузить/);
    assert.equal(app.ids['agent-copy-instructions'].disabled, true);
    assert.equal(app.ids['agent-copy-visible-instructions'].disabled, true);
});
// При отказе Clipboard API запасной способ успешно копирует инструкцию.
test('clipboard failure supports legacy copy', async () => {
    const app = setup({ rejectCopy: true, legacyCopy: true });
    await flush(); await app.ids['agent-copy-visible-instructions'].onclick();
    assert.equal(app.ids['agent-instruction-status'].textContent, 'Скопировано.');
});
// При отказе обоих способов доступен исходный текст для ручного копирования.
test('clipboard failure exposes original text for manual copy', async () => {
    const app = setup({ rejectCopy: true });
    await flush(); await app.ids['agent-copy-visible-instructions'].onclick();
    const status = app.ids['agent-instruction-status'];
    assert.match(status.textContent, /Ctrl\+C/);
    assert.equal(status.children[0].value, app.ids['agent-instruction-text'].textContent);
    assert.equal(status.children[0].readOnly, true);
});

// Повтор ручного копирования сохраняет текст и выводит подсказку в прежнем блоке.
test('manual clipboard retry preserves text and status target', async () => {
    const app = setup({ rejectCopy: true });
    await flush();
    await app.ids['agent-copy-visible-instructions'].onclick();
    const status = app.ids['agent-instruction-status'];
    await status.children[1].onclick();
    assert.match(status.textContent, /Ctrl\+C/);
    assert.equal(status.children[3].value, app.ids['agent-instruction-text'].textContent);
});
