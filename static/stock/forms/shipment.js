window.CheckStockOperations.initBlock('отгрузка', 'sh-status', function () {
    var btn = document.getElementById('sh-btn');
    var rowsBox = document.getElementById('sh-rows');
    if (!btn || !rowsBox) return;

    var storeSlug = document.getElementById('store-layout').dataset.store;
    var ffSelect = document.getElementById('sh-ff');
    var mpSelect = document.getElementById('sh-mp');
    var noteInput = document.getElementById('sh-note');
    var fboInput = document.getElementById('sh-fbo');
    var trashInput = document.getElementById('sh-trash');
    var legacyFbsInput = document.getElementById('sh-legacy-fbs');
    var legacyFbsOption = document.getElementById('sh-legacy-fbs-option');
    var fileInput = document.getElementById('sh-file');
    var urlInput = document.getElementById('sh-url');
    var status = document.getElementById('sh-status');
    var shipmentUrl = '/stock/' + storeSlug + '/shipment';

    var io = window.initIoBlock(document.getElementById('sh-io'));

    var rows = window.CheckStockOperations.requireRowEditor()({
        rowsBox: rowsBox,
        status: status,
        submitBtn: btn,
        storeSlug: storeSlug,
        getSource: function () {
            return { ff: ffSelect.value, mp: mpSelect.value };
        },
        isPendingRetry: function () {
            // The mutation helper still requires the original payload and saved request key.
            return window.CheckStockMutation.hasPending(shipmentUrl);
        },
        emptyHint: 'Сначала выберите, откуда отгружаем',
    });

    document.getElementById('sh-add-row').addEventListener('click', function () {
        rows.addRow().querySelector('.mv-code').focus();
    });
    ffSelect.addEventListener('change', rows.onSourceChange);
    mpSelect.addEventListener('change', rows.onSourceChange);

    function updateMode() {
        btn.textContent = trashInput.checked
            ? 'Списать в мусорку'
            : fboInput.checked
              ? 'Отгрузить на склады FBO'
              : legacyFbsInput.checked
                ? 'Повторить перемещение на FBS'
                : 'Отгрузить';
        rows.setAllowNegative(trashInput.checked);
    }

    [trashInput, fboInput, legacyFbsInput].forEach(function (input, _, inputs) {
        input.addEventListener('change', function () {
            if (input.checked) {
                inputs.forEach(function (other) {
                    if (other !== input) other.checked = false;
                });
            }
            updateMode();
        });
    });

    function refreshLegacyRetry() {
        var pending = window.CheckStockMutation.hasPending(shipmentUrl);
        legacyFbsOption.hidden = !pending;
        if (!pending) legacyFbsInput.checked = false;
        updateMode();
    }
    refreshLegacyRetry();

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
        // Keep legacy signatures, including FBS retries, exactly as they were before FBO.
        if (fboInput.checked) fd.append('to_fbo', '1');
        else fd.append('to_fbs', legacyFbsInput.checked ? '1' : '');
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
            : fboInput.checked
              ? 'Отправляю на склады FBO...'
              : legacyFbsInput.checked
                ? 'Повторяю перемещение на FBS...'
                : 'Отгружаю...';

        window.CheckStockMutation.fetch(shipmentUrl, {
            method: 'POST',
            body: fd,
            pendingOnly: legacyFbsInput.checked,
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
                    (fd.get('to_trash') === '1'
                        ? 'В мусорку: '
                        : res.data.transfer_id
                          ? 'Отправлено на склады FBO, партия №' + res.data.transfer_id + ': '
                          : fd.get('to_fbs') === '1'
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
                fboInput.checked = false;
                trashInput.checked = false;
                legacyFbsInput.checked = false;
                btn.textContent = 'Отгрузить';
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
                refreshLegacyRetry();
                rows.validateRows();
            });
    });
});
