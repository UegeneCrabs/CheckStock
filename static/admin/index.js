(() => {
    'use strict';
    const root = document.getElementById('admin-page');
    if (!root) return;
    const message = document.getElementById('admin-message');
    const dialog = document.getElementById('password-dialog');
    let passwordUserId = null;
    async function submit(url, body, button) {
        if (button) button.disabled = true;
        message.textContent = '';
        try {
            const response = await fetch(url, { method: 'POST', body, headers: { 'X-Requested-With': 'fetch' } });
            if (response.status === 401) { window.location.assign('/login'); return; }
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить изменения');
            window.location.reload();
        } catch (error) {
            message.textContent = error.message;
            if (dialog.open) dialog.close();
        } finally {
            if (button) button.disabled = false;
        }
    }
    document.getElementById('create-user-form').addEventListener('submit', event => {
        event.preventDefault();
        submit('/admin/users', new FormData(event.target), event.submitter);
    });
    document.getElementById('user-search').addEventListener('input', event => {
        const query = event.target.value.trim().toLocaleLowerCase();
        root.querySelectorAll('[data-user-id]').forEach(row => {
            row.hidden = !Array.from(row.cells).slice(0, 3).some(cell => cell.textContent.toLocaleLowerCase().includes(query));
        });
    });
    root.addEventListener('click', event => {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        const id = button.closest('[data-user-id]').dataset.userId;
        if (button.dataset.action === 'reset-password') {
            passwordUserId = id;
            document.getElementById('password-form').reset();
            dialog.showModal();
            return;
        }
        if (button.dataset.action === 'delete' && !window.confirm('Удалить сотрудника?')) return;
        submit(`/admin/users/${id}/${button.dataset.action}`, undefined, button);
    });
    document.getElementById('password-cancel').addEventListener('click', () => dialog.close());
    document.getElementById('password-form').addEventListener('submit', event => {
        event.preventDefault();
        submit(`/admin/users/${passwordUserId}/reset-password`, new FormData(event.target), event.submitter);
    });
})();
