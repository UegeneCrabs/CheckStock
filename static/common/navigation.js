(function () {
    var groups = Array.prototype.slice.call(document.querySelectorAll('[data-nav-group]'));

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
        groups.forEach(function (group) {
            setOpen(group, false);
        });
    }

    if (mobileNavigation.addEventListener) {
        mobileNavigation.addEventListener('change', syncNavigationBreakpoint);
    } else if (mobileNavigation.addListener) {
        mobileNavigation.addListener(syncNavigationBreakpoint);
    }

    document.addEventListener('click', function (event) {
        groups.forEach(function (group) {
            if (!group.contains(event.target)) setOpen(group, false);
        });
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            groups.forEach(function (group) {
                setOpen(group, false);
            });
        }
    });
})();
