window.OzonCategoriesUI = (() => {
    'use strict';
    let catalogRequest;
    const trees = new WeakMap();

    function catalog() {
        if (!catalogRequest) catalogRequest = window.fetch('/api/unit-economics-1c/ozon/tariffs', {
            headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
        }).then(response => {
            if (!response.ok) throw new Error('Не удалось загрузить справочник тарифов.');
            return response.json();
        }).catch(error => { catalogRequest = null; throw error; });
        return catalogRequest;
    }

    function label(item) {
        return item.type_name + ' · ' + item.category + ' · ' + item.group;
    }

    function tree(catalog) {
        if (trees.has(catalog)) return trees.get(catalog);
        const rootId = 'ozon-categories';
        const branches = new Map();
        const items = [];
        catalog.items.forEach(item => {
            const groupId = 'group:' + item.group;
            const categoryId = 'category:' + JSON.stringify([item.group, item.category]);
            if (!branches.has(groupId)) {
                branches.set(groupId, { id: groupId, parent_id: rootId, name: item.group, leaf: false });
            }
            if (!branches.has(categoryId)) {
                branches.set(categoryId, { id: categoryId, parent_id: groupId, name: item.category, leaf: false });
            }
            items.push({ id: item.id, parent_id: categoryId, name: item.type_name, leaf: true, tariff: item });
        });
        const value = { root_id: rootId, items: [...branches.values(), ...items] };
        trees.set(catalog, value);
        return value;
    }

    function source(root, session) {
        const state = session.category;
        const node = root.querySelector('[data-ozon-category-note]');
        if (!node || !state) return;
        node.replaceChildren();
        const message = document.createElement('span');
        if (state.error) message.textContent = state.error;
        else if (state.mode === 'manual') message.textContent = 'Комиссия задана вручную. Следующая правка цены или скидки вернёт автоматический тариф. Выбор типа включит тариф справочника.';
        else if (state.mode === 'catalog') message.textContent = 'Комиссия FBO по справочнику от ' + new Date(state.catalog.effective_from + 'T12:00:00Z').toLocaleDateString('ru-RU') + '. Диапазон определяется ценой с учётом акции. Без региональных коэффициентов и индивидуальных скидок.';
        else message.textContent = 'Комиссия автоматически учитывает цену с учётом акции: до 100 ₽ — 17%, свыше 100 до 300 ₽ — 23%, с исключениями из справочника категории. Выше 300 ₽ используется обычная ставка товара; если она неизвестна — ставка категории. Выбор типа включает все ставки выбранной категории.';
        if (state.error) node.append(message);
        root.querySelectorAll('[data-ozon-commission-api-title]').forEach(row => {
            row.title = state.mode === 'api' ? row.dataset.ozonCommissionApiTitle
                : message.textContent + '\nЦена с учётом акции × комиссия FBO / 100. Сценарий открытой карточки.';
        });
        if (state.categoryError) {
            const warning = document.createElement('span');
            warning.textContent = 'Категорию карточки получить не удалось. Можно выбрать тип из справочника вручную.';
            node.append(warning);
        }
        node.hidden = !node.childElementCount;
        root.querySelector('#ue1c-subject-select').title = state.text;
        const heading = root.querySelector('[data-ozon-categories] .ym-category-heading');
        if (heading) window.OzonCalculatorHelp.bind(heading, () => window.OzonCalculatorHelpContent.category(session));
        root.querySelectorAll('[data-ozon-commission-api-title]').forEach(row => row.removeAttribute('title'));
        window.OzonCalculatorHelp.refresh();
    }

    function apply(root, session, changed) {
        const state = session.category;
        if (!state.selected) return;
        state.mode = 'catalog';
        state.error = '';
        state.touched = true;
        session.tariffId = state.selected.id;
        session.productTariffId = state.selected.id;
        session.invalid.delete('category');
        ['commission_percent', 'commission_amount'].forEach(key => {
            session.invalid.delete(key);
            delete session.draft[key];
        });
        session.edits = session.edits.filter(edit => !edit.field.startsWith('commission_'));
        state.picker.status('');
        source(root, session);
        changed();
    }

    function manual(root, session) {
        if (!session.category) return;
        session.category.mode = 'manual';
        session.tariffId = null;
        session.invalid.delete('category');
        source(root, session);
    }

    function render(root, product, session, changed) {
        let state = session.category;
        if (!state) {
            state = { mode: 'api', text: '', selected: null, catalog: null, touched: false, disposed: false, controller: new AbortController() };
            session.category = state;
        }
        const input = root.querySelector('#ue1c-subject-select');
        const originalRow = input.closest('.ue1c-calculator-row');
        originalRow.hidden = true;
        input.value = state.text;
        input.disabled = true;
        let row = root.querySelector('[data-ozon-categories]');
        if (!row) {
            row = document.createElement('div');
            row.className = 'ym-categories ozon-category-row ue1c-calculator-row--expanded';
            row.dataset.ozonCategories = '';
            originalRow.after(row);
        }
        let note = root.querySelector('[data-ozon-category-note]');
        if (!note) {
            note = document.createElement('div');
            note.className = 'ozon-category-note ue1c-expanded-only';
            note.dataset.ozonCategoryNote = '';
            note.setAttribute('role', 'status');
            row.after(note);
        }
        source(root, session);
        if (state.started) return;
        state.started = true;
        state.picker = window.MarketCategoryPicker.mount(row, {
            heading: 'Категория / тип Ozon', searchLabel: 'Поиск категории Ozon',
            placeholder: 'Название категории или типа товара', emptyTitle: 'Выберите категорию Ozon',
            loadingText: 'Загружаем категории Ozon…', emptySearch: 'Ничего не найдено. Попробуйте другое название.',
            notifyReselect: true,
            load: async () => {
                const value = await catalog();
                if (!state.disposed) {
                    state.catalog = value;
                    prefill();
                }
                return tree(value);
            },
            onChange: node => {
                state.selected = node.tariff;
                state.text = label(node.tariff);
                input.value = state.text;
                apply(root, session, changed);
            },
        });
        source(root, session);
        const query = new URLSearchParams({ store: product.store_slug, article: product.article });
        window.fetch('/api/unit-economics-1c/ozon/category?' + query, {
            signal: state.controller.signal, headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
        }).then(response => {
            if (!response.ok) throw new Error('category_unavailable');
            return response.json();
        }).then(identity => {
            if (state.disposed) return;
            state.identity = identity;
            prefill();
        }).catch(error => {
            if (state.disposed || error.name === 'AbortError') return;
            state.categoryError = true;
            source(root, session);
        });
        function prefill() {
            if (!state.touched && state.catalog && state.identity) {
                state.selected = state.catalog.items.find(item => item.id === state.identity.tariff_id) || null;
                state.text = state.selected ? label(state.selected) : state.identity.type_name;
                input.value = state.text;
                state.picker.select(state.selected ? state.selected.id : null, state.identity.type_name);
                const tariffId = state.selected ? state.selected.id : null;
                if (session.productTariffId !== tariffId) {
                    session.productTariffId = tariffId;
                    changed();
                }
            }
            source(root, session);
        }
    }

    function refresh(root, session) {
        if (!session.category) return;
        session.category.mode = session.values.commission_mode === 'manual' ? 'manual'
            : session.tariffId ? 'catalog' : 'api';
        source(root, session);
    }

    function clear(session) {
        if (!session.category) return;
        session.category.disposed = true;
        session.category.controller.abort();
        if (session.category.picker) session.category.picker.dispose();
    }

    return { render, manual, clear, refresh };
})();
