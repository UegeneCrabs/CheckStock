(() => {
    'use strict';
    let root = document.getElementById('admin-app');
    if (!root) return;
    const state = {
        tab: 'users',
        query: '',
        role: '',
        status: '',
        store: '',
        sort: 'name',
        page: 1,
        log: '',
        requests: '',
    };
    const pageSize = 15;
    const baselines = new WeakMap();
    let pending = false;
    let opener = null;
    let toastTimer;
    const one = (selector, scope = root) => scope.querySelector(selector);
    const all = (selector, scope = root) => Array.from(scope.querySelectorAll(selector));
    const normalize = (value) => value.toLocaleLowerCase('ru').replace(/ё/g, 'е').trim();
    const formValue = (form) => JSON.stringify(Array.from(new FormData(form).entries()));
    const dirty = (form) => baselines.has(form) && baselines.get(form) !== formValue(form);
    const dirtyForms = () => all('dialog[open] form').filter(dirty);

    function toast(message, error = false) {
        const box = one('#ad-toast');
        clearTimeout(toastTimer);
        box.textContent = message;
        box.classList.toggle('is-error', error);
        box.hidden = false;
        toastTimer = setTimeout(
            () => {
                box.hidden = true;
            },
            error ? 10000 : 4500,
        );
    }

    async function post(url, body = new FormData()) {
        const response = await fetch(url, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } });
        let result;
        try {
            result = await response.json();
        } catch (_) {
            throw new Error('Сервер не вернул результат. Обновите страницу, чтобы проверить состояние.');
        }
        if (!response.ok || !result.ok) {
            const error = new Error(
                result.error || 'Не удалось выполнить действие. Проверьте соединение и попробуйте снова.',
            );
            error.field = result.field;
            throw error;
        }
        return result;
    }

    function selectTab(tab) {
        const control = one(`[data-tab="${tab}"]`);
        state.tab = control && !control.hidden ? tab : 'users';
        all('[data-tab]').forEach((button) => {
            const selected = button.dataset.tab === state.tab;
            button.setAttribute('aria-selected', String(selected));
            button.tabIndex = selected ? 0 : -1;
        });
        all('[data-pane]').forEach((pane) => {
            pane.hidden = pane.dataset.pane !== state.tab;
        });
    }

    function filterUsers() {
        const rows = all('[data-user-row]');
        const terms = normalize(state.query).split(/\s+/).filter(Boolean);
        const roles = { superadmin: 0, admin: 1, user: 2 };
        const filtered = rows
            .filter((row) => {
                row.hidden = true;
                return (
                    terms.every((term) => normalize(row.dataset.search).includes(term)) &&
                    (!state.role || row.dataset.role === state.role) &&
                    (!state.status || row.dataset.status === state.status) &&
                    (!state.store || row.dataset.stores.split(' ').includes(state.store))
                );
            })
            .sort((a, b) => {
                const names = a.dataset.name.localeCompare(b.dataset.name, 'ru');
                if (state.sort === 'newest') return Number(b.dataset.userId) - Number(a.dataset.userId);
                if (state.sort === 'role') return roles[a.dataset.role] - roles[b.dataset.role] || names;
                return names;
            });
        const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
        state.page = Math.min(Math.max(1, state.page), pages);
        const start = (state.page - 1) * pageSize;
        filtered.forEach((row, index) => {
            one('#ad-user-rows').appendChild(row);
            row.hidden = index < start || index >= start + pageSize;
        });
        one('#ad-user-count').textContent = `Найдено сотрудников: ${filtered.length} из ${rows.length}`;
        one('#ad-page-label').textContent = filtered.length
            ? `${start + 1}–${Math.min(start + pageSize, filtered.length)} из ${filtered.length} · Страница ${state.page} из ${pages}`
            : 'Нет записей';
        one('#ad-prev').disabled = state.page === 1;
        one('#ad-next').disabled = state.page === pages;
        one('#ad-users-empty').hidden = filtered.length > 0;
        one('#ad-reset-filters').hidden = !(state.query || state.role || state.status || state.store);
    }

    function resetFilters() {
        Object.assign(state, { query: '', role: '', status: '', store: '', page: 1 });
        syncFilters();
        filterUsers();
    }

    function syncFilters() {
        for (const [key, id] of Object.entries({
            query: 'ad-user-search',
            role: 'ad-filter-role',
            status: 'ad-filter-status',
            store: 'ad-filter-store',
            sort: 'ad-sort',
        })) {
            one(`#${id}`).value = state[key];
        }
    }

    function filterTable(key) {
        const rows = all(`#ad-${key}-table tbody tr:not(.empty-row)`);
        const terms = normalize(state[key]).split(/\s+/).filter(Boolean);
        rows.forEach((row) => {
            row.hidden = !terms.every((term) => normalize(row.textContent).includes(term));
        });
        const count = rows.filter((row) => !row.hidden).length;
        one(`[data-table-result="${key}"]`).textContent = rows.length
            ? count
                ? `Показано записей: ${count} из ${rows.length}`
                : 'По вашему запросу ничего не найдено.'
            : '';
    }

    function confirmation(options) {
        const dialog = one('#ad-confirm-dialog');
        if (dialog.open) return Promise.resolve(false);
        const ok = one('#ad-confirm-ok');
        const cancel = one('#ad-confirm-cancel');
        const status = one('#ad-confirm-status');
        one('#ad-confirm-title').textContent = options.title;
        one('#ad-confirm-text').textContent = options.text;
        one('#ad-confirm-password').hidden = !options.password;
        one('#ad-confirm-password input').value = options.password || '';
        one('#ad-confirm-copy').textContent = 'Скопировать пароль';
        ok.textContent = options.label || 'Подтвердить';
        ok.classList.toggle('ad-button--danger', Boolean(options.danger));
        ok.classList.toggle('ad-button--primary', !options.danger);
        cancel.hidden = options.cancel === false;
        status.textContent = '';
        status.classList.remove('is-error');
        dialog.showModal();
        (options.danger ? cancel : ok).focus();
        return new Promise((resolve) => {
            let busy = false;
            const finish = (result) => {
                if (busy) return;
                dialog.close();
                dialog.oncancel = null;
                ok.onclick = null;
                cancel.onclick = null;
                one('#ad-confirm-password input').value = '';
                resolve(result);
            };
            dialog.oncancel = (event) => {
                event.preventDefault();
                if (options.cancel !== false) finish(false);
            };
            cancel.onclick = () => finish(false);
            ok.onclick = async () => {
                if (busy) return;
                try {
                    if (options.action) {
                        busy = true;
                        pending = true;
                        ok.disabled = cancel.disabled = true;
                        status.textContent = 'Выполняю…';
                        await options.action();
                    }
                    busy = false;
                    finish(true);
                } catch (error) {
                    status.textContent = error.message || 'Нет соединения с сервером.';
                    status.classList.add('is-error');
                } finally {
                    busy = pending = false;
                    ok.disabled = cancel.disabled = false;
                }
            };
        });
    }

    async function requestClose(dialog) {
        if (pending || one('#ad-confirm-dialog').open) return;
        if (all('form', dialog).some(dirty)) {
            const discard = await confirmation({
                title: 'Закрыть без сохранения?',
                text: 'В форме есть несохранённые изменения. Они будут потеряны.',
                label: 'Не сохранять',
                danger: true,
            });
            if (!discard) return;
        }
        dialog.close();
        if (dialog.id === 'u-create-overlay') {
            one('#ad-create-form').reset();
            initializeCreate();
        }
        if (opener?.isConnected) opener.focus();
    }

    function updatePolicy(form) {
        const storeButton = one('[data-select-stores]', form);
        if (storeButton)
            storeButton.textContent = all('[name="stores"]', form).every((input) => input.checked)
                ? 'Снять выбор'
                : 'Выбрать все';
        const profile = one('[name="access_profile"]', form);
        const group = one('[data-marketplaces]', form);
        if (!profile || !group) return;
        group.hidden = false;
        const description = one('[data-profile-description]', form);
        if (description) description.textContent = profile.selectedOptions[0]?.dataset.description || '';
        const preview = one('[data-working-scope]', form);
        if (preview) {
            const stores = all('[name="stores"]:checked', form).map((input) =>
                input.closest('label').textContent.trim(),
            );
            const markets = all('[name="marketplaces"]:checked', form).map((input) => input.value);
            preview.textContent =
                stores.length && markets.length
                    ? `Рабочая область: ${stores.join(', ')}. В каждом кабинете: ${markets.join(', ')}.`
                    : 'Выберите хотя бы один кабинет и одну площадку.';
        }
        one('[data-policy-hint]', form).textContent =
            'Для любой должности можно выбрать одну или несколько площадок.';
    }

    function updateDirty(form) {
        const changed = dirty(form);
        const button = one('[type="submit"]', form);
        if (button && form.dataset.endpoint) button.disabled = !changed || pending;
        const status = one('.ad-form-state', form);
        if (status) {
            status.textContent = changed ? 'Есть несохранённые изменения' : '';
            status.classList.remove('is-error');
        }
    }

    function editorTab(tab) {
        all('[data-editor-tab]').forEach((button) => {
            button.classList.toggle('is-active', button.dataset.editorTab === tab);
            button.setAttribute('aria-pressed', String(button.dataset.editorTab === tab));
        });
        all('[data-editor-pane]').forEach((pane) => {
            pane.hidden = pane.dataset.editorPane !== tab;
        });
    }

    function openUser(id, tab = 'access') {
        const template = one(`#ad-user-${id}`);
        if (!template) return;
        one('#ad-editor-content').replaceChildren(template.content.cloneNode(true));
        const row = one(`[data-user-row][data-user-id="${id}"]`);
        if (!opener?.isConnected) opener = one('[data-open-user]', row);
        one('#ad-user-title').textContent = row.dataset.name;
        all('.ad-edit-form').forEach((form) => {
            updatePolicy(form);
            baselines.set(form, formValue(form));
        });
        editorTab(tab);
        one('#ad-user-dialog').showModal();
    }

    function validatePolicy(form) {
        if (form.querySelector('[name="stores"]') && !one('[name="stores"]:checked', form)) {
            throw Object.assign(new Error('Выберите хотя бы один кабинет.'), { field: 'stores' });
        }
        if (!form.querySelector('[name="marketplaces"]')) return;
        const count = all('[name="marketplaces"]:checked', form).length;
        if (!count)
            throw Object.assign(new Error('Выберите хотя бы один маркетплейс.'), { field: 'marketplaces' });
    }

    async function refresh(options = {}) {
        const response = await fetch('/admin', {
            headers: { 'X-Requested-With': 'fetch' },
            cache: 'no-store',
        });
        if (!response.ok) throw new Error('Изменения сохранены, но список не обновился. Обновите страницу.');
        const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
        const replacement = documentCopy.getElementById('admin-app');
        if (!replacement) throw new Error('Изменения сохранены. Обновите страницу и войдите снова.');
        const drafts = options.drafts || [];
        all('dialog[open]').forEach((dialog) => dialog.close());
        root.replaceWith(replacement);
        root = replacement;
        initialize();
        if (options.userId && one(`#ad-user-${options.userId}`)) {
            openUser(options.userId, options.editorTab);
            for (const draft of drafts) {
                const form = one(`.ad-edit-form[data-endpoint="${draft.endpoint}"]:not([hidden])`);
                if (!form) continue;
                all('[name]', form).forEach((input) => {
                    const values = draft.entries
                        .filter((entry) => entry[0] === input.name)
                        .map((entry) => entry[1]);
                    if (input.type === 'checkbox') input.checked = values.includes(input.value);
                    else if (values.length) input.value = values[0];
                });
                updatePolicy(form);
                updateDirty(form);
            }
        }
    }

    function otherDrafts(form) {
        return all('.ad-edit-form')
            .filter((item) => item !== form && dirty(item))
            .map((item) => ({
                endpoint: item.dataset.endpoint,
                entries: Array.from(new FormData(item).entries()),
            }));
    }

    async function saveEditor(form) {
        if (pending || !dirty(form)) return;
        const status = one('.ad-form-state', form);
        let saved = false;
        try {
            validatePolicy(form);
            const drafts = otherDrafts(form);
            if (['role', 'access-policy'].includes(form.dataset.endpoint) && drafts.length) {
                if (
                    !(await confirmation({
                        title: 'Изменить модель доступа?',
                        text: 'В других блоках есть несохранённые изменения. Совместимые настройки останутся в форме. Настройки, недоступные для новой роли или должности, будут сброшены.',
                        label: 'Продолжить',
                    }))
                )
                    return;
            }
            const id = one('.ad-editor').dataset.userId;
            const tab = one('[data-editor-tab].is-active').dataset.editorTab;
            const body = new FormData(form);
            pending = true;
            one('.ad-editor-body').inert = true;
            all('.ad-edit-form button[type="submit"]').forEach((button) => {
                button.disabled = true;
            });
            status.textContent = 'Сохраняю…';
            await post(`/admin/users/${id}/${form.dataset.endpoint}`, body);
            saved = true;
            baselines.set(form, formValue(form));
            pending = false;
            await refresh({ userId: id, editorTab: tab, drafts });
            const savedForm = one(`.ad-edit-form[data-endpoint="${form.dataset.endpoint}"] .ad-form-state`);
            if (savedForm) savedForm.textContent = 'Сохранено';
            toast('Изменения сохранены');
        } catch (error) {
            status.textContent = saved
                ? 'Сохранено. Обновите страницу, чтобы увидеть результат.'
                : error.message;
            status.classList.add('is-error');
        } finally {
            pending = false;
            if (one('.ad-editor-body')) one('.ad-editor-body').inert = false;
            all('.ad-edit-form').forEach((item) => {
                one('[type="submit"]', item).disabled = !dirty(item);
            });
        }
    }

    function generatePassword() {
        const alphabet = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%*-_';
        return Array.from(
            crypto.getRandomValues(new Uint32Array(16)),
            (value) => alphabet[value % alphabet.length],
        ).join('');
    }

    async function copyPassword(value, status) {
        if (!value) {
            status.textContent = 'Сначала введите или сгенерируйте пароль.';
            return;
        }
        try {
            await navigator.clipboard.writeText(value);
            status.textContent = 'Пароль скопирован';
        } catch (_) {
            status.textContent = 'Не удалось скопировать. Выделите пароль и скопируйте вручную.';
        }
    }

    async function userAction(button) {
        if (pending) return;
        const action = button.dataset.userAction;
        const id = one('.ad-editor').dataset.userId;
        const name = one('#ad-user-title').textContent;
        const blocked = one(`[data-user-row][data-user-id="${id}"]`).dataset.status === 'blocked';
        const password = action === 'reset-password' ? generatePassword() : '';
        const descriptions = {
            delete: [
                'Удалить сотрудника?',
                `${name}\nСотрудник потеряет доступ к системе. Учётная запись будет удалена, записи в журнале останутся.`,
                'Удалить',
            ],
            'toggle-active': [
                blocked ? 'Разблокировать сотрудника?' : 'Заблокировать сотрудника?',
                `${name}\n${blocked ? 'Сотрудник снова сможет войти в систему.' : 'Текущие сеансы завершатся. Войти снова можно будет после разблокировки.'}`,
                blocked ? 'Разблокировать' : 'Заблокировать',
            ],
            'reset-password': [
                'Сбросить пароль?',
                `${name}\nВсе сеансы сотрудника завершатся. После сброса передайте ему новый пароль.`,
                'Сбросить пароль',
            ],
            'toggle-stock-edit': [
                'Изменить права на сток?',
                `${name}\n${button.textContent.trim()}.`,
                'Подтвердить',
            ],
        };
        const drafts = otherDrafts(null);
        const [title, text, label] = descriptions[action];
        const data = new FormData();
        if (password) data.append('password', password);
        const confirmed = await confirmation({
            title,
            text,
            label,
            password,
            danger:
                action === 'delete' ||
                action === 'reset-password' ||
                (action === 'toggle-active' && !blocked),
            action: () => post(`/admin/users/${id}/${action}`, data),
        });
        if (!confirmed) return;
        if (password)
            await confirmation({
                title: 'Пароль изменён',
                text: 'Скопируйте пароль и передайте сотруднику. Позже его можно будет только заменить.',
                password,
                label: 'Готово',
                cancel: false,
            });
        try {
            await refresh({ userId: action === 'delete' ? null : id, editorTab: 'account', drafts });
            toast(action === 'delete' ? 'Сотрудник удалён' : 'Изменения сохранены');
        } catch (error) {
            await confirmation({
                title: 'Действие выполнено',
                text: error.message,
                label: 'Понятно',
                cancel: false,
            });
        }
    }

    function initializeCreate() {
        const form = one('#ad-create-form');
        updatePolicy(form);
        baselines.set(form, formValue(form));
        one('#u-status').textContent = '';
        one('#u-status').classList.remove('is-error');
        one('#u-pass-hint').textContent =
            'Скопируйте пароль и передайте сотруднику после создания учётной записи.';
        one('.ad-create-body').scrollTop = 0;
        all('[aria-invalid]', form).forEach((input) => input.removeAttribute('aria-invalid'));
    }

    async function createUser(form) {
        if (pending) return;
        const status = one('#u-status');
        all('[aria-invalid]', form).forEach((input) => input.removeAttribute('aria-invalid'));
        status.classList.remove('is-error');
        try {
            const labels = {
                full_name: 'ФИО',
                google_email: 'Электронная почта',
                login: 'Логин',
                password: 'Пароль',
            };
            for (const [name, label] of Object.entries(labels)) {
                const input = form.elements.namedItem(name);
                if (name !== 'password') input.value = input.value.trim();
                if (!input.value.trim())
                    throw Object.assign(new Error(`Заполните поле «${label}».`), { field: name });
            }
            if (!one('#u-email').checkValidity())
                throw Object.assign(new Error('Укажите корректный адрес электронной почты.'), {
                    field: 'google_email',
                });
            if (!/^[A-Za-z0-9_.@+\-]+$/.test(one('#u-login').value))
                throw Object.assign(new Error('В логине допустимы латиница, цифры и символы . _ @ + −.'), {
                    field: 'login',
                });
            if (one('#u-pass').value.length < 8)
                throw Object.assign(new Error('Пароль должен содержать не менее 8 символов.'), {
                    field: 'password',
                });
            validatePolicy(form);
            const password = one('#u-pass').value;
            pending = true;
            one('.ad-create-body').inert = true;
            one('#u-create').disabled = true;
            status.textContent = 'Создаю сотрудника…';
            await post('/admin/users', new FormData(form));
            baselines.set(form, formValue(form));
            pending = false;
            await confirmation({
                title: 'Сотрудник создан',
                text: 'Учётная запись готова. Скопируйте пароль и передайте сотруднику вместе с логином.',
                password,
                label: 'Готово',
                cancel: false,
            });
            form.reset();
            one('#u-create-overlay').close();
            resetFilters();
            state.sort = 'newest';
            await refresh();
            toast('Сотрудник добавлен в команду');
        } catch (error) {
            status.textContent = error.message || 'Не удалось связаться с сервером.';
            status.classList.add('is-error');
            const input = error.field && one(`[name="${error.field}"]`, form);
            if (input) {
                input.setAttribute('aria-invalid', 'true');
                input.focus();
            }
            if (!one('#u-create-overlay').open) toast(error.message, true);
        } finally {
            pending = false;
            one('.ad-create-body').inert = false;
            one('#u-create').disabled = false;
        }
    }

    async function requestAction(button) {
        if (pending) return;
        const actions = button.closest('.access-request-actions');
        const revoke = button.classList.contains('access-grant-revoke');
        const approve = button.classList.contains('access-request-approve');
        const data = new FormData();
        if (!revoke) data.append('approved', approve ? '1' : '0');
        const url = revoke
            ? `/admin/access-grants/${button.dataset.grantId}/revoke`
            : `/admin/access-requests/${actions.dataset.requestId}/decision`;
        const confirmed = await confirmation({
            title: revoke
                ? 'Отозвать разрешение?'
                : approve
                  ? 'Разрешить временный доступ?'
                  : 'Отклонить запрос?',
            text: revoke
                ? 'Сотрудник больше не сможет использовать это временное разрешение.'
                : approve
                  ? 'Сотрудник получит разрешение на выбранное действие на срок до 7 дней.'
                  : 'Сотрудник не получит временное разрешение на это действие.',
            label: revoke ? 'Отозвать' : approve ? 'Разрешить на 7 дней' : 'Отклонить',
            danger: !approve,
            action: () => post(url, data),
        });
        if (confirmed) {
            try {
                await refresh();
                toast('Решение сохранено');
            } catch (error) {
                toast(error.message, true);
            }
        }
    }

    function initialize() {
        syncFilters();
        selectTab(state.tab);
        filterUsers();
        for (const key of ['requests', 'log']) {
            one(`[data-table-search="${key}"]`).value = state[key];
            filterTable(key);
        }
        initializeCreate();
        all('dialog:not(#ad-confirm-dialog)').forEach((dialog) => {
            dialog.addEventListener('cancel', (event) => {
                event.preventDefault();
                requestClose(dialog);
            });
        });
        root.addEventListener('click', (event) => {
            const button = event.target.closest('button');
            if (!button || button.disabled || pending) return;
            if (button.dataset.tab) selectTab(button.dataset.tab);
            if (button.dataset.statFilter) {
                const filter = button.dataset.statFilter;
                selectTab(filter === 'requests' ? 'requests' : 'users');
                if (filter !== 'requests') {
                    resetFilters();
                    state.status = filter === 'all' ? '' : filter;
                    syncFilters();
                    filterUsers();
                }
            }
            if (button.id === 'ad-reset-filters' || button.hasAttribute('data-clear-search')) resetFilters();
            if (button.id === 'ad-prev' || button.id === 'ad-next') {
                state.page += button.id === 'ad-next' ? 1 : -1;
                filterUsers();
                one('.ad-workspace').scrollIntoView({ block: 'start' });
            }
            if (button.dataset.openUser) {
                opener = button;
                openUser(button.dataset.openUser);
            }
            if (button.dataset.editorTab) editorTab(button.dataset.editorTab);
            if (button.hasAttribute('data-close-dialog')) requestClose(button.closest('dialog'));
            if (button.id === 'u-open-create') {
                opener = button;
                one('#u-create-overlay').showModal();
                one('#u-fio').focus();
            }
            if (button.id === 'u-gen') {
                one('#u-pass').value = generatePassword();
                one('#u-pass-hint').textContent =
                    'Пароль сгенерирован. Нажмите «Копировать», чтобы сохранить его.';
            }
            if (button.id === 'u-copy') copyPassword(one('#u-pass').value, one('#u-pass-hint'));
            if (button.id === 'ad-confirm-copy')
                copyPassword(one('#ad-confirm-password input').value, button);
            if (button.dataset.userAction) userAction(button);
            if (button.matches('.access-request-approve, .access-request-reject, .access-grant-revoke'))
                requestAction(button);
            if (button.hasAttribute('data-select-stores')) {
                const form = button.closest('form');
                const inputs = all('[name="stores"]', form);
                const checked = !inputs.every((input) => input.checked);
                inputs.forEach((input) => {
                    if (!input.disabled) input.checked = checked;
                });
                button.textContent = checked ? 'Снять выбор' : 'Выбрать все';
                updatePolicy(form);
                updateDirty(form);
            }
        });
        root.addEventListener('input', (event) => {
            const target = event.target;
            if (target.id === 'ad-user-search') {
                state.query = target.value;
                state.page = 1;
                filterUsers();
            }
            if (target.dataset.tableSearch) {
                state[target.dataset.tableSearch] = target.value;
                filterTable(target.dataset.tableSearch);
            }
            if (target.form) {
                target.removeAttribute('aria-invalid');
                updateDirty(target.form);
            }
        });
        root.addEventListener('change', (event) => {
            const target = event.target;
            const filter = {
                'ad-filter-role': 'role',
                'ad-filter-status': 'status',
                'ad-filter-store': 'store',
                'ad-sort': 'sort',
            }[target.id];
            if (filter) {
                state[filter] = target.value;
                state.page = 1;
                filterUsers();
            }
            if (target.form) {
                updatePolicy(target.form);
                updateDirty(target.form);
            }
        });
        root.addEventListener('submit', (event) => {
            event.preventDefault();
            if (event.target.id === 'ad-create-form') createUser(event.target);
            else if (event.target.dataset.endpoint) saveEditor(event.target);
        });
        one('.ad-tabs').addEventListener('keydown', (event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            const buttons = all('[data-tab]:not([hidden])');
            const index = buttons.indexOf(document.activeElement);
            if (index < 0) return;
            const next =
                event.key === 'Home'
                    ? 0
                    : event.key === 'End'
                      ? buttons.length - 1
                      : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
            event.preventDefault();
            selectTab(buttons[next].dataset.tab);
            buttons[next].focus();
        });
    }
    window.addEventListener('beforeunload', (event) => {
        if (pending || dirtyForms().length) {
            event.preventDefault();
            event.returnValue = '';
        }
    });
    initialize();
})();
