window.CheckStockUI = (() => {
    'use strict';
    const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    let templates;

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => entities[char]);
    }

    function render(name, values = {}) {
        if (!templates) {
            templates = JSON.parse(document.getElementById('client-templates').textContent);
        }
        if (!Object.hasOwn(templates, name)) throw new Error('Unknown UI template: ' + name);
        return templates[name].replace(/\{\{\{(\w+)\}\}\}|\{\{(\w+)\}\}/g, (_, rawKey, textKey) => {
            const key = rawKey || textKey;
            if (!Object.hasOwn(values, key)) throw new Error('Missing template value: ' + name + '.' + key);
            return rawKey ? '' + values[key] : escapeHtml(values[key]);
        });
    }

    function fallbackCopy(value) {
        const input = document.createElement('textarea');
        input.value = value;
        input.className = 'clipboard-buffer';
        input.setAttribute('readonly', '');
        document.body.appendChild(input);
        try {
            input.select();
            if (!document.execCommand('copy')) throw new Error('copy failed');
        } finally {
            input.remove();
        }
    }

    function copyText(value) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(value).catch(() => fallbackCopy(value));
        }
        return Promise.resolve().then(() => fallbackCopy(value));
    }

    return { escapeHtml, render, copyText };
})();
