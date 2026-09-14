window.CheckStockSuggestions = (() => {
    'use strict';

    function close(row) {
        const box = row.querySelector('.suggest-box');
        if (box) box.classList.remove('open');
    }

    function renderItem(item, subtitle = item.barcode) {
        return window.CheckStockUI.render('stock/suggestions/item', {
            article: item.article,
            barcode: subtitle,
            name: item.name,
        });
    }

    return { close, renderItem };
})();
