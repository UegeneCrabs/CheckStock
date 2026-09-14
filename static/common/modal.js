
window.Modal = (function () {
    'use strict';

    var overlay = null;
    var closeCurrent = null;

    function ensureOverlay() {
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.innerHTML = '<div class="modal-box" role="dialog" aria-modal="true"></div>';
        document.body.appendChild(overlay);

        overlay.addEventListener('mousedown', function (e) {

            if (e.target === overlay && closeCurrent) closeCurrent(false);
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && closeCurrent) closeCurrent(false);
        });

        return overlay;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function open(options) {
        var opts = options || {};
        ensureOverlay();
        var box = overlay.querySelector('.modal-box');

        var codeHtml = opts.copyText
            ? '<div class="modal-code">' +
              '<code id="modal-code-value">' + escapeHtml(opts.copyText) + '</code>' +
              '<button type="button" class="modal-copy" id="modal-copy">Скопировать</button>' +
              '</div>'
            : '';




        var textHtml = opts.bodyHtml || String(opts.text || '')
            .split('\n')
            .filter(function (line) { return line.trim() !== ''; })
            .map(function (line) { return '<p>' + escapeHtml(line) + '</p>'; })
            .join('');

        box.className = 'modal-box' + (opts.danger ? ' modal-box--danger' : '');
        box.innerHTML =
            '<h3 class="modal-title">' + escapeHtml(opts.title || '') + '</h3>' +
            '<div class="modal-text">' + textHtml + '</div>' +
            codeHtml +
            '<div class="modal-actions">' +
            (opts.cancelLabel === null
                ? ''
                : '<button type="button" class="modal-btn modal-btn--ghost" id="modal-cancel">' +
                  escapeHtml(opts.cancelLabel || 'Отмена') + '</button>') +
            '<button type="button" class="modal-btn modal-btn--primary' +
            (opts.danger ? ' modal-btn--danger' : '') + '" id="modal-ok">' +
            escapeHtml(opts.confirmLabel || 'ОК') + '</button>' +
            '</div>';

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
            okBtn.addEventListener('click', function () { closeCurrent(true); });

            var cancelBtn = box.querySelector('#modal-cancel');
            if (cancelBtn) cancelBtn.addEventListener('click', function () { closeCurrent(false); });

            var copyBtn = box.querySelector('#modal-copy');
            if (copyBtn) {
                copyBtn.addEventListener('click', function () {
                    var value = box.querySelector('#modal-code-value').textContent;
                    if (!navigator.clipboard) {
                        copyBtn.textContent = 'Скопируйте вручную';
                        return;
                    }
                    navigator.clipboard.writeText(value).then(function () {
                        copyBtn.textContent = 'Скопировано';
                        setTimeout(function () { copyBtn.textContent = 'Скопировать'; }, 1600);
                    }).catch(function () {
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
        }
    };
})();
