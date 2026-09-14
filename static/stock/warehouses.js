(function () {
    var slug = document.querySelector('[data-warehouses]').dataset.store;
    var marketplace = document.querySelector('[data-warehouses]').dataset.marketplace;

    document.querySelectorAll('.trash-checkbox').forEach(function (box) {
        box.addEventListener('change', function () {
            var row = box.closest('tr');
            if (row) row.classList.toggle('is-checked', box.checked);

            var fd = new FormData();
            fd.append('marketplace', marketplace);
            fd.append('article', box.getAttribute('data-article'));
            fd.append('fulfillment', box.getAttribute('data-warehouse'));
            fd.append('checked', box.checked ? '1' : '');

            fetch('/stock/' + slug + '/trash/checked', { method: 'POST', body: fd })
                .then(function (r) {
                    return r.json();
                })
                .then(function (d) {
                    if (d && d.ok) return;

                    box.checked = !box.checked;
                    if (row) row.classList.toggle('is-checked', box.checked);
                    if (d && d.error) alert(d.error);
                })
                .catch(function () {
                    box.checked = !box.checked;
                    if (row) row.classList.toggle('is-checked', box.checked);
                });
        });
    });
})();

(function () {
    var tabs = document.querySelectorAll('.wh-tab');
    if (!tabs.length) return;

    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            var targetId = tab.getAttribute('data-target');

            tabs.forEach(function (t) {
                t.classList.remove('active');
            });
            tab.classList.add('active');

            document.querySelectorAll('.wh-pane').forEach(function (pane) {
                pane.classList.toggle('is-hidden', pane.id !== targetId);
            });
        });
    });
})();
