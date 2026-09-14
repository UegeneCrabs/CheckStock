window.Modal = (function () {
    'use strict';

    var overlay = null;
    var closeCurrent = null;

    function ensureOverlay() {
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = window.CheckStockUI.render('common/modal/ensure-overlay');
        document.body.appendChild(overlay);

        overlay.addEventListener('mousedown', function (e) {
            if (e.target === overlay && closeCurrent) closeCurrent(false);
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && closeCurrent) closeCurrent(false);
        });

        return overlay;
    }

    var escapeHtml = window.CheckStockUI.escapeHtml;

    function open(options) {
        var opts = options || {};
        ensureOverlay();
        var box = overlay.querySelector('.modal-box');

        var codeHtml = opts.copyText
            ? window.CheckStockUI.render('common/modal/code-html', { copyText: opts.copyText })
            : '';

        var textHtml =
            opts.bodyHtml ||
            String(opts.text || '')
                .split('\n')
                .filter(function (line) {
                    return line.trim() !== '';
                })
                .map(function (line) {
                    return window.CheckStockUI.render('common/modal/text-html', { line: line });
                })
                .join('');

        box.className = 'modal-box' + (opts.danger ? ' modal-box--danger' : '');
        box.innerHTML = window.CheckStockUI.render('common/modal/open-2', {
            content: opts.title || '',
            textHtml: textHtml,
            codeHtml: codeHtml,
            content_2:
                opts.cancelLabel === null
                    ? ''
                    : window.CheckStockUI.render('common/modal/open', {
                          content: opts.cancelLabel || 'Отмена',
                      }),
            content_3: opts.danger ? ' modal-btn--danger' : '',
            content_4: opts.confirmLabel || 'ОК',
        });

        overlay.classList.add('open');
        document.body.classList.add('modal-open');

        return new Promise(function (resolve) {
            closeCurrent = function (result) {
                overlay.classList.remove('open');
                document.body.classList.remove('modal-open');
                closeCurrent = null;
                resolve(result);
            };

            var okBtn = box.querySelector('#modal-ok');
            okBtn.addEventListener('click', function () {
                closeCurrent(true);
            });

            var cancelBtn = box.querySelector('#modal-cancel');
            if (cancelBtn)
                cancelBtn.addEventListener('click', function () {
                    closeCurrent(false);
                });

            var copyBtn = box.querySelector('#modal-copy');
            if (copyBtn) {
                copyBtn.addEventListener('click', function () {
                    var value = box.querySelector('#modal-code-value').textContent;
                    if (!navigator.clipboard) {
                        copyBtn.textContent = 'Скопируйте вручную';
                        return;
                    }
                    navigator.clipboard
                        .writeText(value)
                        .then(function () {
                            copyBtn.textContent = 'Скопировано';
                            setTimeout(function () {
                                copyBtn.textContent = 'Скопировать';
                            }, 1600);
                        })
                        .catch(function () {
                            copyBtn.textContent = 'Скопируйте вручную';
                        });
                });
            }

            okBtn.focus();
        });
    }

    return {
        confirm: open,
        alert: function (options) {
            var opts = options || {};
            opts.cancelLabel = null;
            opts.confirmLabel = opts.confirmLabel || 'Понятно';
            return open(opts);
        },
    };
})();
