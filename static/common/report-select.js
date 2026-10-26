(() => {
    window.CheckStockReportSelect = function setupSearchableSelect(select) {
        const container = select.closest('[data-spp-filter], [data-wb-filter]');
        const prefix = container.hasAttribute('data-wb-filter') ? 'wb' : 'spp';
        const control = document.createElement('div');
        const input = document.createElement('input');
        const toggle = document.createElement('button');
        const list = document.createElement('div');
        let open = false;
        let active = -1;
        let matches = [];
        control.className = `${prefix}-filter__control`;
        input.type = 'search';
        input.id = `${select.id}-search`;
        input.autocomplete = 'off';
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', `${select.id}-options`);
        toggle.type = 'button';
        toggle.className = `${prefix}-filter__toggle`;
        toggle.textContent = '▾';
        toggle.setAttribute('aria-label', `Показать все варианты: ${container.querySelector('label').textContent}`);
        list.id = `${select.id}-options`;
        list.className = `${prefix}-filter__options`;
        list.setAttribute('role', 'listbox');
        list.hidden = true;
        control.append(input, toggle);
        container.append(control, list);
        select.hidden = true;

        function sync() {
            input.value = select.selectedOptions[0]?.textContent || 'Все';
        }
        function setActive(index) {
            active = index;
            list.querySelectorAll('[role="option"]').forEach((option, position) => {
                option.classList.toggle('is-active', position === active);
                if (position === active) option.scrollIntoView({ block: 'nearest' });
            });
            if (active < 0) input.removeAttribute('aria-activedescendant');
            else input.setAttribute('aria-activedescendant', `${list.id}-${active}`);
        }
        function draw(query = '') {
            const normalized = query.trim().toLocaleLowerCase('ru');
            matches = Array.from(select.options).filter((option) =>
                !normalized || option.textContent.toLocaleLowerCase('ru').includes(normalized));
            list.replaceChildren();
            matches.forEach((option, index) => {
                const item = document.createElement('button');
                item.type = 'button';
                item.id = `${list.id}-${index}`;
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', String(option.value === select.value));
                item.textContent = option.textContent;
                item.addEventListener('mousedown', (event) => event.preventDefault());
                item.addEventListener('click', () => choose(option));
                list.append(item);
            });
            if (!matches.length) {
                const empty = document.createElement('div');
                empty.className = `${prefix}-filter__empty`;
                empty.textContent = 'Ничего не найдено';
                list.append(empty);
            }
            setActive(-1);
        }
        function show(query = '') {
            draw(query);
            open = true;
            list.hidden = false;
            input.setAttribute('aria-expanded', 'true');
        }
        function close() {
            open = false;
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            input.removeAttribute('aria-activedescendant');
            sync();
        }
        function choose(option) {
            select.value = option.value;
            select.dispatchEvent(new Event('change', { bubbles: true }));
            close();
            input.focus();
        }
        input.addEventListener('focus', () => input.select());
        input.addEventListener('click', () => { input.select(); show(); });
        input.addEventListener('input', () => show(input.value));
        toggle.addEventListener('click', () => {
            if (open) close();
            else { show(); input.focus(); }
        });
        input.addEventListener('keydown', (event) => {
            if (event.isComposing) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (!open) show();
                if (matches.length) setActive(active < 0 ? (event.key === 'ArrowDown' ? 0 : matches.length - 1)
                    : (active + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length);
            } else if (event.key === 'Enter' && open) {
                event.preventDefault();
                if (active >= 0) choose(matches[active]);
                else if (matches.length === 1) choose(matches[0]);
            } else if (event.key === 'Escape' && open) {
                event.preventDefault();
                close();
            }
        });
        document.addEventListener('pointerdown', (event) => {
            if (open && !container.contains(event.target)) close();
        });
        container.addEventListener('focusout', (event) => {
            if (open && !container.contains(event.relatedTarget)) close();
        });
        select.syncSearch = () => { sync(); if (open) draw(); };
        select.resetSearch = close;
        sync();
    }
})();

