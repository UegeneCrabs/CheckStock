(() => {
    const status = document.getElementById('mcp-status');
    if (!status) return;
    const list = document.getElementById('mcp-connections');
    const copy = document.getElementById('mcp-copy');
    let url = null;
    async function api(suffix = '', method = 'GET') {
        const response = await fetch('/api/ai-agents/mcp/connections' + suffix, {
            method, credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json', 'X-Agent-Management': '1' },
        });
        if (!response.ok) throw new Error('Не удалось обновить подключения. Проверьте вход и повторите.');
        return response.status === 204 ? null : response.json();
    }
    async function load() {
        try {
            const data = await api();
            url = data.url;
            document.getElementById('mcp-url').textContent = url || 'Подключение ещё не настроено администратором';
            copy.disabled = !url;
            list.replaceChildren();
            status.textContent = data.connections.length ? '' : 'Активных подключений пока нет.';
            for (const connection of data.connections) {
                const item = document.createElement('li');
                item.append(document.createTextNode(connection.name + ' — до ' + new Date(connection.expires * 1000).toLocaleString('ru-RU') + ' '));
                const button = document.createElement('button');
                button.type = 'button';
                button.textContent = 'Отозвать';
                button.onclick = async () => {
                    button.disabled = true;
                    try {
                        await api('/' + encodeURIComponent(connection.id), 'DELETE');
                        await load();
                    } catch (error) {
                        status.textContent = error.message;
                        button.disabled = false;
                    }
                };
                item.append(button);
                list.append(item);
            }
        } catch (error) { status.textContent = error.message; }
    }
    copy.onclick = async () => {
        try { await navigator.clipboard.writeText(url); status.textContent = 'Адрес скопирован.'; }
        catch { status.textContent = 'Выделите и скопируйте адрес вручную.'; }
    };
    document.getElementById('mcp-refresh').onclick = load;
    load();
})();
