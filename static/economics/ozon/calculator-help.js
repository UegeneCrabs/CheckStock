window.OzonCalculatorHelp = (() => {
    'use strict';
    const providers = new WeakMap();
    let popup, active, trigger;

    function hide() {
        if (popup) popup.hidden = true;
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
        active = trigger = null;
    }

    function position() {
        const rect = trigger.getBoundingClientRect();
        const gap = 8;
        const width = document.documentElement.clientWidth;
        const height = window.innerHeight;
        popup.style.left = Math.max(gap, Math.min(rect.left, width - popup.offsetWidth - gap)) + 'px';
        popup.style.top = Math.max(gap, rect.bottom + popup.offsetHeight + gap <= height
            ? rect.bottom + gap : rect.top - popup.offsetHeight - gap) + 'px';
    }

    function refresh() {
        if (!active) return;
        if (!active.isConnected || !trigger.isConnected || !trigger.getClientRects().length) { hide(); return; }
        const rect = trigger.getBoundingClientRect();
        if (rect.bottom < 0 || rect.top > window.innerHeight) { hide(); return; }
        const data = providers.get(active)?.();
        if (!data) { hide(); return; }
        popup.replaceChildren();
        const title = document.createElement('strong');
        title.id = popup.id + '-title';
        title.className = 'ozon-calculator-help-title';
        title.textContent = data.title;
        popup.append(title);
        [['Формула', data.formula], ['Расчёт', data.calculation], ['Источник данных', data.source], ['Даты данных', data.dates]].forEach(([label, value]) => {
            const section = document.createElement('section');
            const heading = document.createElement('b');
            heading.textContent = label;
            const text = document.createElement('p');
            text.textContent = value || 'Данные не переданы.';
            section.append(heading, text);
            popup.append(section);
        });
        if (data.note) {
            const note = document.createElement('p');
            note.className = 'ozon-calculator-help-note';
            note.textContent = data.note;
            popup.append(note);
        }
        popup.hidden = false;
        position();
    }

    function show(target, button) {
        hide();
        active = target;
        trigger = button;
        trigger.setAttribute('aria-expanded', 'true');
        refresh();
    }

    function initialize() {
        if (popup) return;
        popup = document.createElement('div');
        popup.id = 'ozon-calculator-help';
        popup.className = 'ozon-calculator-help';
        popup.setAttribute('role', 'region');
        popup.setAttribute('aria-labelledby', popup.id + '-title');
        popup.hidden = true;
        document.body.append(popup);
        document.addEventListener('click', event => {
            const button = event.target instanceof Element ? event.target.closest('[data-ozon-help-toggle]') : null;
            const row = button?.closest('[data-ozon-help]');
            if (row && providers.has(row)) {
                event.preventDefault();
                event.stopPropagation();
                if (trigger === button) hide();
                else show(row, button);
            } else if (active && !popup.contains(event.target)) hide();
        }, true);
        document.addEventListener('pointerdown', event => {
            if (active && !trigger.contains(event.target) && !popup.contains(event.target)) hide();
        }, true);
        document.addEventListener('focusin', event => {
            if (active && !trigger.contains(event.target) && !popup.contains(event.target)) hide();
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && active) {
                hide();
                event.stopPropagation();
            }
        }, true);
        document.addEventListener('scroll', event => {
            if (popup.contains(event.target)) return;
            refresh();
        }, true);
        document.addEventListener('change', refresh);
        window.addEventListener('resize', hide);
    }

    function bind(node, provider) {
        if (!node) return;
        initialize();
        node.dataset.ozonHelp = '';
        providers.set(node, provider);
        node.removeAttribute('title');
        node.querySelectorAll('[title]').forEach(child => child.removeAttribute('title'));
        if (!node.querySelector('[data-ozon-help-toggle]')) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'ozon-calculator-help-toggle';
            button.dataset.ozonHelpToggle = '';
            button.textContent = '?';
            button.setAttribute('aria-label', 'Пояснение: ' + provider().title);
            button.setAttribute('aria-controls', popup.id);
            button.setAttribute('aria-expanded', 'false');
            const label = node.firstElementChild;
            (label?.tagName === 'SPAN' ? label : node).append(button);
        }
        if (active === node) refresh();
    }

    function clear(root) {
        if (!active || !active.isConnected || root.contains(active)) hide();
    }

    return { bind, refresh, clear };
})();
