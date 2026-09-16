(function () {
    'use strict';
    var catalogs = {}, nextId = 0;
    var render = window.CheckStockUI.render;
    function catalog(store) {
        if (!catalogs[store]) catalogs[store] = window.YandexEconomicsFields.request('categories/' + encodeURIComponent(store))
            .catch(function (error) { delete catalogs[store]; throw error; });
        return catalogs[store];
    }
    function mount(container, store, categoryId, onChange) {
        var alive = true, tree, nodes = {}, children = {}, paths = {}, selected = [], ready = false;
        var prefix = 'ym-category-' + (++nextId), enabled = true;
        container.innerHTML = render('economics/yandex/categories/picker', { prefix: prefix });
        var search = container.querySelector('[data-ym-category-search]');
        var searchList = container.querySelector('[data-ym-category-options]');
        var levels = container.querySelector('[data-ym-category-levels]');
        var status = container.querySelector('[data-ym-category-status]');
        var retry = container.querySelector('[data-ym-category-retry]');
        function ancestry(id) {
            var result = [], node = nodes[id];
            while (node && node.id !== tree.root_id) {
                result.unshift(node);
                node = nodes[node.parent_id];
            }
            return result;
        }
        function options(items, fullPath) {
            return items.map(function (node) {
                return render('economics/yandex/categories/option', {
                    value: fullPath ? paths[node.id] : node.name,
                    label: '',
                });
            }).join('');
        }
        function statusText(text) { status.textContent = text; }
        function notify(node) {
            ready = !!(node && node.leaf);
            statusText(ready ? 'Получаем комиссию выбранной категории…' : 'Выберите конечную подкатегорию для расчёта комиссии.');
            onChange(ready ? node : null);
        }
        function drawLevels() {
            var rows = [], parent = tree.root_id, depth = 0;
            while ((children[parent] || []).length) {
                var choices = children[parent], current = selected[depth];
                rows.push(render('economics/yandex/categories/level', {
                    id: prefix + '-' + depth,
                    depth: depth,
                    label: depth ? 'Подкатегория ' + depth : 'Категория',
                    value: current ? current.name : '',
                    options: options(choices, false),
                }));
                if (!current || current.leaf) break;
                parent = current.id;
                depth++;
            }
            levels.innerHTML = rows.join('');
            levels.querySelectorAll('input').forEach(function (input) {
                input.disabled = !enabled;
                input.oninput = function () {
                    var level = Number(input.dataset.ymCategoryLevel);
                    var parentId = level ? selected[level - 1].id : tree.root_id;
                    var node = (children[parentId] || []).find(function (item) { return item.name === input.value; });
                    selected = selected.slice(0, level);
                    search.value = '';
                    if (node) {
                        selected.push(node);
                        drawLevels();
                    } else {
                        // Keep the active search input and its caret while removing obsolete descendants.
                        levels.querySelectorAll('[data-ym-category-row]').forEach(function (row) {
                            if (Number(row.dataset.ymCategoryRow) > level) row.remove();
                        });
                    }
                    notify(node);
                };
            });
        }
        search.oninput = function () {
            var query = search.value.trim().toLocaleLowerCase('ru');
            var words = query.split(/\s+/).filter(Boolean);
            var matches = tree.items.filter(function (node) {
                var text = paths[node.id].toLocaleLowerCase('ru');
                return node.id !== tree.root_id && (String(node.id) === query || words.every(function (word) {
                    return text.includes(word);
                }));
            });
            matches.sort(function (a, b) {
                var aName = a.name.toLocaleLowerCase('ru').includes(query);
                var bName = b.name.toLocaleLowerCase('ru').includes(query);
                return Number(bName) - Number(aName) || paths[a.id].localeCompare(paths[b.id], 'ru');
            });
            searchList.innerHTML = options(matches.slice(0, 60), true);
            var node = matches.find(function (item) { return paths[item.id] === search.value; });
            if (node) {
                selected = ancestry(node.id);
                drawLevels();
            }
            notify(node);
            if (!node) statusText(matches.length ? 'Выберите категорию из подсказок' +
                (matches.length > 60 ? ' или уточните поиск — показаны первые 60 совпадений.' : '.') : 'Категория не найдена. Уточните поиск.');
        };
        async function load() {
            search.disabled = true;
            retry.hidden = true;
            statusText('Загружаем категории ЯМ…');
            try {
                tree = await catalog(store);
                if (!alive) return;
                tree.items.forEach(function (node) {
                    nodes[node.id] = node;
                    (children[node.parent_id] || (children[node.parent_id] = [])).push(node);
                });
                Object.keys(children).forEach(function (key) {
                    children[key].sort(function (a, b) { return a.name.localeCompare(b.name, 'ru'); });
                });
                tree.items.forEach(function (node) {
                    paths[node.id] = ancestry(node.id).map(function (part) { return part.name; }).join(' → ');
                });
                selected = ancestry(categoryId);
                ready = !!(nodes[categoryId] && nodes[categoryId].leaf);
                search.disabled = !enabled;
                searchList.innerHTML = options(children[tree.root_id] || [], true);
                drawLevels();
                statusText('');
            } catch (error) {
                if (!alive) return;
                statusText(error.message);
                retry.hidden = false;
            }
        }
        retry.onclick = load;
        load();
        return {
            complete: function () { return ready; },
            status: statusText,
            disable: function () {
                enabled = false;
                container.querySelectorAll('input, button').forEach(function (input) { input.disabled = true; });
            },
            dispose: function () { alive = false; },
        };
    }
    window.YandexCategoryPicker = { mount: mount };
})();
