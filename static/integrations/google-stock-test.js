(() => {
    'use strict';
    const panel = document.querySelector('[data-stock-test-enabled="true"]');
    if (!panel) return;
    const ui = window.CheckStockUI;
    const api = '/api/admin/integrations/google-stock-test';
    const form = panel.querySelector('[data-stock-test-form]');
    const fields = form.elements;
    const run = panel.querySelector('[data-stock-test-run]');
    const save = form.querySelector('[type="submit"]');
    const feedback = panel.querySelector('[data-stock-test-feedback]');
    const status = panel.querySelector('[data-stock-test-status]');
    let working = false;
    let running = false;
    let refreshing = false;

    function controls() {
        save.disabled = working || running;
        run.disabled = working || running;
    }

    function syncWeekday() {
        const weekly = fields.schedule_kind.value === 'weekly';
        panel.querySelector('[data-stock-test-weekday]').hidden = !weekly;
        fields.weekday.disabled = !weekly;
    }

    function render(data, loadFields = false) {
        if (loadFields) {
            const link = panel.querySelector('[data-stock-test-link]');
            link.href = data.spreadsheet_url;
            panel.querySelector('[data-stock-test-sheets]').textContent = ' · ' + data.sheets.join(', ');
            if (data.service_account_email && data.service_account_email !== '?') {
                const account = panel.querySelector('[data-stock-test-account]');
                account.querySelector('code').textContent = data.service_account_email;
                account.hidden = false;
            }
            fields.enabled.checked = data.schedule.enabled;
            fields.schedule_kind.value = data.schedule.schedule_kind;
            fields.weekday.value = String(data.schedule.weekday);
            fields.run_time.value = data.schedule.run_time;
            syncWeekday();
            form.hidden = false;
        }
        panel.querySelector('[data-stock-test-next]').textContent = data.next_run_at
            ? 'Следующая выгрузка — ' + ui.date(data.next_run_at) + ' МСК'
            : 'Автоматическая выгрузка выключена';
        const state = data.state;
        running = state.running;
        status.textContent = running ? 'Выгружаем остатки и заказы…'
            : state.last_success_at ? 'Последняя успешная выгрузка · ' + ui.date(state.last_success_at) + ' МСК'
            : 'Выгруженных данных пока нет';
        controls();
    }

    async function refresh() {
        if (working || refreshing) return;
        refreshing = true;
        try {
            const data = await ui.request(api);
            if (!working) render(data, form.hidden);
        } catch (_) {
            feedback.textContent = '';
        } finally {
            refreshing = false;
        }
    }

    fields.schedule_kind.addEventListener('change', syncWeekday);
    form.addEventListener('submit', async event => {
        event.preventDefault();
        working = true;
        controls();
        feedback.textContent = '';
        try {
            render(await ui.request(api, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    enabled: fields.enabled.checked,
                    schedule_kind: fields.schedule_kind.value,
                    weekday: Number(fields.weekday.value),
                    run_time: fields.run_time.value,
                }),
            }));
            feedback.textContent = 'Расписание сохранено.';
        } catch (_) {
            feedback.textContent = '';
        } finally {
            working = false;
            controls();
        }
    });
    run.addEventListener('click', async () => {
        working = true;
        controls();
        feedback.textContent = '';
        try {
            await ui.request(api + '/run', { method: 'POST' });
            running = true;
            status.textContent = 'Выгружаем остатки и заказы…';
        } catch (_) {
            feedback.textContent = '';
        } finally {
            working = false;
            controls();
        }
        await refresh();
    });
    panel.hidden = false;
    feedback.textContent = 'Загружаем расписание…';
    refresh().then(() => { if (!form.hidden) feedback.textContent = ''; });
    window.setInterval(() => {
        if (panel.getClientRects().length && !document.hidden) refresh();
    }, 5000);
})();
