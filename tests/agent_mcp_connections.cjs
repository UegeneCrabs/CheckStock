const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(provider, { failLoad = false, failRevoke = false, unknown = true } = {}) {
    const element = () => ({ children: [], textContent: '', disabled: false, hidden: true,
        append(...items) { this.children.push(...items); }, replaceChildren() { this.children = []; } });
    const ids = Object.fromEntries(['mcp-status', 'mcp-connections', 'mcp-copy', 'mcp-url', 'mcp-refresh',
        'mcp-other-section', 'mcp-other-connections'].map(id => [id, element()]));
    const requests = [];
    const copied = [];
    let others = unknown ? [{ id: 'unknown', name: 'Legacy', expires: 1900000000 }] : [];
    const context = { document: { getElementById: id => ids[id],
        querySelector: () => ({ dataset: { mcpProvider: provider } }),
        createElement: element, createTextNode: text => ({ textContent: text }) },
        fetch: async (url, options) => {
            requests.push([url, options.method]);
            if (options.method === 'DELETE') {
                if (failRevoke) return { ok: false };
                others = [];
                return { ok: true, status: 204 };
            }
            return { ok: !failLoad, status: 200, json: async () => ({ url: 'https://site.test/mcp',
                connections: [{ id: provider, name: provider, expires: 1900000000 }], other_connections: others }) };
        }, CheckStockAgentClipboard: async value => copied.push(value) };
    context.window = context;
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/agents/mcp.js'), 'utf8'), context);
    return { ids, requests, copied };
}

for (const provider of ['claude', 'chatgpt']) {
    // Каждая вкладка запрашивает свой сервис; неизвестное подключение отображается отдельно и отзывается.
    test(`separate unknown connections and revoke: ${provider}`, async () => {
        const app = setup(provider);
        await flush();
        const mainCount = app.ids['mcp-connections'].children.length;
        const wasVisible = !app.ids['mcp-other-section'].hidden;
        const revoke = app.ids['mcp-other-connections'].children[0].children[1];

        await revoke.onclick();
        await app.ids['mcp-copy'].onclick();

        assert.equal(mainCount, 1);
        assert.equal(wasVisible, true);
        assert.equal(app.requests[0][0], `/api/ai-agents/mcp/connections?provider=${provider}`);
        assert.deepEqual(app.requests[1], ['/api/ai-agents/mcp/connections/unknown', 'DELETE']);
        assert.equal(app.ids['mcp-other-connections'].children.length, 0);
        assert.equal(app.ids['mcp-other-section'].hidden, true);
        assert.equal(app.ids['mcp-connections'].children.length, 1);
        assert.deepEqual(app.copied, ['https://site.test/mcp']);
    });
    // Ошибка отзыва сохраняет неизвестное подключение и позволяет повторить действие.
    test(`failed revoke keeps connection accessible: ${provider}`, async () => {
        const app = setup(provider, { failRevoke: true });
        await flush();
        const button = app.ids['mcp-other-connections'].children[0].children[1];

        await button.onclick();

        assert.equal(button.disabled, false);
        assert.equal(app.ids['mcp-other-connections'].children.length, 1);
        assert.match(app.ids['mcp-status'].textContent, /Не удалось/);
    });
}
// Если неопределённых подключений нет, дополнительный блок скрыт.
test('empty unknown section stays hidden', async () => {
    const app = setup('claude', { unknown: false });
    await flush();
    assert.equal(app.ids['mcp-other-section'].hidden, true);
    assert.equal(app.ids['mcp-other-connections'].children.length, 0);
});
// Сбой загрузки показывает ошибку и не создаёт вымышленные подключения.
test('load failure shows error', async () => {
    const app = setup('chatgpt', { failLoad: true });
    await flush();
    assert.match(app.ids['mcp-status'].textContent, /Не удалось/);
    assert.equal(app.ids['mcp-connections'].children.length, 0);
    assert.equal(app.ids['mcp-other-connections'].children.length, 0);
});
