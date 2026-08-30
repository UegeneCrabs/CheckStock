(function () {
    'use strict';

    var SELECTOR = '[data-copy-value]';
    var tooltip = document.createElement('div');
    var toast = document.createElement('div');
    var active = null;
    var toastTimer = null;
    var resetTimer = null;

    tooltip.className = 'identifier-copy-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.hidden = true;
    toast.className = 'identifier-copy-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    document.body.appendChild(tooltip);
    document.body.appendChild(toast);

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function identifierKind(node) {
        var explicit = node.getAttribute('data-copy-kind');
        if (explicit) return explicit;
        return /баркод|штрихкод/i.test(node.textContent || '') ? 'Баркод' : 'Артикул';
    }

    function tooltipText(node) {
        return node.getAttribute('data-copy-tooltip') || 'Нажмите, чтобы скопировать';
    }

    function positionTooltip(node) {
        var rect = node.getBoundingClientRect();
        tooltip.style.left = Math.max(8, Math.min(
            window.innerWidth - tooltip.offsetWidth - 8,
            rect.left + rect.width / 2 - tooltip.offsetWidth / 2
        )) + 'px';
        var top = rect.top - tooltip.offsetHeight - 8;
        if (top < 8) top = rect.bottom + 8;
        tooltip.style.top = top + 'px';
    }

    function showTooltip(node, text) {
        if (!node || !node.getAttribute('data-copy-value')) return;
        active = node;
        tooltip.textContent = text || tooltipText(node);
        tooltip.hidden = false;
        positionTooltip(node);
    }

    function hideTooltip(node) {
        if (node && active !== node) return;
        active = null;
        tooltip.hidden = true;
    }

    function showToast(message, isError) {
        window.clearTimeout(toastTimer);
        toast.textContent = message;
        toast.classList.toggle('is-error', !!isError);
        toast.classList.add('is-visible');
        toastTimer = window.setTimeout(function () {
            toast.classList.remove('is-visible');
        }, 1500);
    }

    function fallbackCopy(value) {
        var helper = document.createElement('textarea');
        helper.value = value;
        helper.setAttribute('readonly', '');
        helper.style.position = 'fixed';
        helper.style.opacity = '0';
        document.body.appendChild(helper);
        helper.select();
        var copied = document.execCommand('copy');
        helper.remove();
        if (!copied) throw new Error('copy failed');
    }

    function copyText(value) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(value).catch(function () { fallbackCopy(value); });
        }
        return Promise.resolve().then(function () { fallbackCopy(value); });
    }

    function copyElement(node) {
        var value = node && node.getAttribute('data-copy-value') || '';
        if (!value) return Promise.resolve(false);
        window.clearTimeout(resetTimer);
        return copyText(value).then(function () {
            document.querySelectorAll(SELECTOR + '.is-copied').forEach(function (item) {
                item.classList.remove('is-copied');
            });
            node.classList.add('is-copied');
            showTooltip(node, 'Скопировано');
            showToast(identifierKind(node) + ' скопирован');
            resetTimer = window.setTimeout(function () {
                node.classList.remove('is-copied');
                if (active === node) showTooltip(node, tooltipText(node));
            }, 1100);
            return true;
        }).catch(function () {
            showTooltip(node, 'Не удалось скопировать');
            showToast('Не удалось скопировать', true);
            return false;
        });
    }

    function html(kind, value, label, className) {
        var raw = String(value === null || value === undefined ? '' : value);
        if (!raw) return escapeHtml(label || '—');
        return '<button class="copy-identifier' + (className ? ' ' + escapeHtml(className) : '')
            + '" type="button" data-copy-kind="' + escapeHtml(kind) + '" data-copy-value="'
            + escapeHtml(raw) + '" data-copy-tooltip="Нажмите, чтобы скопировать" aria-label="Скопировать '
            + escapeHtml(kind.toLocaleLowerCase('ru-RU')) + ' ' + escapeHtml(raw) + '">'
            + escapeHtml(label === undefined ? raw : label) + '</button>';
    }

    document.addEventListener('pointerover', function (event) {
        var node = event.target.closest && event.target.closest(SELECTOR);
        if (node && (!event.relatedTarget || !node.contains(event.relatedTarget))) showTooltip(node);
    });
    document.addEventListener('pointerout', function (event) {
        var node = event.target.closest && event.target.closest(SELECTOR);
        if (node && (!event.relatedTarget || !node.contains(event.relatedTarget))) hideTooltip(node);
    });
    document.addEventListener('focusin', function (event) {
        var node = event.target.closest && event.target.closest(SELECTOR);
        if (node) showTooltip(node);
    });
    document.addEventListener('focusout', function (event) {
        var node = event.target.closest && event.target.closest(SELECTOR);
        if (node) hideTooltip(node);
    });
    document.addEventListener('click', function (event) {
        var node = event.target.closest && event.target.closest(SELECTOR);
        if (!node) return;
        event.preventDefault();
        event.stopPropagation();
        copyElement(node);
    }, true);
    window.addEventListener('scroll', function () { hideTooltip(); }, true);
    window.addEventListener('resize', function () { hideTooltip(); });

    window.CheckStockIdentifierCopy = { copyElement: copyElement, html: html };
})();
