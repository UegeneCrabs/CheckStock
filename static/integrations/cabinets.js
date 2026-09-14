(function () {
    'use strict';
    var root = document.querySelector('[data-cabinet-workspace]');
    if (!root) return;
    var buttons = Array.from(root.querySelectorAll('[data-cabinet-market]'));
    var panels = Array.from(root.querySelectorAll('[data-cabinet-panel]'));

    function showFromUrl() {
        var url = new URL(window.location.href);
        var selected = url.searchParams.get('cabinet_market');
        if (url.hash.indexOf('#yandex-') === 0 || (!selected && (url.searchParams.has('ym_store') || url.searchParams.has('ym_article'))))
            selected = 'ym';
        if (selected !== 'ym') selected = 'wb';
        panels.forEach(function (panel) {
            panel.hidden = panel.dataset.cabinetPanel !== selected;
        });
        buttons.forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.cabinetMarket === selected));
        });
    }
    buttons.forEach(function (button) {
        button.addEventListener('click', function () {
            var url = new URL(window.location.href);
            url.searchParams.set('tab', 'cabinets');
            url.searchParams.set('cabinet_market', button.dataset.cabinetMarket);
            url.hash = '';
            window.history.pushState(null, '', url);
            showFromUrl();
        });
    });
    window.addEventListener('popstate', showFromUrl);
    window.addEventListener('hashchange', showFromUrl);
    showFromUrl();
})();
