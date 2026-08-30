(function () {
    var groups = Array.prototype.slice.call(document.querySelectorAll('[data-nav-group]'));
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
        setOpen(group, false);
        button.addEventListener('click', function (event) {
            event.preventDefault();
            var opening = !group.classList.contains('is-open');
            if (opening) {
                groups.forEach(function (other) {
                    if (other !== group) setOpen(other, false);
                });
            }
            setOpen(group, opening);
        });
    });

    var mobileNavigation = window.matchMedia('(max-width: 800px)');

    function syncNavigationBreakpoint(event) {
        if (!event.matches) return;
        groups.forEach(function (group) { setOpen(group, false); });
    }

    if (mobileNavigation.addEventListener) {
        mobileNavigation.addEventListener('change', syncNavigationBreakpoint);
    } else if (mobileNavigation.addListener) {
        mobileNavigation.addListener(syncNavigationBreakpoint);
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
        groups.forEach(function (group) {
            if (!group.contains(event.target)) setOpen(group, false);
        });
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            setNotificationsOpen(false);
            groups.forEach(function (group) { setOpen(group, false); });
        }
    });
})();
