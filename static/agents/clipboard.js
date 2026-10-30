window.CheckStockAgentClipboard = async function copy(text, message) {
    const tell = value => { message.textContent = value; };
    try {
        await navigator.clipboard.writeText(text);
        tell('Скопировано.');
        return;
    } catch {
        /* Some browsers block Clipboard API, especially over HTTP. */
    }
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
    try {
        copied = document.execCommand('copy');
    } catch {
        /* Show manual fallback below. */
    }
    field.remove();
    if (copied) {
        previousFocus?.focus();
        tell('Скопировано.');
        return;
    }
    tell(
        'Автоматическое копирование недоступно. Текст выделен ниже — нажмите Ctrl+C или выберите «Копировать» в меню выделения.',
    );
    field.style.cssText = 'display:block;width:100%;margin-top:12px';
    field.rows = 6;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Повторить копирование';
    retry.onclick = () => copy(field.value, message);
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = 'Скрыть текст';
    close.onclick = () => {
        field.value = '';
        tell('');
        previousFocus?.focus();
    };
    message.append(field, retry, close);
    field.focus();
    field.select();
};
