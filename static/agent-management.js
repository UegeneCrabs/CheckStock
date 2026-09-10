(() => {
  const root = document.querySelector('.agent-page');
  if (!root) return;
  const message = document.getElementById('agent-message');
  const form = document.getElementById('agent-create');
  const secret = document.getElementById('agent-secret');
  const token = document.getElementById('agent-token');
  const tell = text => { message.textContent = text; };
  const editorHelp = document.getElementById('agent-editor-help');
  document.getElementById('agent-open-editor-help').onclick = () => editorHelp.showModal();
  document.getElementById('agent-close-editor-help').onclick = () => editorHelp.close();
  async function api(path, options = {}) {
    const response = await fetch('/api/ai-agents/keys' + path, {
      ...options, credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Agent-Management': '1'}
    });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || 'Не удалось выполнить запрос. Проверьте данные и повторите.');
    return data;
  }
  async function load() {
    const data = await api('');
    const body = document.getElementById('agent-keys');
    body.replaceChildren();
    document.getElementById('agent-empty').hidden = data.keys.length !== 0;
    for (const key of data.keys) {
      const row = document.createElement('tr');
      for (const value of [key.name, key.id, new Date(key.expires_at).toLocaleString('ru-RU'), key.active ? 'Активен' : 'Истёк']) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      const cell = document.createElement('td');
      const button = document.createElement('button'); button.textContent = 'Отозвать';
      button.setAttribute('aria-label', 'Отозвать ключ ' + key.name);
      button.onclick = async () => {
        if (!window.confirm('Отозвать ключ «' + key.name + '»? Агент потеряет доступ.')) return;
        button.disabled = true;
        try { await api('/' + encodeURIComponent(key.id), {method: 'DELETE'}); token.value = ''; secret.hidden = true; await load(); tell('Ключ отозван.'); }
        catch (error) { tell(error.message); button.disabled = false; }
      };
      cell.append(button); row.append(cell); body.append(row);
    }
  }
  form.onsubmit = async event => {
    event.preventDefault(); const submit = form.querySelector('button'); submit.disabled = true;
    token.value = ''; secret.hidden = true;
    try {
      const data = await api('', {method: 'POST', body: JSON.stringify({name: form.elements.name.value.trim(), days: Number(form.elements.days.value)})});
      token.value = data.token; secret.hidden = false; token.focus(); tell('Подключение создано. Сохраните ключ.');
      await load();
    } catch (error) { tell(error.message); } finally { submit.disabled = false; }
  };
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      tell('Скопировано.');
      return;
    } catch { /* Some browsers block Clipboard API, especially over HTTP. */ }
    const previousFocus = document.activeElement;
    const field = document.createElement('textarea');
    field.value = text;
    field.readOnly = true;
    field.setAttribute('aria-label', 'Текст для копирования');
    field.style.cssText = 'position:fixed;left:-9999px;top:0';
    document.body.append(field);
    field.focus();
    field.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch { /* Show manual fallback below. */ }
    field.remove();
    if (copied) {
      previousFocus?.focus();
      tell('Скопировано.');
      return;
    }
    tell('Автоматическое копирование недоступно. Текст выделен ниже — нажмите Ctrl+C или выберите «Копировать» в меню выделения.');
    field.style.cssText = 'display:block;width:100%;margin-top:12px';
    field.rows = 6;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Повторить копирование';
    retry.onclick = () => copy(field.value);
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = 'Скрыть текст';
    close.onclick = () => { field.value = ''; tell(''); previousFocus?.focus(); };
    message.append(field, retry, close);
    field.focus();
    field.select();
  }
  document.getElementById('agent-copy-key').onclick = () => copy(token.value);
  document.getElementById('agent-copy-instructions').onclick = async () => {
    try {
      const response = await fetch('/static/agent-instructions.txt', {cache: 'no-store'});
      if (!response.ok) throw new Error('Не удалось загрузить инструкцию.');
      await copy(await response.text());
    } catch (error) { tell(error.message); }
  };
  document.getElementById('agent-hide-key').onclick = () => { token.value = ''; secret.hidden = true; tell(''); };
  document.getElementById('agent-copy-schema').onclick = async () => {
    try {
      const response = await fetch('/api/agent/v1/openapi.json');
      if (!response.ok) throw new Error('Не удалось загрузить схему API.');
      const schema = await response.json();
      if (location.protocol === 'https:') schema.servers = [{url: location.origin}];
      await copy(JSON.stringify(schema, null, 2));
    } catch (error) { tell(error.message); }
  };
  document.getElementById('agent-refresh').onclick = () => load().catch(error => tell(error.message));
  window.addEventListener('pagehide', () => { token.value = ''; secret.hidden = true; tell(''); });
  load().catch(error => tell(error.message));
})();
