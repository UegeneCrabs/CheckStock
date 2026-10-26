(() => {
    const text = document.getElementById('agent-instruction-text');
    const button = document.getElementById('agent-copy-visible-instructions');
    const status = document.getElementById('agent-instruction-status') || document.getElementById('agent-message');
    const buttons = [button, document.getElementById('agent-copy-instructions')].filter(Boolean);
    if (!buttons.length || !status) return;
    let instruction = '';
    for (const control of buttons) {
        control.disabled = true;
        control.onclick = () => window.CheckStockAgentClipboard(instruction, status);
    }
    fetch(buttons[0].dataset.instructionUrl, { cache: 'no-store' })
        .then(response => {
            if (!response.ok) throw new Error('load');
            return response.text();
        })
        .then(value => {
            if (!value.trim()) throw new Error('empty');
            instruction = value;
            if (text) text.textContent = value;
            for (const control of buttons) control.disabled = false;
            status.textContent = '';
        })
        .catch(() => {
            status.textContent = 'Не удалось загрузить инструкцию. Обновите страницу.';
        });
})();
