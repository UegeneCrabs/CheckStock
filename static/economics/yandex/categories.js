(function () {
    'use strict';
    var catalogs = {}, nextId = 0;
    var render = window.CheckStockUI.render;
    function catalog(store) {
        if (!catalogs[store]) catalogs[store] = window.YandexEconomicsFields.request('categories/' + encodeURIComponent(store))
            .catch(function (error) { delete catalogs[store]; throw error; });
        return catalogs[store];
    }
    function normalize(value) {
        return String(value).toLocaleLowerCase('ru').replace(/ё/g, 'е').trim();
    }
    function mount(container, store, categoryId, onChange) {
        var alive = true, enabled = true, tree, nodes = {}, children = {}, paths = {}, searchText = {};
        var selected, parentId, ready = false, opened = false, matches = [], limit = 60, active = -1;
        var prefix = 'ym-category-' + (++nextId);
        container.innerHTML = render('economics/yandex/categories/picker', { prefix: prefix });
        function element(name) { return container.querySelector('[data-ym-category-' + name + ']'); }
        var toggle = element('toggle'), editor = element('editor'), search = element('search');
        var list = element('options'), breadcrumbs = element('breadcrumbs'), caption = element('caption');
        var clear = element('clear'), more = element('more'), status = element('status'), retry = element('retry');
        function ancestry(id) {
            var result = [], node = nodes[id];
            while (node && String(node.id) !== String(tree.root_id)) {
                result.unshift(node);
                node = nodes[node.parent_id];
            }
            return result;
        }
        function statusText(text) { if (alive) status.textContent = text; }
        function drawSelection() {
            element('name').textContent = selected ? selected.name : 'Выберите категорию';
            element('path').textContent = selected
                ? ancestry(selected.id).slice(0, -1).map(function (node) { return node.name; }).join(' › ')
                : 'Найдите по названию или откройте каталог';
            element('action').firstChild.textContent = selected ? 'Изменить ' : 'Выбрать ';
        }
        function setActive(index, scroll) {
            var options = list.querySelectorAll('[role="option"]');
            active = index >= 0 && index < options.length ? index : -1;
            options.forEach(function (option, position) { option.classList.toggle('is-active', position === active); });
            if (active < 0) search.removeAttribute('aria-activedescendant');
            else {
                search.setAttribute('aria-activedescendant', options[active].id);
                if (scroll) options[active].scrollIntoView({ block: 'nearest' });
            }
        }
        function drawOptions() {
            var query = normalize(search.value), words = query.split(/\s+/).filter(Boolean);
            clear.hidden = !search.value;
            if (query) {
                matches = tree.items.filter(function (node) {
                    return String(node.id) !== String(tree.root_id) &&
                        (String(node.id) === query || words.every(function (word) { return searchText[node.id].includes(word); }));
                }).sort(function (a, b) {
                    function rank(node) {
                        var name = normalize(node.name);
                        return String(node.id) === query ? 0 : name === query ? 1 : name.startsWith(query) ? 2 :
                            words.every(function (word) { return name.includes(word); }) ? 3 : 4;
                    }
                    return rank(a) - rank(b) || Number(b.leaf) - Number(a.leaf) || paths[a.id].localeCompare(paths[b.id], 'ru');
                });
            } else matches = children[parentId] || [];
            var branch = query ? [] : ancestry(parentId);
            breadcrumbs.innerHTML = [{ id: tree.root_id, name: 'Все категории' }].concat(branch).map(function (node, index, parts) {
                return render('economics/yandex/categories/breadcrumb', {
                    id: node.id, name: node.name,
                    current: !query && index === parts.length - 1 ? ' aria-current="location"' : '',
                });
            }).join('<span aria-hidden="true">›</span>');
            var visible = matches.slice(0, limit);
            caption.textContent = query ? (matches.length ? 'Найдено: ' + matches.length : 'Ничего не найдено. Попробуйте другое название или ID.')
                : matches.length ? 'Выберите категорию или откройте раздел' : 'В этом разделе нет категорий.';
            if (matches.length > visible.length) caption.textContent += ' · Показано ' + visible.length;
            list.innerHTML = visible.map(function (node, index) {
                var isSelected = !!(selected && String(selected.id) === String(node.id));
                return render('economics/yandex/categories/option', {
                    id: prefix + '-option-' + index, nodeId: node.id, name: node.name, selected: String(isSelected),
                    path: query ? ancestry(node.id).slice(0, -1).map(function (part) { return part.name; }).join(' › ')
                        : node.leaf ? '' : 'Подкатегорий: ' + (children[node.id] || []).length,
                    suffix: node.leaf ? (isSelected ? '✓' : '') : '›',
                });
            }).join('');
            more.hidden = matches.length <= limit;
            list.scrollTop = 0;
            setActive(-1, false);
        }
        function closeEditor(focus) {
            opened = false;
            editor.hidden = true;
            toggle.setAttribute('aria-expanded', 'false');
            search.setAttribute('aria-expanded', 'false');
            setActive(-1, false);
            if (focus) toggle.focus();
        }
        function openEditor() {
            if (!enabled || !tree) return;
            opened = true;
            editor.hidden = false;
            toggle.setAttribute('aria-expanded', 'true');
            search.setAttribute('aria-expanded', 'true');
            search.value = '';
            parentId = selected ? selected.parent_id : tree.root_id;
            if (!children[parentId]) parentId = tree.root_id;
            limit = 60;
            drawOptions();
            search.focus();
        }
        function choose(node) {
            if (!enabled || !node) return;
            if (!node.leaf) {
                parentId = node.id;
                search.value = '';
                limit = 60;
                drawOptions();
                search.focus();
                return;
            }
            var changed = !selected || String(selected.id) !== String(node.id);
            selected = node;
            ready = true;
            drawSelection();
            closeEditor(true);
            if (changed) {
                statusText('Получаем комиссию выбранной категории…');
                onChange(node);
            }
        }
        toggle.onclick = function () { if (opened) closeEditor(false); else openEditor(); };
        element('close').onclick = function () { closeEditor(true); };
        search.oninput = function () { limit = 60; drawOptions(); };
        clear.onclick = function () { search.value = ''; limit = 60; drawOptions(); search.focus(); };
        breadcrumbs.onclick = function (event) {
            var button = event.target.closest('[data-ym-category-parent]');
            if (!enabled || !button) return;
            parentId = button.dataset.ymCategoryParent;
            search.value = '';
            limit = 60;
            drawOptions();
            search.focus();
        };
        list.onclick = function (event) {
            var option = event.target.closest('[data-ym-category-id]');
            if (option) choose(nodes[option.dataset.ymCategoryId]);
        };
        // Keep focus in the combobox while selecting its options with a pointer.
        list.onmousedown = function (event) { event.preventDefault(); };
        more.onclick = function () {
            var next = Math.min(limit, matches.length - 1);
            limit += 60;
            drawOptions();
            search.focus();
            setActive(next, true);
        };
        search.onkeydown = function (event) {
            if (event.isComposing) return;
            var count = Math.min(matches.length, limit);
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                setActive(!count ? -1 : active < 0 ? (event.key === 'ArrowDown' ? 0 : count - 1)
                    : (active + (event.key === 'ArrowDown' ? 1 : -1) + count) % count, true);
            } else if (event.key === 'Enter') {
                event.preventDefault();
                if (active >= 0) choose(matches[active]);
                else if (matches.length === 1) choose(matches[0]);
            }
        };
        container.onkeydown = function (event) {
            if (event.key === 'Escape' && opened) {
                event.preventDefault();
                event.stopPropagation();
                closeEditor(true);
            }
        };
        function outside(event) { if (opened && !container.contains(event.target)) closeEditor(false); }
        function focusOut(event) {
            if (opened && event.relatedTarget && !container.contains(event.relatedTarget)) closeEditor(false);
        }
        document.addEventListener('pointerdown', outside);
        container.addEventListener('focusout', focusOut);
        async function load() {
            search.disabled = true;
            toggle.disabled = true;
            retry.hidden = true;
            statusText('Загружаем категории ЯМ…');
            try {
                var result = await catalog(store);
                if (!alive) return;
                tree = result;
                nodes = {}; children = {}; paths = {}; searchText = {};
                tree.items.forEach(function (node) {
                    nodes[node.id] = node;
                    (children[node.parent_id] || (children[node.parent_id] = [])).push(node);
                });
                Object.keys(children).forEach(function (key) {
                    children[key].sort(function (a, b) { return a.name.localeCompare(b.name, 'ru'); });
                });
                tree.items.forEach(function (node) {
                    paths[node.id] = ancestry(node.id).map(function (part) { return part.name; }).join(' › ');
                    searchText[node.id] = normalize(paths[node.id]);
                });
                selected = nodes[categoryId] && nodes[categoryId].leaf ? nodes[categoryId] : null;
                ready = !!selected;
                drawSelection();
                search.disabled = !enabled;
                toggle.disabled = !enabled;
                statusText('');
            } catch (error) {
                if (!alive) return;
                element('name').textContent = 'Категории недоступны';
                statusText(error.message);
                retry.hidden = false;
                retry.disabled = !enabled;
            }
        }
        retry.onclick = load;
        load();
        return {
            complete: function () { return ready; },
            status: statusText,
            disable: function () {
                enabled = false;
                closeEditor(false);
                container.querySelectorAll('input, button').forEach(function (input) { input.disabled = true; });
            },
            dispose: function () {
                alive = false;
                document.removeEventListener('pointerdown', outside);
                container.removeEventListener('focusout', focusOut);
            },
        };
    }
    window.YandexCategoryPicker = { mount: mount };
})();
