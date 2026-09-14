window.CheckStockOperations.initBlock('допуск', 'ff-upload-status', function () {
    var flag = document.getElementById('access-problems');
    if (!flag || flag.getAttribute('data-can-edit') === '1') return;

    var blocks = [
        { btn: 'ff-upload-btn', status: 'ff-upload-status' },
        { btn: 'mv-btn', status: 'mv-status' },
        { btn: 'sh-btn', status: 'sh-status' },
    ];

    blocks.forEach(function (item) {
        var btn = document.getElementById(item.btn);
        var status = document.getElementById(item.status);
        if (btn) {
            btn.disabled = true;
            btn.title = 'Изменение остатков для вашей учётной записи закрыто';
        }
        if (status) {
            status.textContent =
                'Изменение остатков для вашей учётной записи ' +
                'пока закрыто — обратитесь к администратору.';
            status.classList.add('ff-select-status--bad');
        }
    });

    document.addEventListener(
        'input',
        function () {
            blocks.forEach(function (item) {
                var btn = document.getElementById(item.btn);
                if (btn) btn.disabled = true;
            });
        },
        true,
    );
});
window.CheckStockOperations.initBlock('доступы', 'ff-upload-status', function () {
    var box = document.getElementById('access-problems');
    if (!box) return;

    var VISIT_KEY = 'paketa.access.' + document.getElementById('store-layout').dataset.store;
    try {
        if (window.sessionStorage.getItem(VISIT_KEY)) return;
    } catch (e) {}

    var problems = [];
    try {
        problems = JSON.parse(box.getAttribute('data-problems') || '[]');
    } catch (e) {
        return;
    }
    if (!problems.length) return;

    var esc = window.CheckStockUI.escapeHtml;

    var bodyHtml = problems
        .map(function (p) {
            return window.CheckStockUI.render('stock/store-access/body-html-4', {
                kind: p.kind,
                title: p.title,
                status: p.status,
                detail: p.detail,
            });
        })
        .join('');

    function show() {
        if (!window.Modal) {
            console.error('CheckStock: modal.js не загрузился, показать окно нечем');
            return;
        }
        try {
            window.sessionStorage.setItem(VISIT_KEY, '1');
        } catch (e) {}

        Modal.alert({
            title: 'Доступ к маркетплейсам',
            bodyHtml: bodyHtml,
            confirmLabel: 'Понятно',
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', show);
    } else {
        show();
    }
});
