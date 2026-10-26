window.OzonCalculatorHelp = (() => {
    'use strict';
    const providers = new WeakMap();
    let popup, active, described, showTimer, hideTimer;

    function hide() {
        window.clearTimeout(showTimer);
        window.clearTimeout(hideTimer);
        if (popup) popup.hidden = true;
        if (described) {
            const ids = (described.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== popup.id);
            if (ids.length) described.setAttribute('aria-describedby', ids.join(' '));
            else described.removeAttribute('aria-describedby');
        }
        active = described = null;
    }

    function position() {
        const rect = active.getBoundingClientRect();
        const gap = 8;
        const width = document.documentElement.clientWidth;
        const height = window.innerHeight;
        popup.style.left = Math.max(gap, Math.min(rect.left, width - popup.offsetWidth - gap)) + 'px';
        popup.style.top = Math.max(gap, rect.bottom + popup.offsetHeight + gap <= height
            ? rect.bottom + gap : rect.top - popup.offsetHeight - gap) + 'px';
    }

    function refresh() {
        if (!active) return;
        if (!active.isConnected || !active.getClientRects().length) { hide(); return; }
        const rect = active.getBoundingClientRect();
        if (rect.bottom < 0 || rect.top > window.innerHeight) { hide(); return; }
        const data = providers.get(active)?.();
        if (!data) { hide(); return; }
        popup.replaceChildren();
        const title = document.createElement('strong');
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

    function show(target, control) {
        hide();
        active = target;
        described = control || target.querySelector('input, select, button') || target;
        const ids = new Set((described.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
        ids.add(popup.id);
        described.setAttribute('aria-describedby', [...ids].join(' '));
        refresh();
    }

    function target(node) {
        return node instanceof Element ? node.closest('[data-ozon-help]') : null;
    }

    function scheduleHide() {
        window.clearTimeout(showTimer);
        window.clearTimeout(hideTimer);
        hideTimer = window.setTimeout(() => {
            if (!active?.contains(document.activeElement)) hide();
        }, 180);
    }

    function initialize() {
        if (popup) return;
        popup = document.createElement('div');
        popup.id = 'ozon-calculator-help';
        popup.className = 'ozon-calculator-help';
        popup.setAttribute('role', 'tooltip');
        popup.hidden = true;
        document.body.append(popup);
        document.addEventListener('pointerover', event => {
            const row = target(event.target);
            if (!row || row.contains(event.relatedTarget)) return;
            window.clearTimeout(hideTimer);
            window.clearTimeout(showTimer);
            showTimer = window.setTimeout(() => show(row), 180);
        });
        document.addEventListener('pointerout', event => {
            const row = target(event.target);
            if (row && !row.contains(event.relatedTarget)) scheduleHide();
        });
        document.addEventListener('focusin', event => {
            const row = target(event.target);
            if (row) show(row, event.target);
            else if (!popup.contains(event.target)) hide();
        });
        document.addEventListener('focusout', event => {
            const row = target(event.target);
            if (row && !row.contains(event.relatedTarget)) scheduleHide();
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && active) {
                hide();
                event.stopPropagation();
            }
        }, true);
        document.addEventListener('scroll', event => {
            if (popup.contains(event.target)) return;
            if (active?.contains(document.activeElement)) refresh();
            else hide();
        }, true);
        document.addEventListener('change', refresh);
        window.addEventListener('resize', hide);
        popup.addEventListener('pointerenter', () => window.clearTimeout(hideTimer));
        popup.addEventListener('pointerleave', scheduleHide);
    }

    function bind(node, provider) {
        if (!node) return;
        initialize();
        node.dataset.ozonHelp = '';
        providers.set(node, provider);
        node.removeAttribute('title');
        node.querySelectorAll('[title]').forEach(child => child.removeAttribute('title'));
        if (active === node) refresh();
    }

    function clear(root) {
        if (!active || !active.isConnected || root.contains(active)) hide();
    }

    return { bind, refresh, clear };
})();
