window.CheckStockOperations.initBlock('отгрузка', 'sh-status', function () {
    var btn = document.getElementById('sh-btn');
    var rowsBox = document.getElementById('sh-rows');
    if (!btn || !rowsBox) return;

    var storeSlug = document.getElementById('store-layout').dataset.store;
    var ffSelect = document.getElementById('sh-ff');
    var mpSelect = document.getElementById('sh-mp');
    var noteInput = document.getElementById('sh-note');
    var fbsInput = document.getElementById('sh-fbs');
    var trashInput = document.getElementById('sh-trash');
    var fileInput = document.getElementById('sh-file');
    var urlInput = document.getElementById('sh-url');
    var status = document.getElementById('sh-status');

    var io = window.initIoBlock(document.getElementById('sh-io'));

    var rows = window.CheckStockOperations.requireRowEditor()({
        rowsBox: rowsBox,
        status: status,
        submitBtn: btn,
        storeSlug: storeSlug,
        getSource: function () {
            return { ff: ffSelect.value, mp: mpSelect.value };
        },
        emptyHint: 'Сначала выберите, откуда отгружаем',
    });

    document.getElementById('sh-add-row').addEventListener('click', function () {
        rows.addRow().querySelector('.mv-code').focus();
    });
    ffSelect.addEventListener('change', rows.onSourceChange);
    mpSelect.addEventListener('change', rows.onSourceChange);

    trashInput.addEventListener('change', function () {
        if (trashInput.checked) fbsInput.checked = false;
        btn.textContent = trashInput.checked
            ? 'Списать в мусорку'
            : fbsInput.checked
              ? 'Переместить на FBS'
              : 'Отгрузить';

        rows.setAllowNegative(trashInput.checked);
    });

    fbsInput.addEventListener('change', function () {
        if (fbsInput.checked) trashInput.checked = false;
        btn.textContent = fbsInput.checked ? 'Переместить на FBS' : 'Отгрузить';
        rows.setAllowNegative(false);
    });

    btn.addEventListener('click', function () {
        if (!ffSelect.value || !mpSelect.value) {
            status.textContent = 'Выберите фулфилмент и маркетплейс, с которых уходит товар';
            status.classList.add('ff-select-status--bad');
            return;
        }
        if (!noteInput.value.trim()) {
            status.textContent = 'Укажите обязательное примечание к операции';
            status.classList.add('ff-select-status--bad');
            noteInput.focus();
            return;
        }

        var fd = new FormData();
        fd.append('fulfillment', ffSelect.value);
        fd.append('marketplace', mpSelect.value);
        fd.append('note', noteInput.value.trim());
        fd.append('to_fbs', fbsInput.checked ? '1' : '');
        fd.append('to_trash', trashInput.checked ? '1' : '');

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
        status.textContent = trashInput.checked
            ? 'Списываю в мусорку...'
            : fbsInput.checked
              ? 'Фиксирую перемещение на FBS...'
              : 'Отгружаю...';

        window.CheckStockMutation.fetch('/stock/' + storeSlug + '/shipment', {
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
                    rows.showServerMessage('Ошибка: ' + (res.data.error || 'не удалось отгрузить'), true);
                    return;
                }
                var list = res.data.results || [];
                rows.showServerMessage(
                    (trashInput.checked
                        ? 'В мусорку: '
                        : fbsInput.checked
                          ? 'Перемещено на FBS: '
                          : 'Отгружено: ') +
                        list
                            .map(function (r) {
                                return r.article + ' x' + r.quantity;
                            })
                            .join(', '),
                    false,
                );

                rows.reset();
                urlInput.value = '';
                noteInput.value = '';
                fbsInput.checked = false;
                trashInput.checked = false;
                btn.textContent = 'Отгрузить';
                fileInput.value = '';
                var drop = fileInput.closest('.file-drop');
                var lbl = drop && drop.querySelector('.file-drop-text');
                if (lbl) lbl.textContent = 'Выбрать файл .xlsx';
                rows.loadSourceStock();
                if (window.stockTable) window.stockTable.refresh();
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
