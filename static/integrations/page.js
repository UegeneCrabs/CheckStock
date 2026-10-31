(() => {
    'use strict';
    const ui = window.CheckStockUI;
    const e = ui.escapeHtml;
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
        const values = { service_account_email: e(data.service_account_email), week_update: await week.text() };
        const google = (await templateResponse.text()).replace(/\$([a-zA-Z_]\w*)/g, (_, key) => values[key]);
        return { title: 'CheckStock — Интеграции и выгрузки', section: 'admin_integrations', contentClass: 'content--integrations', values: { google_export: google, cabinet_settings: await cabinets.text(), ozon_cabinet_settings: ozonSettings } };
    });
})();
