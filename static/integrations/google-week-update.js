(() => {
    'use strict';
    const root = document.querySelector('[data-week-marketplaces]');
    const template = document.querySelector('[data-week-card]');
    if (!root || !template) return;
    const e = window.CheckStockUI.escapeHtml;
    const labels = { WB: ['wb', 'WB', 'FBO'], OZON: ['ozon', 'Ozon', 'FBO'], 'YANDEX MARKET': ['yandex', 'Яндекс Маркет', 'FBY'] };
    const actions = ['number', 'sales', 'stock', 'search', 'freeze'];
    const weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
    const errors = {
        invalid_settings: 'Проверьте ссылку, названия листов, ячейки и расписание.', not_saved: 'Сначала сохраните настройки.',
        busy: 'Действие уже выполняется. Дождитесь его завершения.', credentials: 'Не настроен сервисный аккаунт Google.',
        google: 'Не удалось обратиться к Google Таблице. Проверьте доступ и повторите запуск.',
        source: 'Не удалось прочитать данные проекта. Запись для него пропущена.', sheet_missing: 'Лист удалён или недоступен.',
        week_missing: 'Не найдена неделя в нужном формате или непрерывный блок прошлых недель.',
        headers: 'Не найдены обязательные заголовки в одной строке.', ambiguous: 'Неоднозначные строки, диапазоны или порядок недель.',
        product: 'Не найден единственный товар по ARTICLE и проекту.', project: 'Не заполнен или неизвестен проект.',
        duplicate: 'Один товар повторяется в нескольких строках.', missing_data: 'Данные неполные. Отсутствующие данные учтены нулём.',
        future: 'Неделя ещё не завершена.', target: 'Недопустимый диапазон или пересечение с исходными ячейками.',
        merged: 'Ячейки объединены или объединение пересекает границу диапазона.', protected: 'Диапазон защищён от редактирования.',
        array: 'Диапазон может затронуть вывод формулы массива или сводной таблицы.', formula: 'Есть ошибка вычисления, нерассчитанная формула или внешний источник.',
        changed: 'Таблица изменилась во время проверки. Повторите действие.', too_large: 'Слишком много ячеек для одного пакета.',
        selection: 'Обновите список листов и сохраните выбор.', interrupted: 'Запуск прерван. Повторите действие.',
        internal: 'Не удалось выполнить действие. Повторите запуск.',
    };
    const date = value => value ? new Date(value).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' МСК' : '';
    const errorText = problem => !problem ? '' : problem.code === 'legacy' ? problem.detail :
        (errors[problem.code] || problem.code || 'Не удалось выполнить запрос.') +
        (problem.cell ? ' Ячейка: ' + problem.cell + '.' : '') + (problem.status ? ' HTTP ' + problem.status + '.' : '');
    const reason = value => errors[value] || value;

    async function request(url, options = {}) {
        let response;
        try { response = await fetch(url, { cache: 'no-store', credentials: 'same-origin', ...options }); }
        catch (_) { throw new Error('Нет связи с сервером. Проверьте подключение.'); }
        let data;
        try { data = await response.json(); }
        catch (_) { throw new Error('Сервер не смог выполнить запрос. Обновите страницу.'); }
        if (!response.ok) {
            if (Array.isArray(data.detail)) throw new Error('Проверьте заполнение полей: ' + data.detail.map(item => item.loc.slice(1).join('.')).join(', '));
            throw new Error(typeof data.detail === 'string' ? data.detail : errorText(data.detail));
        }
        return data;
    }

    function reportHtml(report, action) {
        if (!report) return '<p class="week-update-hint">Действие ещё не выполнялось для этих настроек.</p>';
        const cellLink = cell => cell ? `<a href="${e(report.spreadsheet_url + '#gid=' + report.sheet_id + '&range=' + encodeURIComponent(cell))}" target="_blank" rel="noopener noreferrer">${e(cell)}</a>` : '';
        const issueList = (title, values) => values.length ? `<details><summary>${title}: ${values.length}</summary><ul>${values.map(item => `<li>Строка ${item.row}, ${e(item.article)} · ${e(item.project)} ${cellLink(item.cell)} — ${e(reason(item.reason || 'missing_data'))}${item.missing_days.length ? ' Даты: ' + e(item.missing_days.join(', ')) : ''}</li>`).join('')}</ul></details>` : '';
        if (action === 'search') return report.sources.map(source => `<details open><summary>${cellLink(source.cell)} · ${e(source.value)} · совпадений: ${source.matches.length}</summary>${source.issue ? `<p>${e(reason(source.issue))}</p>` : ''}${source.matches.map(match => `<p>${cellLink(match.range)} · строка ${match.row}<br>${match.headers.map(header => `${cellLink(header.cell)}: ${e(header.value)}`).join('; ')}<br>${['ARTICLE', 'Проект'].map(name => `${name}: ${(match.columns[name] || []).map(item => cellLink(item.cell)).join(', ') || 'не найден'}`).join(' · ')}${match.stop ? '<br>Остановка: ' + cellLink(match.stop.cell) + ' — ' + e(match.stop.value || 'пусто') : ''}</p>`).join('')}</details>`).join('');
        if (action === 'freeze') return `${report.dry_run ? '<p>Предварительная проверка. Таблица не изменена.</p>' : ''}<p>Неделя: ${e(report.week)} · Формул: ${report.total_formulas} · Зафиксировано: ${report.written_cells}</p>${report.sheets.map(sheet => `<p><strong>${e(sheet.sheet_name || 'Лист #' + sheet.sheet_id)}</strong><br>${e(sheet.ranges.join(', '))}<br>${sheet.issue ? e(reason(sheet.issue)) : 'Формул: ' + sheet.formula_count + ', зафиксировано: ' + sheet.written_cells}</p>`).join('')}${report.backup ? '<p>Резервная копия: ' + e(report.backup) + '</p>' : ''}`;
        return `<p>Сопоставлено товаров: ${report.matched_products}. Проверено ячеек: ${report.checked_cells}; записано: ${report.written_cells}; из них нулей: ${report.zeroed_cells}; уже актуальны: ${report.unchanged_cells}.</p>
            <p>${report.periods.map(period => `${cellLink(period.cell)}: ${e(period.value)} (${e(period.date_from)} — ${e(period.date_to)})`).join('<br>')}</p>
            ${issueList('Пропущенные товары', report.issues)}${issueList('Незавершённые недели', report.missing_data)}${issueList('Неполные данные', report.zero_filled)}
            ${report.snapshot_times.length ? '<details><summary>Время снимков</summary><ul>' + report.snapshot_times.map(item => `<li>${e(item.store_slug)} · ${e(item.day)}: ${e(date(item.captured_at))}</li>`).join('') + '</ul></details>' : ''}
            ${report.backup ? '<p>Резервная копия: ' + e(report.backup) + '</p>' : ''}`;
    }

    function card(initial) {
        const market = initial.profile.marketplace;
        const [prefix, label, scheme] = labels[market];
        const salesDescription = market === 'OZON'
            ? 'Заказы FBO + FBS по дате «Принят» (МСК), кроме отменённых, по всем найденным неделям.'
            : 'Заказы за вычетом отмен по всем найденным неделям.';
        const values = { marketplace: label, prefix, stock_scheme: scheme, sales_description: salesDescription, weekdays: weekdays.map((day, index) => `<option value="${index}">${day}</option>`).join('') };
        const wrapper = document.createElement('div');
        wrapper.innerHTML = template.innerHTML.replace(/\$([a-zA-Z_]\w*)/g, (_, name) => values[name] || '');
        const form = wrapper.querySelector('form');
        form.dataset.marketplace = market;
        root.append(form);
        const field = name => form.elements.namedItem(name);
        const query = selector => form.querySelector(selector);
        const api = '/api/admin/google-weeks/' + encodeURIComponent(market);
        const statusNodes = Object.fromEntries(actions.map(action => [action, query(action === 'number' ? '[data-week-status]' : `[data-week-${action}-status]`)]));
        const buttons = Object.fromEntries(actions.map(action => [action, query(action === 'number' ? '[data-week-run]' : `[data-week-${action}]`)]));
        const feedback = query('[data-week-save-status]');
        let view = initial;
        let savedFingerprint = '';
        let busy = false;
        let selected = new Set();
        let preview = null;
        let previewContext = '';
        const markup = new WeakMap();

        function settings() {
            return {
                spreadsheet_url: field('spreadsheet_url').value.trim(), sheet_name: field('sheet_name').value.trim(),
                stock_sheet_name: field('stock_sheet_name').value.trim(), cells: field('cells').value.trim().toUpperCase().split(/[\s,;]+/),
                weekday: Number(field('weekday').value), run_time: field('run_time').value,
                enabled_actions: actions.filter(action => field(action === 'number' ? 'enabled' : action + '_enabled').checked),
                freeze_sheet_ids: Array.from(selected).sort((a, b) => a - b), selection_initialized: field('freeze_selection_initialized').value === '1',
            };
        }
        const fingerprint = () => JSON.stringify(settings());
        const dirty = () => fingerprint() !== savedFingerprint;
        const changedDocument = () => {
            const id = url => (url.match(/\/spreadsheets\/d\/([\w-]+)/) || [])[1] || '';
            return id(field('spreadsheet_url').value) !== id(view.profile.settings.spreadsheet_url);
        };

        function controls() {
            const locked = busy || view.busy;
            const pending = dirty();
            Object.values(buttons).forEach(button => { button.disabled = locked || pending || !view.profile.saved_at; });
            buttons.freeze.disabled ||= !selected.size;
            query('[data-week-freeze-preview]').disabled = buttons.freeze.disabled;
            query('[type="submit"]').disabled = locked;
            query('[data-freeze-refresh]').disabled = locked || changedDocument() || !view.profile.saved_at;
            form.querySelectorAll('input, select').forEach(input => { input.disabled = locked; });
            form.querySelectorAll('[data-freeze-sheet], [data-freeze-select-all], [data-freeze-clear-found], [data-freeze-clear]').forEach(input => { input.disabled = locked || changedDocument(); });
            query('[data-week-unsaved]').hidden = !pending;
            Object.values(buttons).forEach(button => { button.title = pending ? 'Сначала сохраните настройки этого блока' : ''; });
        }

        function replaceResult(action, html) {
            const container = query(`[data-week-${action}-results]`);
            if (markup.get(container) === html) return;
            const opened = new Set(Array.from(container.querySelectorAll('details[open]'), detail => detail.querySelector('summary').textContent));
            container.innerHTML = html;
            markup.set(container, html);
            container.querySelectorAll('details').forEach(detail => { if (opened.has(detail.querySelector('summary').textContent)) detail.open = true; });
        }

        function filterSheets() {
            const search = query('[data-freeze-search]').value.trim().toLocaleLowerCase('ru');
            let count = 0;
            query('[data-freeze-options]').querySelectorAll('label').forEach(option => {
                option.hidden = !option.textContent.toLocaleLowerCase('ru').includes(search);
                if (!option.hidden) count++;
            });
            query('[data-freeze-empty]').hidden = Boolean(count);
        }

        function renderSheets() {
            const options = view.profile.catalog.sheets.slice();
            const known = new Set(options.map(sheet => sheet.sheet_id));
            const missing = Array.from(selected).filter(id => !known.has(id));
            missing.forEach(id => options.push({ sheet_id: id, title: 'Лист #' + id + ' — удалён или недоступен', missing: true }));
            const container = query('[data-freeze-options]');
            const html = options.map(sheet => `<label${sheet.missing ? ' class="week-search-warning"' : ''}><input type="checkbox" data-freeze-sheet="${sheet.sheet_id}"${selected.has(sheet.sheet_id) ? ' checked' : ''}><span>${e(sheet.title)}</span></label>`).join('');
            if (container.dataset.render !== html) {
                const scroll = container.scrollTop;
                container.innerHTML = html;
                container.dataset.render = html;
                container.scrollTop = scroll;
            }
            query('[data-freeze-selected-count]').textContent = 'Выбрано: ' + selected.size;
            query('[data-freeze-selection-hint]').textContent = missing.length ? 'Недоступных выбранных листов: ' + missing.length + '. Снимите их выбор или обновите список.' : 'Добавление и удаление меняет только список обработки. Сами листы сохраняются.';
            filterSheets();
        }

        function fillFields() {
            const config = view.profile.settings;
            for (const name of ['spreadsheet_url', 'sheet_name', 'stock_sheet_name', 'weekday', 'run_time']) field(name).value = config[name];
            field('cells').value = config.cells.join(', ');
            actions.forEach(action => { field(action === 'number' ? 'enabled' : action + '_enabled').checked = config.enabled_actions.includes(action); });
            selected = new Set(config.freeze_sheet_ids);
            field('freeze_selection_initialized').value = config.selection_initialized ? '1' : '0';
            savedFingerprint = fingerprint();
        }

        function apply(next, fill = false) {
            const pending = savedFingerprint && dirty();
            view = next;
            if (fill || !pending) fillFields();
            const context = JSON.stringify([view.value, view.profile.settings.spreadsheet_url, view.profile.settings.freeze_sheet_ids]);
            if (previewContext !== context) preview = null;
            if (next.preview) { preview = next.preview; previewContext = context; }
            query('[data-week-value]').textContent = view.value;
            const link = query('[data-week-sheet-link]');
            link.href = view.profile.settings.spreadsheet_url || '#';
            link.hidden = !view.profile.settings.spreadsheet_url;
            for (const action of actions) {
                const state = view.profile.states[action];
                const schedule = query(action === 'number' ? '[data-week-schedule]' : `[data-week-${action}-schedule]`);
                schedule.textContent = !view.next_runs[action] ? 'По расписанию выключено' : !view.background_enabled ? 'Фоновые задачи отключены. Доступен ручной запуск.' : action === 'freeze' && view.freeze_waiting ? 'Ожидает полного завершения включённых выгрузок.' : 'Ближайший запуск: ' + date(view.next_runs[action]);
                const result = action === 'freeze' && preview ? preview : state.result;
                statusNodes[action].textContent = (result ? (result.dry_run ? 'Предварительная проверка: ' : 'Последний результат: ') + date(result.processed_at || state.last_success_at) + (result.value ? ' — ' + result.value : '') + (result.complete ? '' : '. Выполнено с замечаниями.') : 'Действие ещё не запускалось.') + (state.last_error ? ' ' + errorText(state.last_error) : '');
                statusNodes[action].classList.toggle('export-status--error', Boolean(state.last_error || result && !result.complete));
                if (action !== 'number') replaceResult(action, reportHtml(result, action));
            }
            const catalog = view.profile.catalog;
            const catalogStatus = query('[data-freeze-catalog-status]');
            catalogStatus.textContent = catalog.last_error ? errorText(catalog.last_error) : 'Список: ' + (date(catalog.updated_at) || 'ещё не обновлялся');
            catalogStatus.classList.toggle('export-status--error', Boolean(catalog.last_error));
            renderSheets();
            controls();
        }

        async function perform(path, options, button, statusNode = feedback) {
            if (busy || view.busy) return;
            busy = true;
            controls();
            const oldLabel = button.textContent;
            button.textContent = 'Выполняю…';
            button.setAttribute('aria-busy', 'true');
            statusNode.textContent = 'Выполняю…';
            statusNode.classList.remove('export-status--error');
            try {
                const next = await request(api + path, options);
                apply(next, options.method === 'PUT');
                if (statusNode === feedback) statusNode.textContent = 'Настройки сохранены';
            } catch (error) {
                try { apply(await request(api)); } catch (_) { }
                statusNode.textContent = error.message;
                statusNode.classList.add('export-status--error');
            } finally {
                busy = false;
                button.textContent = oldLabel;
                button.removeAttribute('aria-busy');
                controls();
            }
        }

        form.addEventListener('submit', async event => {
            event.preventDefault();
            const body = JSON.stringify(settings());
            preview = null;
            await perform('', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body }, query('[type="submit"]'));
            if (!dirty() && view.catalog_due) refreshCatalog();
        });
        function markDirty() {
            preview = null;
            feedback.textContent = 'Настройки общие для действий этого блока.';
            feedback.classList.remove('export-status--error');
            controls();
        }
        for (const event of ['input', 'change']) form.addEventListener(event, event => { if (event.target.name) markDirty(); });
        for (const action of actions) buttons[action].addEventListener('click', async () => {
            if (dirty() || !view.profile.saved_at) return;
            if (action === 'freeze') preview = null;
            await perform('/' + action, { method: 'POST' }, buttons[action], statusNodes[action]);
            const report = query(`[data-week-${action}-report]`);
            if (report) report.open = true;
        });
        query('[data-week-freeze-preview]').addEventListener('click', async () => {
            if (dirty() || !view.profile.saved_at) return;
            await perform('/freeze?preview=true', { method: 'POST' }, query('[data-week-freeze-preview]'), statusNodes.freeze);
            query('[data-week-freeze-report]').open = true;
        });
        query('[data-freeze-search]').addEventListener('input', filterSheets);
        function selectionChanged() {
            field('freeze_selection_initialized').value = '1';
            renderSheets();
            markDirty();
        }
        query('[data-freeze-options]').addEventListener('change', event => {
            if (!event.target.dataset.freezeSheet || busy || changedDocument()) return;
            const id = Number(event.target.dataset.freezeSheet);
            if (event.target.checked) selected.add(id); else selected.delete(id);
            selectionChanged();
        });
        for (const [selector, select, all] of [['[data-freeze-select-all]', true, false], ['[data-freeze-clear-found]', false, false], ['[data-freeze-clear]', false, true]]) {
            query(selector).addEventListener('click', () => {
                if (busy || changedDocument()) return;
                query('[data-freeze-options]').querySelectorAll('label').forEach(option => {
                    if (!all && option.hidden || select && option.classList.contains('week-search-warning')) return;
                    const id = Number(option.querySelector('input').dataset.freezeSheet);
                    if (select) selected.add(id); else selected.delete(id);
                });
                selectionChanged();
            });
        }
        async function refreshCatalog() {
            if (busy || view.busy || changedDocument() || !view.profile.saved_at) return;
            await perform('/catalog', { method: 'POST' }, query('[data-freeze-refresh]'), query('[data-freeze-catalog-status]'));
        }
        query('[data-freeze-refresh]').addEventListener('click', refreshCatalog);
        apply(initial, true);
        if (view.catalog_due) refreshCatalog();
        window.setInterval(async () => {
            if (busy || dirty() || document.hidden || form.closest('[hidden]')) return;
            try {
                const next = await request(api);
                if (!busy && !dirty()) {
                    apply(next);
                    if (view.catalog_due) refreshCatalog();
                }
            } catch (_) { }
        }, 30000);
    }

    request('/api/admin/google-weeks').then(views => {
        root.replaceChildren();
        views.forEach(card);
    }).catch(error => {
        root.textContent = error.message;
    });
})();
