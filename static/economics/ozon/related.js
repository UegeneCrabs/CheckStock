window.OzonRelatedUI = (() => {
    'use strict';
    const sessions = new WeakMap();

    function clear(root) {
        const session = sessions.get(root);
        if (session) {
            session.controller.abort();
            session.toggle.onclick = null;
            session.heading.textContent = 'Связанные артикулы';
            session.section.classList.remove('ozon-related');
            session.section.hidden = true;
            session.content.hidden = false;
            session.content.removeAttribute('aria-busy');
        }
        sessions.delete(root);
    }

    async function render(root, product, display) {
        if (sessions.get(root)?.productId === product.id) return;
        clear(root);
        const section = root.querySelector('#ue1c-glued-section');
        const content = root.querySelector('#ue1c-glued-products');
        const heading = section.querySelector('h3');
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'ozon-related-toggle';
        toggle.setAttribute('aria-controls', content.id);
        const title = document.createElement('span');
        title.textContent = 'Связанные артикулы';
        const count = document.createElement('span');
        count.className = 'ozon-related-count';
        const action = document.createElement('span');
        action.className = 'ozon-related-action';
        toggle.append(title, count, action);
        heading.replaceChildren(toggle);
        section.classList.add('ozon-related');
        const session = {
            productId: product.id, controller: new AbortController(), expanded: false,
            section, content, heading, toggle, count, action,
        };
        sessions.set(root, session);
        toggle.onclick = () => {
            session.expanded = !session.expanded;
            updateExpanded(session);
        };
        updateExpanded(session);
        return load(root, product, display, session);
    }

    function updateExpanded(session) {
        session.toggle.setAttribute('aria-expanded', String(session.expanded));
        session.action.textContent = session.expanded ? 'Свернуть' : 'Показать';
        session.content.hidden = !session.expanded;
    }

    async function load(root, product, display, session) {
        const { section, content } = session;
        section.hidden = false;
        content.textContent = 'Загружаем связанные артикулы…';
        content.setAttribute('aria-busy', 'true');
        try {
            const query = new URLSearchParams({ store: product.store_slug, article: product.article });
            const response = await fetch('/api/unit-economics-1c/ozon/related?' + query, {
                headers: { Accept: 'application/json' }, signal: session.controller.signal,
            });
            const result = await response.json();
            if (!response.ok || !Array.isArray(result.items)) throw new Error('Related products unavailable');
            if (sessions.get(root) !== session) return;
            display(result.items);
            session.count.textContent = result.complete === true ? String(result.items.length) : '';
            if (result.complete === false) {
                section.hidden = false;
                const note = document.createElement('p');
                note.className = 'ue1c-calculator-note';
                note.textContent = 'Показаны известные связи. Для части товаров Ozon не вернул модель.';
                content.append(note);
            }
        } catch (error) {
            if (sessions.get(root) !== session || error.name === 'AbortError') return;
            content.textContent = 'Не удалось загрузить связанные артикулы. ';
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'ue1c-calculator-reset';
            retry.textContent = 'Повторить';
            retry.onclick = () => {
                session.toggle.focus();
                load(root, product, display, session);
            };
            content.append(retry);
        } finally {
            if (sessions.get(root) === session) content.removeAttribute('aria-busy');
        }
    }

    return { render, clear };
})();
