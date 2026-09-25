(function () {
    var select = document.getElementById('ff-upload-select');
    var noteInput = document.getElementById('ff-upload-note');
    var fileInput = document.getElementById('ff-upload-file');
    var urlInput = document.getElementById('ff-upload-url');
    var btn = document.getElementById('ff-upload-btn');
    var status = document.getElementById('ff-upload-status');
    if (!btn) return;

    var storeSlug = document.getElementById('store-layout').dataset.store;

    var submitManual = null;
    var io = window.initIoBlock(document.getElementById('ff-io'));

    function refreshFfAvailable() {
        if (window.stockTable) window.stockTable.refresh();
    }

    function hasManualInput() {
        var rowsBox = document.getElementById('ff-manual-rows');
        if (!rowsBox) return false;
        return Array.prototype.some.call(rowsBox.children, function (row) {
            var code = row.querySelector('.manual-code');
            var qty = row.querySelector('.manual-qty');
            return (code && code.value.trim()) || (qty && qty.value.trim());
        });
    }

    btn.addEventListener('click', function () {
        var fulfillment = select.value;
        if (!fulfillment) {
            status.textContent = 'Сначала выберите фулфилмент назначения';
            status.classList.add('ff-select-status--bad');
            return;
        }
        if (!noteInput.value.trim()) {
            status.textContent = 'Укажите обязательное примечание к поставке';
            status.classList.add('ff-select-status--bad');
            noteInput.focus();
            return;
        }

        var method = io.current();
        var hasFile = fileInput.files && fileInput.files.length > 0;
        var url = urlInput.value.trim();

        status.classList.remove('ff-select-status--bad');

        if (method === 'manual') {
            if (!hasManualInput()) {
                status.textContent = 'Заполните хотя бы одну позицию';
                status.classList.add('ff-select-status--bad');
                return;
            }
            if (submitManual) submitManual();
            return;
        }

        if (method === 'sheet' && !url) {
            status.textContent = 'Вставьте ссылку на Google Таблицу';
            status.classList.add('ff-select-status--bad');
            return;
        }
        if (method === 'file' && !hasFile) {
            status.textContent = 'Прикрепите файл .xlsx';
            status.classList.add('ff-select-status--bad');
            return;
        }

        var marketplace = window.stockTable.currentMp();

        function buildFormData(previewMode, confirmationToken) {
            var formData = new FormData();
            formData.append('fulfillment', fulfillment);
            formData.append('marketplace', marketplace);
            formData.append('note', noteInput.value.trim());
            if (previewMode) formData.append('preview', '1');
            if (confirmationToken) formData.append('confirmation_token', confirmationToken);
            if (method === 'file') {
                formData.append('file', fileInput.files[0]);
            } else {
                formData.append('sheet_url', url);
            }
            return formData;
        }

        function postImport(formData) {
            return window.CheckStockMutation.fetch('/stock/' + storeSlug + '/upload-ff-stock', {
                method: 'POST',
                body: formData,
            }).then(function (r) {
                return r.json().then(function (data) {
                    return { ok: r.ok, data: data };
                });
            });
        }

        btn.disabled = true;
        status.textContent = 'Проверяю количество...';

        var submission;
        if (window.CheckStockMutation.hasPending('/stock/' + storeSlug + '/upload-ff-stock')) {
            status.textContent = 'Уточняю результат предыдущей попытки...';
            submission = postImport(buildFormData(false, ''));
        } else {
            submission = postImport(buildFormData(true, '')).then(function (result) {
                var data = result.data;
                if (!result.ok || !data.ok) {
                    status.textContent = 'Ошибка: ' + (data.error || 'не удалось проверить файл');
                    return null;
                }

                var preview = data.preview;
                var sourceLabel = method === 'file' ? 'В файле' : 'В таблице';
                var unmatchedNote = preview.unmatched_quantity
                    ? window.CheckStockUI.render('stock/forms/upload/unmatched-note', {
                          unmatched_quantity: preview.unmatched_quantity,
                      })
                    : '';
                var body = window.CheckStockUI.render('stock/forms/upload/body', {
                    source_quantity: preview.source_quantity,
                    content: sourceLabel.toLowerCase(),
                    added_quantity: preview.added_quantity,
                    marketplace: marketplace,
                    unmatchedNote: unmatchedNote,
                });
                var confirmation =
                    window.Modal && window.Modal.confirm
                        ? window.Modal.confirm({
                              title: 'Проверка внесения стока',
                              bodyHtml: body,
                              confirmLabel: 'ДА',
                              cancelLabel: 'ОТМЕНА',
                          })
                        : Promise.resolve(
                              window.confirm(
                                  sourceLabel +
                                      ': ' +
                                      preview.source_quantity +
                                      ' шт.\n' +
                                      'Будет внесено: ' +
                                      preview.added_quantity +
                                      ' шт.\n\n' +
                                      'Каждая загрузка прибавляет всё количество. Повторная загрузка добавит его ещё раз.\n\n' +
                                      'Внести в ФФ для распределения ' +
                                      marketplace +
                                      '?',
                              ),
                          );

                return confirmation.then(function (approved) {
                    if (!approved) {
                        status.textContent = 'Отменено. Сток не изменён.';
                        return null;
                    }
                    status.textContent = 'Вношу сток...';
                    return postImport(buildFormData(false, preview.confirmation_token));
                });
            });
        }
        submission
            .then(function (result) {
                if (!result) return;
                var data = result.data;
                if (!result.ok || !data.ok) {
                    status.textContent = 'Ошибка: ' + (data.error || 'не удалось загрузить');
                    return;
                }
                var r = data.report;
                var skipped = r.negative_skipped || [];
                var safe = window.CheckStockUI.escapeHtml;
                var detail = function (title, items, tone, describe) {
                    if (!items.length) return '';
                    var visible = items
                        .slice(0, 12)
                        .map(function (item) {
                            var article = window.CheckStockIdentifierCopy
                                ? window.CheckStockIdentifierCopy.html('Артикул', item.article, item.article)
                                : safe(item.article);
                            return window.CheckStockUI.render('stock/forms/upload/visible', {
                                article: article,
                                item: describe(item),
                            });
                        })
                        .join('');
                    var tail =
                        items.length > 12
                            ? window.CheckStockUI.render('stock/forms/upload/tail', {
                                  content: items.length - 12,
                              })
                            : '';
                    return window.CheckStockUI.render('stock/forms/upload/detail', {
                        tone: tone,
                        title: title,
                        length: items.length,
                        visible: visible,
                        tail: tail,
                    });
                };
                status.textContent = r.added_quantity
                    ? 'Готово: добавлено ' + r.added_quantity + ' шт. в ' + r.applied + ' позициях.'
                    : 'Готово: в источнике нет распознанных товаров с положительным количеством.';
                if (window.Modal) {
                    Modal.alert({
                        title: 'Результат загрузки поставки',
                        okText: 'Готово',
                        bodyHtml: window.CheckStockUI.render('stock/forms/upload/body-html-2', {
                            added_quantity: r.added_quantity,
                            applied: r.applied,
                            content: detail('Добавлено на ФФ', r.items || [], 'success', function (item) {
                                return '+' + item.quantity + ' шт.';
                            }),
                            content_2: detail(
                                'Отрицательные значения пропущены',
                                skipped,
                                'warning',
                                function (item) {
                                    return item.quantity + ' шт.';
                                },
                            ),
                            content_3: r.unmatched
                                ? window.CheckStockUI.render('stock/forms/upload/body-html', {
                                      unmatched: r.unmatched,
                                  })
                                : '',
                        }),
                    });
                }

                urlInput.value = '';
                noteInput.value = '';
                fileInput.value = '';
                var drop = fileInput.closest('.file-drop');
                var lbl = drop && drop.querySelector('.file-drop-text');
                if (lbl) lbl.textContent = 'Выбрать файл .xlsx';
                refreshFfAvailable();
            })
            .catch(function (err) {
                status.textContent = 'Ошибка загрузки: ' + err;
            })
            .finally(function () {
                btn.disabled = false;
            });
    });

    var manualRows = document.getElementById('ff-manual-rows');
    var manualAddRow = document.getElementById('ff-manual-add-row');
    var manualStatus = status;

    if (manualRows) {
        var suggestTimer = null;

        var closeSuggest = window.CheckStockSuggestions.close;

        function renderSuggest(row, items) {
            var box = row.querySelector('.suggest-box');
            if (!items.length) {
                box.classList.remove('open');
                return;
            }
            box.innerHTML = items
                .map(function (it) {
                    return window.CheckStockSuggestions.renderItem(it);
                })
                .join('');
            box.classList.add('open');

            box.querySelectorAll('.suggest-item').forEach(function (btn) {
                btn.addEventListener('mousedown', function (e) {
                    e.preventDefault();
                    row.querySelector('.manual-code').value = btn.getAttribute('data-code');
                    box.classList.remove('open');
                    row.querySelector('.manual-qty').focus();
                });
            });
        }

        function wireRow(row) {
            var code = row.querySelector('.manual-code');

            code.addEventListener('input', function () {
                var q = code.value.trim();
                if (suggestTimer) clearTimeout(suggestTimer);
                if (q.length < 2) {
                    closeSuggest(row);
                    return;
                }

                suggestTimer = setTimeout(function () {
                    fetch('/stock/' + storeSlug + '/catalog-search?q=' + encodeURIComponent(q))
                        .then(function (r) {
                            return r.json();
                        })
                        .then(function (d) {
                            renderSuggest(row, d.items || []);
                        })
                        .catch(function () {
                            closeSuggest(row);
                        });
                }, 200);
            });

            code.addEventListener('blur', function () {
                setTimeout(function () {
                    closeSuggest(row);
                }, 120);
            });

            row.querySelector('.manual-remove').addEventListener('click', function () {
                if (manualRows.children.length > 1) {
                    row.remove();
                } else {
                    row.querySelector('.manual-code').value = '';
                    row.querySelector('.manual-qty').value = '';
                }
            });
        }

        function addRow() {
            var row = document.createElement('div');
            row.className = 'manual-row';
            row.innerHTML = window.CheckStockUI.render('stock/forms/upload/add-row');
            manualRows.appendChild(row);
            wireRow(row);
            return row;
        }

        addRow();
        manualAddRow.addEventListener('click', function () {
            addRow().querySelector('.manual-code').focus();
        });

        submitManual = function () {
            var fulfillment = select.value;
            if (!fulfillment) {
                manualStatus.textContent = 'Сначала выберите фулфилмент назначения';
                return;
            }

            var items = [];
            Array.prototype.forEach.call(manualRows.children, function (row) {
                var code = row.querySelector('.manual-code').value.trim();
                var qty = row.querySelector('.manual-qty').value.trim();
                if (!code && !qty) return;
                items.push({ code: code, quantity: parseInt(qty, 10) });
            });

            if (!items.length) {
                manualStatus.textContent = 'Заполните хотя бы одну позицию';
                return;
            }

            var invalidItem = items.some(function (item) {
                return !item.code || !Number.isInteger(item.quantity) || item.quantity <= 0;
            });
            if (invalidItem) {
                manualStatus.textContent =
                    'Заполните артикул/баркод и положительное целое количество в каждой строке';
                return;
            }

            var marketplace = window.stockTable.currentMp();
            var totalQuantity = items.reduce(function (total, item) {
                return total + item.quantity;
            }, 0);
            var confirmationText =
                'Внести ' + totalQuantity + ' единиц в колонку «ФФ для распределения ' + marketplace + '»?';
            var confirmation =
                window.Modal && window.Modal.confirm
                    ? window.Modal.confirm({
                          title: 'Проверка внесения стока',
                          bodyHtml: window.CheckStockUI.render('stock/forms/upload/body-html-3', {
                              totalQuantity: totalQuantity,
                              marketplace: marketplace,
                          }),
                          confirmLabel: 'ДА',
                          cancelLabel: 'ОТМЕНА',
                      })
                    : Promise.resolve(window.confirm(confirmationText));

            confirmation.then(function (approved) {
                if (!approved) {
                    manualStatus.textContent = 'Отменено. Сток не изменён.';
                    return;
                }

                btn.disabled = true;
                manualStatus.textContent = 'Добавляю...';

                window.CheckStockMutation.fetch('/stock/' + storeSlug + '/add-ff-items', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                    body: JSON.stringify({
                        fulfillment: fulfillment,
                        marketplace: marketplace,
                        note: noteInput.value.trim(),
                        confirmed: true,
                        items: items,
                    }),
                })
                    .then(function (r) {
                        return r.json().then(function (d) {
                            return { ok: r.ok, data: d };
                        });
                    })
                    .then(function (res) {
                        if (!res.ok || !res.data.ok) {
                            manualStatus.textContent = 'Ошибка: ' + (res.data.error || 'не удалось добавить');
                            return;
                        }
                        var list = res.data.results || [];
                        manualStatus.textContent =
                            'Добавлено: ' +
                            list
                                .map(function (r) {
                                    return r.article + ' +' + r.added;
                                })
                                .join(', ');

                        manualRows.innerHTML = '';
                        addRow();
                        noteInput.value = '';
                        refreshFfAvailable();
                    })
                    .catch(function (e) {
                        manualStatus.textContent = 'Ошибка: ' + e;
                    })
                    .finally(function () {
                        btn.disabled = false;
                    });
            });
        };
    }
})();
