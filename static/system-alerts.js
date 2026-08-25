(function () {
    'use strict';

    Array.prototype.forEach.call(document.querySelectorAll('[data-system-alerts]'), function (root) {
        var items = Array.prototype.slice.call(root.querySelectorAll('[data-system-alert-item]'));
        var dots = Array.prototype.slice.call(root.querySelectorAll('.system-alert-dot'));
        if (items.length < 2) return;
        var current = 0;
        window.setInterval(function () {
            items[current].hidden = true;
            if (dots[current]) dots[current].classList.remove('is-active');
            current = (current + 1) % items.length;
            items[current].hidden = false;
            if (dots[current]) dots[current].classList.add('is-active');
        }, 15000);
    });
})();
