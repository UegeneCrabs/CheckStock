
(function () {
    'use strict';

    var STORAGE_PREFIX = 'paketa.collapse.';

    var CHEVRON =
        '<svg class="panel-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" ' +
        'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" ' +
        'aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg>';

    function readState(id) {
        if (!id) return null;
        try {
            return window.localStorage.getItem(STORAGE_PREFIX + id);
        } catch (e) {
            return null;
        }
    }

    function writeState(id, collapsed) {
        if (!id) return;
        try {
            window.localStorage.setItem(STORAGE_PREFIX + id, collapsed ? '1' : '0');
        } catch (e) {

        }
    }

    function initPanel(panel) {
        var title = panel.querySelector('.panel-title');
        var body = panel.querySelector('.panel-body');
        if (!title || !body) return;

        var id = panel.getAttribute('data-collapse-id');


        var toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'panel-toggle';
        title.parentNode.insertBefore(toggle, title);
        toggle.appendChild(title);
        toggle.insertAdjacentHTML('beforeend', CHEVRON);

        function apply(collapsed, save) {
            panel.classList.toggle('is-collapsed', collapsed);
            toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            if (save) writeState(id, collapsed);
        }

        var saved = readState(id);
        var startCollapsed = saved !== null
            ? saved === '1'
            : panel.hasAttribute('data-collapsed');

        apply(startCollapsed, false);

        toggle.addEventListener('click', function () {
            apply(!panel.classList.contains('is-collapsed'), true);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.panel--collapsible').forEach(initPanel);
    });
})();
