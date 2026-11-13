(() => {
    const form = document.querySelector('[data-export-log-form]');
    if (!form) return;
    const day = form.elements.namedItem('day');
    window.CheckStockUI.request('/api/admin/integrations/export-log').then(calendar => {
        day.min = calendar.oldest;
        day.max = calendar.today;
        if (!day.value) day.value = calendar.today;
    }).catch(() => {});
})();
