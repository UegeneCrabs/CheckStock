(function () {
    var groups = Array.prototype.slice.call(document.querySelectorAll('[data-nav-group]'));
    var hoverNavigation = window.matchMedia('(min-width: 560px) and (hover: hover) and (pointer: fine)');

    function setBranch(branch, open) {
        var button = branch.querySelector('[data-nav-branch-toggle]');
        button.setAttribute('aria-expanded', String(open));
        document.getElementById(button.getAttribute('aria-controls')).hidden = !open;
    }

    function setOpen(group, open) {
        group.classList.toggle('is-open', open);
        var button = group.querySelector('[data-nav-toggle]');
        if (button) button.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (!open) group.querySelectorAll('[data-nav-branch]').forEach(function (branch) { setBranch(branch, false); });
    }

    function setupBranches(group) {
        var branches = group.querySelectorAll('[data-nav-branch]');
        branches.forEach(function (branch) {
            var trigger = branch.querySelector('[data-nav-branch-toggle]');
            var links = document.getElementById(trigger.getAttribute('aria-controls'));
            links.querySelectorAll('a').forEach(function (link) {
                if (location.pathname !== link.pathname && !location.pathname.startsWith(link.pathname + '/')) return;
                link.classList.add('active');
                link.setAttribute('aria-current', location.pathname === link.pathname ? 'page' : 'location');
                trigger.classList.add('active');
            });

            function openBranch() {
                branches.forEach(function (other) { setBranch(other, other === branch); });
            }

            branch.addEventListener('pointerenter', function (event) {
                if (hoverNavigation.matches && event.pointerType === 'mouse') openBranch();
            });
            branch.addEventListener('pointerleave', function (event) {
                if (hoverNavigation.matches && event.pointerType === 'mouse' &&
                    !links.contains(document.activeElement)) {
                    setBranch(branch, false);
                }
            });
            trigger.addEventListener('click', function (event) {
                var hoverClick = hoverNavigation.matches && event.pointerType === 'mouse' && branch.matches(':hover');
                if (trigger.getAttribute('aria-expanded') === 'true' && !hoverClick) setBranch(branch, false);
                else openBranch();
            });
        });
    }

    groups.forEach(function (group) {
        var button = group.querySelector('[data-nav-toggle]');
        if (!button) return;
        setOpen(group, false);
        setupBranches(group);
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
            var branch = event.target instanceof Element && event.target.closest('[data-nav-branch]');
            var trigger = branch && branch.querySelector('[data-nav-branch-toggle]');
            if (trigger && trigger.getAttribute('aria-expanded') === 'true') {
                setBranch(branch, false);
                trigger.focus();
                return;
            }
            groups.forEach(function (group) {
                if (group.classList.contains('is-open') && group.contains(event.target)) {
                    group.querySelector('[data-nav-toggle]').focus();
                }
                setOpen(group, false);
            });
        }
    });
})();
