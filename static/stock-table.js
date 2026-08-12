(function () {
    'use strict';

    var copyButtons = document.querySelectorAll('.stock-copy');
    if (!copyButtons.length) return;

    document.querySelectorAll('.stock-product-image').forEach(function (image) {
        image.addEventListener('error', function () {
            image.hidden = true;
        });
    });

    var toast = document.createElement('div');
    toast.className = 'stock-copy-toast';
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    document.body.appendChild(toast);
    var toastTimer = null;

    function fallbackCopy(value) {
        var input = document.createElement('textarea');
        input.value = value;
        input.setAttribute('readonly', '');
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.appendChild(input);
        input.select();
        var copied = document.execCommand('copy');
        input.remove();
        if (!copied) throw new Error('copy failed');
    }

    function copy(value) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(value).catch(function () {
                fallbackCopy(value);
            });
        }
        return Promise.resolve().then(function () { fallbackCopy(value); });
    }

    function showToast(message) {
        window.clearTimeout(toastTimer);
        toast.textContent = message;
        toast.classList.add('is-visible');
        toastTimer = window.setTimeout(function () {
            toast.classList.remove('is-visible');
        }, 1400);
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('.stock-copy');
        if (!button) return;

        event.stopPropagation();
        var value = button.getAttribute('data-copy-value') || '';
        var kind = button.getAttribute('data-copy-kind') || 'Значение';
        if (!value) return;

        copy(value).then(function () {
            document.querySelectorAll('.stock-copy.is-copied').forEach(function (item) {
                item.classList.remove('is-copied');
            });
            button.classList.add('is-copied');
            showToast(kind + ' скопирован');
            window.setTimeout(function () { button.classList.remove('is-copied'); }, 900);
        }).catch(function () {
            showToast('Не удалось скопировать');
        });
    });

    var table = document.getElementById('stock-table');
    if (!table) return;

    var drawerBackdrop = document.querySelector('[data-stock-drawer]');
    var drawer = drawerBackdrop && drawerBackdrop.querySelector('.stock-drawer');
    var drawerClose = drawerBackdrop && drawerBackdrop.querySelector('[data-stock-drawer-close]');
    var drawerOperation = drawerBackdrop && drawerBackdrop.querySelector('[data-stock-drawer-operation]');
    var drawerRequest = 0;
    var restoreFocus = null;
    var numberFormat = new Intl.NumberFormat('ru-RU');

    function numberFromCell(row, selector) {
        var cell = row.querySelector(selector);
        if (!cell) return 0;
        var match = cell.textContent.replace(/[\s\u00a0]/g, '').match(/-?\d+/);
        return match ? Number(match[0]) : 0;
    }

    function productValue(row, kind) {
        var node = row.querySelector('.stock-copy[data-copy-kind="' + kind + '"]');
        return node ? node.getAttribute('data-copy-value') || '' : '';
    }

    function setDrawerText(selector, value) {
        var node = drawerBackdrop.querySelector(selector);
        if (node) node.textContent = value;
    }

    function renderWarehouses(items) {
        var list = drawerBackdrop.querySelector('[data-stock-drawer-warehouses]');
        list.innerHTML = '';
        items.forEach(function (item) {
            var row = document.createElement('div');
            row.className = 'stock-drawer-warehouse';

            var identity = document.createElement('span');
            identity.className = 'stock-drawer-warehouse-name';
            var name = document.createElement('strong');
            name.textContent = item.name;
            var caption = document.createElement('small');
            caption.textContent = 'Фулфилмент';
            identity.appendChild(name);
            identity.appendChild(caption);

            var available = document.createElement('span');
            available.className = 'stock-drawer-warehouse-value';
            available.innerHTML = '<small>Доступно</small><b>' + numberFormat.format(item.available || 0) + '</b>';

            var fbs = document.createElement('span');
            fbs.className = 'stock-drawer-warehouse-value stock-drawer-warehouse-value--fbs';
            fbs.innerHTML = '<small>FBS</small><b>' + numberFormat.format(item.fbs || 0) + '</b>';

            row.appendChild(identity);
            row.appendChild(available);
            row.appendChild(fbs);
            list.appendChild(row);
        });
        setDrawerText('[data-stock-drawer-warehouse-count]', items.length + ' складов');
    }

    function loadWarehouseDetails(article) {
        var request = ++drawerRequest;
        var list = drawerBackdrop.querySelector('[data-stock-drawer-warehouses]');
        list.innerHTML = '<div class="stock-drawer-loading"><span></span>Загружаем остатки...</div>';

        var layout = document.getElementById('store-layout');
        var marketplace = layout ? layout.getAttribute('data-marketplace') || 'WB' : 'WB';
        var path = '/stock/' + window.location.pathname.split('/')[2] + '/article-detail' +
            '?article=' + encodeURIComponent(article) + '&mp=' + encodeURIComponent(marketplace);

        fetch(path)
            .then(function (response) {
                if (!response.ok) throw new Error('detail request failed');
                return response.json();
            })
            .then(function (data) {
                if (request !== drawerRequest) return;
                renderWarehouses(data.warehouses || []);
            })
            .catch(function () {
                if (request !== drawerRequest) return;
                list.innerHTML = '<div class="stock-drawer-loading stock-drawer-loading--error">Не удалось загрузить детализацию складов</div>';
                setDrawerText('[data-stock-drawer-warehouse-count]', '');
            });
    }

    function openDrawer(row) {
        if (!drawerBackdrop || !drawer) return;
        restoreFocus = row;

        var name = row.querySelector('.stock-product-name');
        var media = row.querySelector('.stock-product-media');
        var image = row.querySelector('.stock-product-image');
        var article = productValue(row, 'Артикул');
        var barcode = productValue(row, 'Баркод');
        var marketplace = document.getElementById('store-layout').getAttribute('data-marketplace') || 'WB';

        setDrawerText('[data-stock-drawer-name]', name ? name.textContent.trim() : article);
        setDrawerText('[data-stock-drawer-article]', 'Арт. ' + (article || '—'));
        setDrawerText('[data-stock-drawer-barcode]', 'Баркод ' + (barcode || '—'));
        setDrawerText('[data-stock-drawer-marketplace]', marketplace);
        setDrawerText('[data-stock-drawer-total]', numberFormat.format(numberFromCell(row, '.col-row-total')));
        setDrawerText('[data-stock-drawer-available]', numberFormat.format(numberFromCell(row, '.col-ff-available')));
        setDrawerText('[data-stock-drawer-fbs]', numberFormat.format(numberFromCell(row, '.col-fbs')));
        setDrawerText('[data-stock-drawer-fbo]', numberFormat.format(numberFromCell(row, '.col-fbo')));

        var drawerInitial = drawerBackdrop.querySelector('[data-stock-drawer-initial]');
        var drawerImage = drawerBackdrop.querySelector('[data-stock-drawer-image]');
        var drawerMedia = drawerBackdrop.querySelector('[data-stock-drawer-media]');
        drawerInitial.textContent = name && name.textContent.trim() ? name.textContent.trim().charAt(0).toUpperCase() : '?';
        drawerMedia.className = 'stock-drawer-media';
        if (media) {
            Array.prototype.forEach.call(media.classList, function (className) {
                if (className.indexOf('stock-product-media--') === 0) drawerMedia.classList.add(className);
            });
        }
        if (image && image.src) {
            drawerImage.src = image.src;
            drawerImage.hidden = false;
        } else {
            drawerImage.removeAttribute('src');
            drawerImage.hidden = true;
        }

        drawerBackdrop.hidden = false;
        document.body.classList.add('stock-drawer-open');
        window.requestAnimationFrame(function () {
            drawerBackdrop.classList.add('is-open');
            drawer.focus();
        });
        loadWarehouseDetails(article);
    }

    function closeDrawer() {
        if (!drawerBackdrop || drawerBackdrop.hidden) return;
        drawerRequest += 1;
        drawerBackdrop.classList.remove('is-open');
        document.body.classList.remove('stock-drawer-open');
        window.setTimeout(function () { drawerBackdrop.hidden = true; }, 180);
        if (restoreFocus) restoreFocus.focus();
    }

    table.addEventListener('click', function (event) {
        if (event.target.closest('.stock-copy')) return;
        var row = event.target.closest('tbody tr[data-article]');
        if (row && table.contains(row)) openDrawer(row);
    });

    table.addEventListener('keydown', function (event) {
        if (event.target.closest('.stock-copy')) return;
        var row = event.target.closest('tbody tr[data-article]');
        if (!row || (event.key !== 'Enter' && event.key !== ' ')) return;
        event.preventDefault();
        openDrawer(row);
    });

    if (drawerClose) drawerClose.addEventListener('click', closeDrawer);
    if (drawerBackdrop) drawerBackdrop.addEventListener('mousedown', function (event) {
        if (event.target === drawerBackdrop) closeDrawer();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && drawerBackdrop && !drawerBackdrop.hidden) closeDrawer();
    });

    if (drawerOperation) drawerOperation.addEventListener('click', function () {
        var movePanel = document.querySelector('[data-collapse-id="ff-move"]');
        closeDrawer();
        if (!movePanel) return;
        var toggle = movePanel.querySelector('.panel-toggle');
        if (movePanel.classList.contains('is-collapsed') && toggle) toggle.click();
        window.setTimeout(function () {
            movePanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 80);
    });

    var drawerImage = drawerBackdrop && drawerBackdrop.querySelector('[data-stock-drawer-image]');
    if (drawerImage) drawerImage.addEventListener('error', function () { drawerImage.hidden = true; });

    function enhanceTableToolbar() {
        var panel = table.closest('.stock-table-panel');
        var toolbar = panel && panel.querySelector('.tf-toolbar');
        var warehouseControl = panel && panel.querySelector('.stock-warehouse-control');
        var search = toolbar && toolbar.querySelector('.tf-search');
        var found = toolbar && toolbar.querySelector('.tf-found');
        var count = panel && panel.querySelector('[data-stock-visible-count]');
        if (!toolbar) return;

        if (search) search.placeholder = 'Артикул, баркод или название';
        var colorFilter = toolbar.querySelector('.tf-btn--color span');
        if (colorFilter) colorFilter.textContent = 'Требует внимания';
        if (warehouseControl) toolbar.appendChild(warehouseControl);

        function updateCount() {
            var rows = Array.prototype.slice.call(table.querySelectorAll('tbody tr[data-article]'));
            var visible = rows.filter(function (row) { return row.style.display !== 'none'; }).length;
            var label = 'Показано ' + visible + ' из ' + rows.length + ' товаров';
            if (count) count.textContent = label;
            if (found && !search.value.trim()) found.textContent = label;
        }

        var observer = new MutationObserver(updateCount);
        table.querySelectorAll('tbody tr[data-article]').forEach(function (row) {
            observer.observe(row, { attributes: true, attributeFilter: ['style'] });
        });
        if (search) search.addEventListener('input', function () {
            window.setTimeout(updateCount, 0);
        });
        updateCount();
    }

    document.addEventListener('DOMContentLoaded', enhanceTableToolbar);
})();
