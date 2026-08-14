(function () {
    var groups = Array.prototype.slice.call(document.querySelectorAll('[data-nav-group]'));
    var collapse = document.getElementById('sidebar-collapse');
    var theme = document.getElementById('theme-toggle');
    var notificationsTrigger = document.getElementById('notifications-trigger');
    var notificationsPanel = document.getElementById('notifications-panel');

    function setOpen(group, open) {
        group.classList.toggle('is-open', open);
        var button = group.querySelector('[data-nav-toggle]');
        if (button) button.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    groups.forEach(function (group) {
        var button = group.querySelector('[data-nav-toggle]');
        if (!button) return;
        if (window.innerWidth <= 800) setOpen(group, false);
        button.addEventListener('click', function () {
            if (document.documentElement.classList.contains('sidebar-collapsed')) {
                setSidebarCollapsed(false);
                return;
            }
            var opening = !group.classList.contains('is-open');
            if (window.innerWidth <= 800 && opening) {
                groups.forEach(function (other) {
                    if (other !== group) setOpen(other, false);
                });
            }
            setOpen(group, opening);
        });
    });

    function setSidebarCollapsed(collapsed) {
        document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
        if (collapse) {
            collapse.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
            collapse.setAttribute('aria-label', collapsed ? 'Развернуть боковое меню' : 'Свернуть боковое меню');
        }
        try { localStorage.setItem('checkstock-sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (e) {}
    }

    function setTheme(dark) {
        if (dark) document.documentElement.setAttribute('data-theme', 'dark');
        else document.documentElement.removeAttribute('data-theme');
        if (theme) {
            theme.setAttribute('aria-pressed', dark ? 'true' : 'false');
            var label = theme.querySelector('.theme-toggle-label');
            if (label) label.textContent = dark ? 'Тёмная тема' : 'Светлая тема';
        }
        var themeColor = document.querySelector('meta[name="theme-color"]');
        if (themeColor) themeColor.setAttribute('content', dark ? '#111827' : '#ffffff');
        try { localStorage.setItem('checkstock-theme', dark ? 'dark' : 'light'); } catch (e) {}
    }

    function setNotificationsOpen(open) {
        if (!notificationsTrigger || !notificationsPanel) return;
        notificationsPanel.hidden = !open;
        notificationsTrigger.setAttribute('aria-expanded', open ? 'true' : 'false');
        document.body.classList.toggle('notifications-open', open);
    }

    if (collapse) {
        setSidebarCollapsed(document.documentElement.classList.contains('sidebar-collapsed'));
        collapse.addEventListener('click', function () {
            setSidebarCollapsed(!document.documentElement.classList.contains('sidebar-collapsed'));
        });
    }
    if (theme) {
        setTheme(document.documentElement.getAttribute('data-theme') === 'dark');
        theme.addEventListener('click', function () {
            setTheme(document.documentElement.getAttribute('data-theme') !== 'dark');
        });
    }
    if (notificationsTrigger && notificationsPanel) {
        notificationsTrigger.addEventListener('click', function () {
            setNotificationsOpen(notificationsPanel.hidden);
        });
        var notificationsClose = notificationsPanel.querySelector('.notifications-close');
        if (notificationsClose) {
            notificationsClose.addEventListener('click', function () { setNotificationsOpen(false); });
        }
    }

    document.addEventListener('click', function (event) {
        if (notificationsPanel && !notificationsPanel.hidden &&
                !notificationsPanel.contains(event.target) &&
                !notificationsTrigger.contains(event.target)) {
            setNotificationsOpen(false);
        }
        if (window.innerWidth > 800) return;
        groups.forEach(function (group) {
            if (!group.contains(event.target)) setOpen(group, false);
        });
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') setNotificationsOpen(false);
    });
})();
