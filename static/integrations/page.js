(() => {
    'use strict';
    const ui = window.CheckStockUI;
    const e = ui.escapeHtml;
    const markets = { 'YANDEX MARKET': ['yandex', 'Яндекс Маркет'], OZON: ['ozon', 'Ozon'], WB: ['wb', 'Wildberries'] };
    const weekdays = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
    const fields = [
        ['spreadsheet_url', 'spreadsheet_url', 'Ссылка на Google Таблицу площадки', 'url'],
        ['stock_sheet_name', 'sheet_name', 'Лист остатков', 'text'],
        ['orders_sheet_name', 'fbs_orders_sheet_name', 'Лист заказов FBS за 30 дней', 'text'],
        ['orders_quantity_column', 'fbs_orders_quantity_column', 'Столбец количества заказов FBS', 'column'],
        ['fbo_sheet_name', 'fbo_sheet_name', 'Лист FBO: в пути к клиенту и от клиента', 'text'],
        ['fbo_to_customer_column', 'fbo_to_customer_column', 'Столбец FBO: в пути к клиенту', 'column'],
        ['fbo_from_customer_column', 'fbo_from_customer_column', 'Столбец FBO: в пути от клиента', 'column']
    ];
    const option = (value, label, selected) => `<option value="${e(value)}"${value === selected ? ' selected' : ''}>${e(label)}</option>`;

    function exportForm(data, context) {
        const settings = data.settings;
        const saved = Boolean(settings.updated_at && settings.targets.some(target => target.spreadsheet_url));
        const sections = Object.entries(markets).map(([mp, [prefix, label]]) => {
            const target = settings.targets.find(item => item.marketplace === mp);
            const inputs = fields.map(([key, field, title, type]) => `<label class="export-url-field"><span>${title}</span><input class="input-control" type="${type === 'column' ? 'text' : type}" name="${prefix}_${field}" value="${e(target[key])}"${type === 'column' ? ' maxlength="3" pattern="[A-Za-z]{1,3}" required' : type === 'text' ? ' maxlength="100"' : ''}></label>`).join('');
            const buttons = [['stocks', 'sheet_name', 'Выгрузить остатки'], ['fbs_orders', 'fbs_orders_sheet_name', 'Выгрузить заказы'], ['fbo_transit', 'fbo_sheet_name', 'Выгрузить FBO в пути']].map(([kind, field, title]) => `<button class="btn-secondary" type="button" data-export-scope data-marketplace="${e(mp)}" data-url-field="${prefix}_spreadsheet_url" data-export-kind="${kind}" data-sheet-field="${prefix}_${field}" disabled>${title}</button>`).join('');
            return `<section class="export-marketplace"><div class="export-marketplace-head"><h3>${label}</h3></div>${inputs}<p class="panel-desc">Пустое название листа отключает выгрузку. Для остатков нужен отдельный лист. FBS и FBO одной площадки можно выгружать в один лист, указав разные столбцы.</p><div class="export-marketplace-actions">${buttons}</div></section>`;
        }).join('');
        const error = settings.last_error && (!settings.last_success_at || settings.last_attempt_at > settings.last_success_at) ? settings.last_error : '';
        const status = (settings.last_success_at ? 'Последняя успешная выгрузка: ' + ui.date(settings.last_success_at) + '. ' : saved ? 'Выгрузка ещё не запускалась. ' : 'Сохраните общие настройки перед первой выгрузкой. ') + (error ? 'Ошибка: ' + error : '');
        return `<form class="panel export-project-form" data-export-form data-saved="${saved}"><div class="export-card-head"><div class="export-store-title"><div><small>ВЫГРУЗКА ПО ПЛОЩАДКАМ</small><h2>Все проекты на каждой площадке</h2></div></div><label class="export-enabled"><input type="checkbox" name="enabled" value="1"${settings.enabled ? ' checked' : ''}><span>Автовыгрузка включена</span></label></div><div class="export-projects">${context.stores.map(store => `<span class="export-project-chip">${e(store.name)}</span>`).join('')}</div><div class="export-schedule-grid"><label><span>Периодичность</span><select class="integration-select" name="schedule_kind" data-schedule-kind>${option('daily', 'Каждый день', settings.schedule_kind)}${option('weekly', 'Раз в неделю', settings.schedule_kind)}</select></label><label data-weekday-field><span>День недели</span><select class="integration-select" name="weekday">${weekdays.map((label, index) => option(index, label, settings.weekday)).join('')}</select></label><label><span>Время (Москва)</span><input class="input-control" type="time" name="run_time" value="${e(settings.run_time)}" required></label></div><div class="integration-export-marketplaces">${sections}</div><p class="export-status ${error ? 'export-status--error' : 'export-status--ok'}" data-export-status role="status" aria-live="polite">${e(status)}</p><p class="integration-hint" data-export-save-hint>После изменений сохраните настройки.</p><div class="export-actions"><button class="btn-primary" type="submit">Сохранить настройки</button><button class="btn-secondary" type="button" data-export-now disabled>Выгрузить всё сейчас</button></div></form>`;
    }

    window.CheckStockPage.register(url => url.pathname === '/admin/integrations', async context => {
        const [data, templateResponse, cabinets, week] = await Promise.all([
            ui.request('/api/admin/google-export'), fetch('/ui/integrations/google-export', { credentials: 'same-origin' }),
            fetch('/admin/integrations/wb-yandex/cabinets', { credentials: 'same-origin' }),
            fetch('/admin/integrations/wb-yandex/week', { credentials: 'same-origin' })
        ]);
        for (const response of [templateResponse, cabinets, week]) if (!response.ok || response.redirected) throw new Error('Не удалось загрузить настройки');
        let ozonSettings = "";
        if (context.economics_preview) {
            const response = await fetch("/admin/integrations/ozon/cabinets", { credentials: "same-origin" });
            if (!response.ok || response.redirected) throw new Error("Не удалось загрузить настройки Ozon");
            ozonSettings = await response.text();
        }
        const values = { service_account_email: e(data.service_account_email), export_form: exportForm(data, context), week_update: await week.text() };
        const google = (await templateResponse.text()).replace(/\$([a-zA-Z_]\w*)/g, (_, key) => values[key]);
        return { title: 'CheckStock — Интеграции и выгрузки', section: 'admin_integrations', contentClass: 'content--integrations', values: { google_export: google, cabinet_settings: await cabinets.text(), ozon_cabinet_settings: ozonSettings } };
    });
})();
