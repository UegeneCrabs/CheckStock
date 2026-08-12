(function () {
    var groups = Array.prototype.slice.call(document.querySelectorAll('[data-nav-group]'));
    if (!groups.length) return;

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
            setOpen(group, !group.classList.contains('is-open'));
        });
    });

    document.addEventListener('click', function (event) {
        if (window.innerWidth > 800) return;
        groups.forEach(function (group) {
            if (!group.contains(event.target)) setOpen(group, false);
        });
    });
})();
