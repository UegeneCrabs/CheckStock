window.CheckStockOperations.initBlock('допуск', 'ff-upload-status', function () {
    var flag = document.getElementById('stock-edit-access');
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
