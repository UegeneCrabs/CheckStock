(() => {
    const text = document.getElementById('agent-instruction-text');
    const button = document.getElementById('agent-copy-visible-instructions');
    const status = document.getElementById('agent-instruction-status') || document.getElementById('agent-message');
    const viewer = document.getElementById('agent-instruction-viewer');
    if (viewer) {
        const controls = Array.from(document.querySelectorAll('#agent-instruction-sections button'));
        const heading = document.getElementById('agent-instruction-heading');
        const panel = document.getElementById('agent-instruction-panel');
        fetch(viewer.dataset.instructionUrl, { cache: 'no-store' })
            .then(response => { if (!response.ok) throw new Error('load'); return response.text(); })
            .then(value => {
                const sections = CheckStockInstructionSections.parse(value);
                const show = control => {
                    text.textContent = sections.get(control.dataset.section);
                    heading.textContent = control.textContent;
                    controls.forEach(item => item.setAttribute('aria-pressed', String(item === control)));
                    text.scrollTop = 0;
                };
                controls.forEach(control => { control.disabled = false; control.onclick = () => show(control); });
                show(controls[0]);
                panel.setAttribute('aria-busy', 'false');
                status.textContent = '';
            })
            .catch(() => {
                panel.setAttribute('aria-busy', 'false');
                status.textContent = 'Не удалось загрузить инструкцию. Обновите страницу.';
            });
        return;
    }
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
