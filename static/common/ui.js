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

    async function request(url, options = {}) {
        const response = await fetch(url, { ...options, headers: { Accept: 'application/json', ...options.headers } });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(errorText(data));
        return data;
    }

    function errorText(data) {
        const messages = {
            forbidden: 'Нет доступа', not_found: 'Данные не найдены', no_targets: 'Не выбран ни один магазин',
            job_busy: 'Выгрузка уже выполняется', invalid_target: 'Некорректные параметры',
            invalid_period: 'Проверьте даты периода', interrupted: 'Выполнение прервано',
        };
        return messages[data.code || data.error_code || data.detail] || data.error ||
            (typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос');
    }

    async function loadTemplates(scripts) {
        const query = new URLSearchParams();
        const modules = new Set();
        scripts.forEach(item => {
            const url = new URL(item.src, location.href);
            if (url.origin !== location.origin) throw new Error('Неизвестный источник интерфейса');
            const match = url.pathname.match(/^\/static\/(.+)\.js$/);
            if (match) modules.add(match[1]);
        });
        modules.forEach(module => query.append('modules', module));
        templates = { ...templates, ...await request('/api/ui/templates?' + query) };
    }

    function date(value) {
        return value ? new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value)) : '—';
    }

    return { escapeHtml, render, copyText, request, errorText, loadTemplates, date, context: () => request('/api/ui/context') };
})();
