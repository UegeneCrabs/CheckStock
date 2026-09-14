window.CheckStockOperations.initBlock('перемещение', 'mv-status', function () {
    var btn = document.getElementById('mv-btn');
    var rowsBox = document.getElementById('mv-rows');
    if (!btn || !rowsBox) return;

    var storeSlug = document.getElementById('store-layout').dataset.store;
    var fromFf = document.getElementById('mv-from-ff');
    var fromMp = document.getElementById('mv-from-mp');
    var toFf = document.getElementById('mv-to-ff');
    var toMp = document.getElementById('mv-to-mp');
    var noteInput = document.getElementById('mv-note');
    var fileInput = document.getElementById('mv-file');
    var urlInput = document.getElementById('mv-url');
    var status = document.getElementById('mv-status');

    var io = window.initIoBlock(document.getElementById('mv-io'));

    var rows = window.CheckStockOperations.requireRowEditor()({
        rowsBox: rowsBox,
        status: status,
        submitBtn: btn,
        storeSlug: storeSlug,
        getSource: function () {
            return { ff: fromFf.value, mp: fromMp.value };
        },
        emptyHint: 'Сначала выберите, откуда перемещаем',
    });

    document.getElementById('mv-add-row').addEventListener('click', function () {
        rows.addRow().querySelector('.mv-code').focus();
    });
    fromFf.addEventListener('change', rows.onSourceChange);
    fromMp.addEventListener('change', rows.onSourceChange);

    btn.addEventListener('click', function () {
        if (!noteInput.value.trim()) {
            status.textContent = 'Укажите обязательное примечание к перемещению';
            status.classList.add('ff-select-status--bad');
            noteInput.focus();
            return;
        }
        var fd = new FormData();
        fd.append('from_fulfillment', fromFf.value);
        fd.append('from_marketplace', fromMp.value);
        fd.append('to_fulfillment', toFf.value);
        fd.append('to_marketplace', toMp.value);
        fd.append('note', noteInput.value.trim());

        var method = io.current();
        var hasFile = fileInput.files && fileInput.files.length > 0;
        var url = urlInput.value.trim();

        if (method === 'file') {
            if (!hasFile) {
                status.textContent = 'Прикрепите файл .xlsx';
                status.classList.add('ff-select-status--bad');
                return;
            }
            fd.append('file', fileInput.files[0]);
        } else if (method === 'sheet') {
            if (!url) {
                status.textContent = 'Вставьте ссылку на Google Таблицу';
                status.classList.add('ff-select-status--bad');
                return;
            }
            fd.append('sheet_url', url);
        } else {
            if (rows.validateRows().length) return;

            var items = rows.collectItems();
            if (!items.length) {
                status.textContent = 'Заполните хотя бы одну позицию или приложите файл';
                return;
            }
            fd.append('items', JSON.stringify(items));
        }

        btn.disabled = true;
        status.textContent = 'Перемещаю...';

        fetch('/stock/' + storeSlug + '/transfer', {
            method: 'POST',
            body: fd,
            headers: { 'X-Requested-With': 'fetch' },
        })
            .then(function (r) {
                return r.json().then(function (d) {
                    return { ok: r.ok, data: d };
                });
            })
            .then(function (res) {
                if (!res.ok || !res.data.ok) {
                    rows.showServerMessage('Ошибка: ' + (res.data.error || 'не удалось переместить'), true);
                    return;
                }
                var list = res.data.results || [];
                var skipped = res.data.skipped || [];
                var message =
                    'Отправлено в путь, партия №' +
                    res.data.transfer_id +
                    ': ' +
                    list
                        .map(function (r) {
                            return r.article + ' x' + r.quantity;
                        })
                        .join(', ');
                if (skipped.length) {
                    message +=
                        '. Не переведено: ' +
                        skipped
                            .map(function (r) {
                                return r.article + ' x' + r.quantity + ' — ' + r.reason;
                            })
                            .join('; ');
                }
                rows.showServerMessage(message, false);

                rows.reset();
                urlInput.value = '';
                noteInput.value = '';

                fileInput.value = '';
                var drop = fileInput.closest('.file-drop');
                var lbl = drop && drop.querySelector('.file-drop-text');
                if (lbl) lbl.textContent = 'Выбрать файл .xlsx';
                rows.loadSourceStock();
                if (window.stockTable) window.stockTable.refresh();
                if (window.stockTransit) window.stockTransit.refresh();
            })
            .catch(function (e) {
                rows.showServerMessage('Ошибка: ' + e, true);
            })
            .finally(function () {
                btn.disabled = false;
                rows.validateRows();
            });
    });
});
