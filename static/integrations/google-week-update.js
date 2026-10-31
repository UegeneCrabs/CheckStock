(() => {
    'use strict';
    const targets = document.querySelectorAll('[data-week-actions]');
    const template = document.querySelector('[data-week-card]');
    if (!targets.length || !template) return;
    const e = window.CheckStockUI.escapeHtml;
    const labels = { WB: ['wb', 'WB', 'FBO'], OZON: ['ozon', 'Ozon', 'FBO'], 'YANDEX MARKET': ['yandex', 'Яндекс Маркет', 'FBY'] };
    const actions = ['number', 'sales', 'stock', 'search', 'freeze'];
    const exportKinds = ['stocks', 'fbs_orders', 'fbo_transit'];
    const exportFields = { stock_sheet_name: 'report_sheet_name', orders_sheet_name: 'fbs_sheet_name', orders_quantity_column: 'fbs_quantity_column', fbo_sheet_name: 'fbo_sheet_name', fbo_to_customer_column: 'fbo_to_customer_column', fbo_from_customer_column: 'fbo_from_customer_column' };
    const weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
    const errors = {
        invalid_settings: 'Проверьте ссылку, названия листов, ячейки и расписание.', not_saved: 'Сначала сохраните настройки.',
        busy: 'Действие уже выполняется. Дождитесь его завершения.', credentials: 'Не настроен сервисный аккаунт Google.',
        google: 'Не удалось обратиться к Google Таблице. Проверьте доступ и повторите запуск.',
        source: 'Не удалось прочитать данные проекта. Запись для него пропущена.', sheet_missing: 'Лист удалён или недоступен.',
        source_incomplete: 'Не удалось подтвердить данные всех проектов. Запись в таблицу не запускалась.',
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
        export: 'Выгрузка не завершена.',
    };
    const date = value => value ? new Date(value).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' МСК' : '';
    const errorText = problem => !problem ? '' : problem.code === 'legacy' ? problem.detail :
        problem.code === 'export' && problem.detail ? problem.detail :
        problem.code === 'internal' && problem.detail === 'ValidationError' ? 'Сервер не смог обработать результат выгрузки. Данные могли быть записаны в таблицу. Повторите запуск после обновления страницы.' :
        (errors[problem.code] || problem.code || 'Не удалось выполнить запрос.') +
        (problem.code === 'source_incomplete' && problem.detail ? ' Проект: ' + problem.detail + '.' : '') +
        (problem.cell ? ' Ячейка: ' + problem.cell + '.' : '') + (problem.status ? ' HTTP ' + problem.status + '.' : '');
    const reason = value => errors[value] || value;

    function freezeOutcome(result) {
        const issues = (result.sheets || []).filter(sheet => sheet.issue);
        const incomplete = !result.complete || issues.length > 0;
        const written = result.written_cells || 0;
        if (result.dry_run) return {
            tone: incomplete ? 'error' : 'success',
            text: incomplete ? 'Проверка не пройдена' : 'Проверка пройдена',
            message: `Формул для фиксации: ${result.total_formulas || 0}. Проверка не изменяет таблицу.`,
            issues,
        };
        if (incomplete) return {
            tone: written ? 'warning' : 'error',
            text: written ? 'Выполнено частично' : 'Фиксация не выполнена',
            message: written ? `Зафиксировано формул: ${written}. Остальные листы не обработаны.` : 'Зафиксировано формул: 0. Ни одна формула не заменена значением.',
            issues,
        };
        return {
            tone: written ? 'success' : 'idle',
            text: written ? 'Зафиксировано' : 'Изменения не требуются',
            message: written ? `Зафиксировано формул: ${written}.` : 'В прошлых неделях нет формул для фиксации.',
            issues,
        };
    }

    function renderStatus(node, state = {}, result = null, { pending = false, requestError = '', action = '' } = {}) {
        const failure = pending ? '' : requestError || (result?.dry_run ? '' : errorText(state.last_error));
        const freeze = action === 'freeze' && result && !pending && !failure ? freezeOutcome(result) : null;
        const tone = pending ? 'idle' : failure ? 'error' : freeze ? freeze.tone : !result ? 'idle' : result.complete ? 'success' : 'warning';
        const text = pending ? 'Не сохранено' : failure ? 'Ошибка запуска' : freeze ? freeze.text : !result ? 'Ещё не запускалось' : result.dry_run ? result.complete ? 'Проверка пройдена' : 'Проверка: есть замечания' : result.complete ? 'Завершено' : 'Есть замечания';
        const when = pending ? null : failure ? state.last_error ? state.last_attempt_at : null : result && (result.processed_at || state.last_success_at);
        const message = pending ? 'Сохраните настройки площадки перед выгрузкой.' : failure || freeze?.message;
        const issues = freeze ? freeze.issues.map(sheet => `<span class="week-status-message">${e(sheet.sheet_name || 'Лист #' + sheet.sheet_id)}: ${e(reason(sheet.issue))}</span>`).join('') : '';
        const html = `<span class="week-status-line"><span class="week-result-badge">${text}</span>${when ? `<time>${e(date(when))}</time>` : ''}</span>${message ? `<span class="week-status-message">${e(message)}</span>` : ''}${issues}`;
        node.dataset.outcome = tone;
        node.classList.toggle('export-status--error', tone === 'error');
        if (node.innerHTML !== html) node.innerHTML = html;
    }

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

    function card(initial, group, target, actionsOnly = true) {
        const market = initial.profile.marketplace;
        const [marketPrefix, label, scheme] = labels[market];
        const prefix = actionsOnly ? marketPrefix + '-actions' : marketPrefix;
        const salesDescription = market === 'OZON'
            ? 'Заказы FBO + FBS по дате «Принят» (МСК), кроме отменённых, по всем найденным неделям.'
            : 'Заказы за вычетом отмен по всем найденным неделям.';
        const values = { marketplace: label, prefix, stock_scheme: scheme, sales_description: salesDescription, weekdays: weekdays.map((day, index) => `<option value="${index}">${day}</option>`).join('') };
        const wrapper = document.createElement('div');
        wrapper.innerHTML = template.innerHTML.replace(/\$([a-zA-Z_]\w*)/g, (_, name) => values[name] || '');
        const form = wrapper.querySelector('form');
        form.dataset.marketplace = market;
        {
            const salesDestination = document.createElement('label');
            salesDestination.className = 'week-field';
            salesDestination.innerHTML = '<span>Лист недельных заказов</span><input class="input-control" name="sales_sheet_name" required maxlength="100">';
            form.querySelector('.week-fields').append(salesDestination);
            form.elements.namedItem('sheet_name').closest('label').querySelector('span').textContent = 'Лист для номера недели';
        }
        if (actionsOnly) {
            form.classList.add('week-update--actions');
            form.removeAttribute('aria-labelledby');
            form.setAttribute('aria-label', 'Действия ' + label);
            for (const selector of [
                '.week-update-head', '.week-settings', '[data-week-search-panel]', '.week-help',
                '.week-action-description', '.week-action-note', '.week-freeze-selection > .week-update-hint',
                '[data-week-freeze-schedule]', '[data-week-freeze-report]',
            ]) {
                form.querySelectorAll(selector).forEach(element => { element.hidden = true; });
            }
            const footer = form.querySelector('.week-settings-footer');
            form.querySelector('.week-actions-heading > span').remove();
            for (const action of ['sales', 'stock']) {
                const report = form.querySelector(`[data-week-${action}-report]`);
                const panel = document.createElement('div');
                panel.className = 'week-report';
                panel.setAttribute(`data-week-${action}-report`, '');
                panel.append(report.querySelector(`[data-week-${action}-results]`));
                report.replaceWith(panel);
            }
            const spreadsheetInput = form.elements.namedItem('spreadsheet_url');
            spreadsheetInput.placeholder = 'https://docs.google.com/spreadsheets/d/…';
            form.prepend(spreadsheetInput.closest('.week-field'));
            spreadsheetInput.closest('.week-field').after(footer);
            {
                const timing = form.querySelector('.week-timing');
                timing.querySelector('h3').textContent = 'Расписание ' + label;
                timing.querySelector('h3 + p').textContent = 'Выполняются только отмеченные действия.';
                const frequency = document.createElement('label');
                frequency.className = 'week-field';
                frequency.innerHTML = '<span>Периодичность</span><select class="integration-select" name="schedule_kind"><option value="daily">Ежедневно</option><option value="weekly">Еженедельно</option></select>';
                timing.querySelector('.week-time-fields').prepend(frequency);
                timing.querySelector('.week-update-hint').textContent = 'Сначала обновляется номер недели, затем выполняются выгрузки. Фиксация — после их успешного завершения.';
                const scheduleStatus = document.createElement('p');
                scheduleStatus.className = 'week-next-run';
                scheduleStatus.setAttribute('data-marketplace-schedule-status', '');
                scheduleStatus.setAttribute('role', 'status');
                timing.append(scheduleStatus);
                form.querySelector('.week-actions-heading').before(timing);
                const destination = document.createElement('div');
                destination.className = 'week-number-fields';
                for (const [name, title] of [['sheet_name', 'Лист для номера недели'], ['cells', 'Ячейки для записи недели']]) {
                    const input = form.elements.namedItem(name);
                    const label = input.closest('.week-field');
                    label.querySelector('span').textContent = title;
                    destination.append(label);
                }
                form.querySelector('.week-value').before(destination);
                for (const [action, name, title] of [
                    ['sales', 'sales_sheet_name', 'Лист недельных заказов'],
                    ['stock', 'stock_sheet_name', 'Лист остатков ' + label + ' ' + scheme],
                ]) {
                    const field = form.elements.namedItem(name).closest('.week-field');
                    field.querySelector('span').textContent = title;
                    form.querySelector(`[data-week-${action}]`).closest('.week-action').querySelector('.week-action-description').before(field);
                }
                form.querySelector('#' + prefix + '-week-sales-title').textContent = 'Заказы ' + label + ' Недельный';
                const search = form.querySelector('[data-week-search-panel]');
                search.hidden = false;
                search.classList.remove('week-action');
                search.classList.add('week-search-tools');
                const advanced = document.createElement('details');
                const advancedTitle = document.createElement('summary');
                advancedTitle.textContent = 'Проверка недель';
                advanced.append(advancedTitle, search);
                form.querySelector('[data-week-sales]').closest('.week-action').append(advanced);
            }
            form.querySelector('.week-value > span').textContent = 'неделя для записи';
            form.querySelector('[data-week-stock]').textContent = 'Выгрузить остатки';
            form.querySelector('[data-freeze-refresh]').textContent = 'Обновить листы';
            form.querySelector('[data-week-freeze-preview]').textContent = 'Проверить';
            const freezeButton = form.querySelector('[data-week-freeze]');
            freezeButton.textContent = 'Зафиксировать';
            freezeButton.classList.replace('btn-secondary', 'btn-primary');
            for (const section of form.querySelectorAll('.week-action')) {
                const head = document.createElement('div');
                head.className = 'week-compact-head';
                const controls = document.createElement('div');
                controls.className = 'week-compact-controls';
                const toggle = section.querySelector('.week-update-switch');
                toggle.querySelector('span:last-child').textContent = 'Автозапуск';
                controls.append(toggle);
                const run = section.querySelector('.week-run-button');
                if (run) controls.append(run);
                head.append(section.querySelector('.week-action-title'), controls);
                section.prepend(head);
            }
            form.append(footer);
        }
        target.append(form);
        const field = name => form.elements.namedItem(name);
        const query = selector => form.querySelector(selector);
        const api = '/api/admin/google-weeks/' + encodeURIComponent(market);
        const statusNodes = Object.fromEntries(actions.map(action => [action, query(action === 'number' ? '[data-week-status]' : `[data-week-${action}-status]`)]));
        const buttons = Object.fromEntries(actions.map(action => [action, query(action === 'number' ? '[data-week-run]' : `[data-week-${action}]`)]));
        const exportButtons = Object.fromEntries(exportKinds.map(kind => [kind, query(`[data-week-export="${kind}"]`)]));
        const feedback = query('[data-week-save-status]');
        let view = initial;
        let savedFingerprint = '';
        let selected = new Set();
        let preview = null;
        let previewContext = '';
        let scheduleManaged = false;
        const markup = new WeakMap();

        function settings() {
            return {
                spreadsheet_url: field('spreadsheet_url').value.trim(), sheet_name: field('sheet_name').value.trim(),
                sales_sheet_name: field('sales_sheet_name') ? field('sales_sheet_name').value.trim() : view.profile.settings.sales_sheet_name || '',
                stock_sheet_name: field('stock_sheet_name').value.trim(), cells: field('cells').value.trim().toUpperCase().split(/[\s,;]+/),
                weekday: Number(field('weekday').value), run_time: field('run_time').value,
                enabled_actions: actions.filter(action => field(action === 'number' ? 'enabled' : action + '_enabled').checked),
                schedule_managed: scheduleManaged,
                export_actions: exportKinds.filter(kind => field('export_' + kind + '_enabled').checked),
                export_target: Object.fromEntries(Object.entries(exportFields).map(([key, name]) => [key, field(name).value.trim()])),
                schedule_kind: field('schedule_kind').value,
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
            const locked = group.busy || view.busy;
            const pending = dirty();
            Object.values(buttons).forEach(button => { button.disabled = locked || pending || !scheduleManaged || !view.profile.saved_at; });
            buttons.freeze.disabled ||= !selected.size;
            query('[data-week-freeze-preview]').disabled = buttons.freeze.disabled;
            query('[type="submit"]').disabled = locked || !pending && Boolean(view.profile.saved_at && scheduleManaged);
            for (const kind of exportKinds) {
                const name = { stocks: 'report_sheet_name', fbs_orders: 'fbs_sheet_name', fbo_transit: 'fbo_sheet_name' }[kind];
                exportButtons[kind].disabled = locked || pending || !scheduleManaged || !view.profile.saved_at || !field(name).value.trim();
            }
            query('[data-freeze-refresh]').disabled = locked || changedDocument() || !view.profile.saved_at;
            form.querySelectorAll('input, select').forEach(input => { if (input.form === form) input.disabled = locked; });
            form.querySelectorAll('[data-freeze-sheet], [data-freeze-select-all], [data-freeze-clear-found], [data-freeze-clear]').forEach(input => { input.disabled = locked || changedDocument(); });
            query('[data-week-unsaved]').hidden = !pending && scheduleManaged && Boolean(view.profile.saved_at);
            if (actionsOnly) {
                form.setAttribute('aria-busy', String(locked));
                {
                    field('weekday').closest('label').hidden = settings().schedule_kind !== 'weekly';
                    const next = [...Object.values(view.next_runs), ...Object.values(view.export_next_runs || {})].filter(Boolean).sort();
                    query('[data-marketplace-schedule-status]').textContent = pending ? 'Изменения расписания не сохранены.' : !scheduleManaged || !view.profile.saved_at ? 'Сохраните настройки, чтобы использовать расписание этого блока.' :
                        !view.background_enabled ? 'Фоновые задачи отключены. Доступен ручной запуск.' :
                        next.length ? 'Ближайший запуск: ' + date(next[0]) : 'Отметьте действия для запуска по расписанию.';
                }
                if (!group.busy && !feedback.classList.contains('export-status--error')) {
                    feedback.textContent = pending || !scheduleManaged || !view.profile.saved_at ? 'Сохраните настройки площадки перед запуском.' : 'Настройки сохранены';
                }
            }
            Object.values(buttons).forEach(button => { button.title = pending ? 'Сначала сохраните настройки этого блока' : ''; });
            if (!group.busy) updateExportStatuses();
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
            const catalogLoaded = Boolean(view.profile.catalog.updated_at);
            missing.forEach(id => options.push({ sheet_id: id, title: 'Лист #' + id + (catalogLoaded ? ' — удалён или недоступен' : ' — список ещё не обновлён'), missing: catalogLoaded }));
            const container = query('[data-freeze-options]');
            const html = options.map(sheet => `<label${sheet.missing ? ' class="week-search-warning"' : ''}><input type="checkbox" data-freeze-sheet="${sheet.sheet_id}"${selected.has(sheet.sheet_id) ? ' checked' : ''}><span>${e(sheet.title)}</span></label>`).join('');
            if (container.dataset.render !== html) {
                const scroll = container.scrollTop;
                container.innerHTML = html;
                container.dataset.render = html;
                container.scrollTop = scroll;
            }
            query('[data-freeze-selected-count]').textContent = 'Выбрано: ' + selected.size;
            query('[data-freeze-selection-hint]').textContent = missing.length && catalogLoaded ? 'Недоступных выбранных листов: ' + missing.length + '. Снимите их выбор или обновите список.' : 'Добавление и удаление меняет только список обработки. Сами листы сохраняются.';
            filterSheets();
        }

        function setFields(config) {
            field('schedule_kind').value = config.schedule_kind || 'weekly';
            scheduleManaged = Boolean(config.schedule_managed);
            const destination = { stock_sheet_name: '', orders_sheet_name: '', orders_quantity_column: 'C', fbo_sheet_name: '', fbo_to_customer_column: 'J', fbo_from_customer_column: 'L', ...config.export_target };
            for (const [key, name] of Object.entries(exportFields)) field(name).value = destination[key];
            exportKinds.forEach(kind => { field('export_' + kind + '_enabled').checked = (config.export_actions || []).includes(kind); });
            for (const name of ['spreadsheet_url', 'sheet_name', 'stock_sheet_name', 'weekday', 'run_time']) field(name).value = config[name];
            if (field('sales_sheet_name')) field('sales_sheet_name').value = config.sales_sheet_name || config.sheet_name;
            field('cells').value = config.cells.join(', ');
            actions.forEach(action => { field(action === 'number' ? 'enabled' : action + '_enabled').checked = config.enabled_actions.includes(action); });
            selected = new Set(config.freeze_sheet_ids);
            field('freeze_selection_initialized').value = config.selection_initialized ? '1' : '0';
        }

        function fillFields() {
            setFields(view.profile.settings);
            savedFingerprint = fingerprint();
        }

        function compactStatus(action, state, result) {
            renderStatus(statusNodes[action], state, result, { action });
        }

        function updateExportStatuses() {
            const pending = dirty() || !scheduleManaged || !view.profile.saved_at;
            for (const kind of exportKinds) {
                const node = query(`[data-export-result="${kind}"]`);
                const state = view.profile.export_states?.[kind] || {};
                const result = state.result;
                const warnings = result?.warnings || [];
                const open = Boolean(node.querySelector('details[open]'));
                if (pending) delete node.dataset.requestError;
                const failed = Boolean(state.last_error || node.dataset.requestError);
                renderStatus(node, state, warnings.length && result ? { ...result, complete: false } : result,
                    { pending, requestError: node.dataset.requestError || '' });
                if (!pending && result) {
                    const summary = document.createElement('span');
                    summary.className = failed ? 'week-previous-result' : 'week-status-message';
                    summary.textContent = (failed ? 'Предыдущая выгрузка. ' : '') + 'Записано ячеек: ' + result.written_cells;
                    node.append(summary);
                }
                if (!pending && warnings.length) {
                    const details = document.createElement('details');
                    details.open = open;
                    const heading = document.createElement('summary');
                    heading.textContent = (failed ? 'Предупреждения предыдущей выгрузки: ' : 'Предупреждения: ') + warnings.length;
                    const list = document.createElement('ul');
                    for (const warning of warnings) {
                        const item = document.createElement('li');
                        item.textContent = warning;
                        list.append(item);
                    }
                    details.append(heading, list);
                    node.append(details);
                }
            }
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
                if (actionsOnly) schedule.hidden = true;
                const result = action === 'freeze' && preview ? preview : state.result;
                if (actionsOnly || action === 'freeze') compactStatus(action, state, result);
                else {
                    statusNodes[action].textContent = (result ? (result.dry_run ? 'Предварительная проверка: ' : 'Последний результат: ') + date(result.processed_at || state.last_success_at) + (result.value ? ' — ' + result.value : '') + (result.complete ? '' : '. Выполнено с замечаниями.') : 'Действие ещё не запускалось.') + (state.last_error ? ' ' + errorText(state.last_error) : '');
                    statusNodes[action].classList.toggle('export-status--error', Boolean(state.last_error || result && !result.complete));
                }
                if (action !== 'number') replaceResult(action,
                    actionsOnly && (action === 'sales' || action === 'stock')
                        ? window.CheckStockWeekReport.summaryHtml(result, action, Boolean(state.last_error)) : reportHtml(result, action));
                if (actionsOnly && (action === 'sales' || action === 'stock')) query(`[data-week-${action}-report]`).hidden = !result;
            }
            const catalog = view.profile.catalog;
            const catalogStatus = query('[data-freeze-catalog-status]');
            catalogStatus.textContent = catalog.last_error ? errorText(catalog.last_error) : 'Список: ' + (date(catalog.updated_at) || 'ещё не обновлялся');
            catalogStatus.classList.toggle('export-status--error', Boolean(catalog.last_error));
            renderSheets();
            controls();
        }

        async function perform(path, options, button, statusNode = feedback) {
            if (group.busy || view.busy) return;
            group.busy = true;
            group.cards.forEach(card => card.controls());
            const oldLabel = button.textContent;
            button.textContent = 'Выполняю…';
            button.setAttribute('aria-busy', 'true');
            statusNode.textContent = 'Выполняю…';
            delete statusNode.dataset.requestError;
            statusNode.classList.remove('export-status--error');
            if (actionsOnly && statusNode.classList.contains('week-action-status')) statusNode.dataset.outcome = 'running';
            try {
                const next = await request(api + path, options);
                group.cards.forEach(card => card.apply(next, options.method === 'PUT'));
                if (statusNode === feedback) statusNode.textContent = 'Настройки сохранены';
                return true;
            } catch (error) {
                try {
                    const next = await request(api);
                    group.cards.forEach(card => card.apply(next));
                } catch (_) { }
                statusNode.textContent = error.message;
                statusNode.dataset.requestError = error.message;
                statusNode.classList.add('export-status--error');
                if (actionsOnly && statusNode.classList.contains('week-action-status')) statusNode.dataset.outcome = 'error';
                return false;
            } finally {
                group.busy = false;
                button.textContent = oldLabel;
                button.removeAttribute('aria-busy');
                group.cards.forEach(card => card.controls());
            }
        }

        async function saveSettings() {
            if (group.busy || view.busy) return false;
            scheduleManaged = true;
            const body = JSON.stringify(settings());
            preview = null;
            const saved = await perform('', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body }, query('[type="submit"]'));
            if (!dirty() && view.catalog_due) refreshCatalog();
            return saved;
        }
        form.addEventListener('submit', async event => {
            event.preventDefault();
            await saveSettings();
        });
        function markDirty() {
            preview = null;
            feedback.textContent = 'Настройки общие для действий этого блока.';
            feedback.classList.remove('export-status--error');
            controls();
        }
        function syncDraft(draft) {
            setFields(draft);
            renderSheets();
            markDirty();
        }
        function draftChanged() {
            scheduleManaged = true;
            const draft = settings();
            group.cards.forEach(card => { if (card.syncDraft !== syncDraft) card.syncDraft(draft); });
            markDirty();
        }
        for (const event of ['input', 'change']) form.addEventListener(event, event => { if (event.target.name) draftChanged(); });
        form.addEventListener('click', event => {
            const button = event.target.closest('[data-week-products]');
            if (!button) return;
            const action = button.dataset.weekProducts;
            const title = action === 'sales' ? 'Заказы ' + label + ' Недельный' : 'Остатки ' + label;
            window.CheckStockWeekReport.show(view.profile.states[action].result,
                title, reason, button.dataset.weekProductFilter || 'all', market);
        });
        for (const action of actions) buttons[action].addEventListener('click', async () => {
            if (dirty() || !view.profile.saved_at) return;
            if (action === 'freeze') preview = null;
            await perform('/' + action, { method: 'POST' }, buttons[action], statusNodes[action]);
            const report = query(`[data-week-${action}-report]`);
            if (report && report.tagName === 'DETAILS') report.open = true;
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
            draftChanged();
        }
        query('[data-freeze-options]').addEventListener('change', event => {
            if (!event.target.dataset.freezeSheet || group.busy || changedDocument()) return;
            const id = Number(event.target.dataset.freezeSheet);
            if (event.target.checked) selected.add(id); else selected.delete(id);
            selectionChanged();
        });
        for (const [selector, select, all] of [['[data-freeze-select-all]', true, false], ['[data-freeze-clear-found]', false, false], ['[data-freeze-clear]', false, true]]) {
            query(selector).addEventListener('click', () => {
                if (group.busy || changedDocument()) return;
                query('[data-freeze-options]').querySelectorAll('label').forEach(option => {
                    if (!all && option.hidden || select && option.classList.contains('week-search-warning')) return;
                    const id = Number(option.querySelector('input').dataset.freezeSheet);
                    if (select) selected.add(id); else selected.delete(id);
                });
                selectionChanged();
            });
        }
        async function refreshCatalog() {
            if (group.busy || view.busy || changedDocument() || !view.profile.saved_at) return;
            await perform('/catalog', { method: 'POST' }, query('[data-freeze-refresh]'), query('[data-freeze-catalog-status]'));
        }
        query('[data-freeze-refresh]').addEventListener('click', refreshCatalog);
        async function runExport(kind, button, statusNode) {
            if (dirty() || !view.profile.saved_at) return;
            await perform('/' + kind, { method: 'POST' }, button, statusNode);
        }
        for (const kind of exportKinds) exportButtons[kind].addEventListener('click', () => runExport(kind, exportButtons[kind], query(`[data-export-result="${kind}"]`)));
        group.cards.push({ apply, controls, syncDraft, settings, saveSettings, dirty, runExport, getView: () => view, isBusy: () => group.busy || view.busy });
        apply(initial, true);
        if (view.catalog_due) refreshCatalog();
        window.setInterval(async () => {
            if (group.busy || dirty() || document.hidden || form.closest('[hidden]')) return;
            try {
                const next = await request(api);
                if (!group.busy && !dirty()) {
                    group.cards.forEach(card => card.apply(next));
                    if (view.catalog_due) refreshCatalog();
                }
            } catch (_) { }
        }, 30000);
    }

    request('/api/admin/google-weeks').then(views => {
        for (const view of views) {
            const group = { busy: false, cards: [] };
            const target = document.querySelector(`[data-week-actions="${view.profile.marketplace}"]`);
            if (target) {
                target.replaceChildren();
                card(view, group, target, true);
            }
        }
    }).catch(error => {
        document.querySelectorAll('[data-week-actions]').forEach(target => { target.textContent = error.message; });
    });
})();
