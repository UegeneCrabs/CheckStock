(function () {
    'use strict';

    var button = document.querySelector('[data-store-total]');
    var view = document.querySelector('[data-stock-store-total-view]');
    var table = document.getElementById('store-stock-total-table');
    var layout = document.getElementById('store-layout');
    if (!button || !view || !table || !layout) return;

    var normalViews = document.querySelectorAll('[data-stock-marketplace-view]');
    var marketplaceTabs = document.querySelectorAll('.mp-tab[data-mp]');
    var placeholder = document.getElementById('mp-placeholder');
    var status = view.querySelector('[data-stock-store-total-status]');
    var body = table.tBodies[0];
    var storeSlug = window.location.pathname.split('/').filter(Boolean)[1] || '';
    var numberFormat = new Intl.NumberFormat('ru-RU');
    var moneyFormat = new Intl.NumberFormat('ru-RU', {
        style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2
    });
    var totalKeys = ['grand_total', 'total_wb', 'total_ozon', 'total_yandex'];
    var quantityKeys = [
        'ff_wb', 'ff_ozon', 'ff_yandex',
        'transit_wb', 'transit_ozon', 'transit_yandex',
        'fbs_wb', 'fbs_ozon', 'fbs_yandex',
        'rfbs_wb', 'rfbs_ozon', 'rfbs_yandex',
        'fbo_wb', 'fbo_ozon', 'fbo_yandex'
    ];
    var valueKeys = totalKeys.concat(quantityKeys);
    var loaded = false;
    var loading = false;

    function number(value) {
        return Number(value || 0);
    }

    function setStatus(text, isError) {
        if (!status) return;
        status.textContent = text || '';
        status.classList.toggle('ff-select-status--bad', Boolean(isError));
    }

    function cell(text, className) {
        var node = document.createElement('td');
        if (className) node.className = className;
        node.textContent = text;
        return node;
    }

    function quantityCell(value) {
        var amount = number(value);
        var node = cell(numberFormat.format(amount));
        node.setAttribute('data-filter-value', String(amount));
        return node;
    }

    function renderRows(rows) {
        body.innerHTML = '';
        if (!rows.length) {
            var empty = document.createElement('tr');
            empty.className = 'empty-row';
            var message = cell('В этом магазине пока нет товаров и остатков');
            message.colSpan = 23;
            empty.appendChild(message);
            body.appendChild(empty);
            updateTotals();
            return;
        }

        rows.forEach(function (item) {
            var row = document.createElement('tr');
            row.setAttribute('data-grand-total', String(number(item.grand_total)));
            row.setAttribute(
                'data-purchase-price',
                item.purchase_price === null || item.purchase_price === undefined
                    ? '' : String(number(item.purchase_price))
            );
            row.appendChild(cell(String(item.article || '')));
            row.appendChild(cell(String(item.barcode || '')));
            var name = cell(String(item.name || item.article || 'Без названия'));
            name.title = name.textContent;
            row.appendChild(name);
            var priceValue = row.getAttribute("data-purchase-price");
            var price = cell(priceValue === "" ? "—" : moneyFormat.format(Number(priceValue)));
            price.setAttribute("data-filter-value", priceValue);
            row.appendChild(price);
            valueKeys.forEach(function (key) {
                row.appendChild(quantityCell(item[key]));
            });
            body.appendChild(row);
        });
        updateTotals();
        if (window.CheckStockTableFilter) window.CheckStockTableFilter.refresh(table);
    }

    function visibleRows() {
        return Array.prototype.filter.call(body.querySelectorAll('tr:not(.empty-row)'), function (row) {
            return row.style.display !== 'none';
        });
    }

    function updateTotals() {
        var rows = visibleRows();
        var positions = table.querySelector('[data-store-total-positions]');
        if (positions) positions.textContent = 'позиций: ' + numberFormat.format(rows.length);
        valueKeys.forEach(function (key, index) {
            var target = table.querySelector('[data-store-total-key="' + key + '"]');
            if (!target) return;
            var total = rows.reduce(function (sum, row) {
                var valueCell = row.children[index + 4];
                return sum + number(valueCell && valueCell.getAttribute('data-filter-value'));
            }, 0);
            target.textContent = numberFormat.format(total);
        });
        var pricedRows = rows.filter(function (row) {
            return row.dataset.purchasePrice !== '' && Number.isFinite(Number(row.dataset.purchasePrice));
        });
        var costPositions = table.querySelector('[data-store-cost-positions]');
        if (costPositions) {
            costPositions.textContent = 'ЗЦ: ' + numberFormat.format(pricedRows.length)
                + ' из ' + numberFormat.format(rows.length) + ' поз.';
        }
        valueKeys.forEach(function (key, index) {
            var target = table.querySelector('[data-store-cost-key="' + key + '"]');
            if (!target) return;
            var total = pricedRows.reduce(function (sum, row) {
                var valueCell = row.children[index + 4];
                return sum + number(valueCell && valueCell.getAttribute('data-filter-value'))
                    * number(row.dataset.purchasePrice);
            }, 0);
            target.textContent = moneyFormat.format(total);
        });
    }

    function updateAddress() {
        if (!window.history || !window.history.replaceState) return;
        var url = new URL(window.location.href);
        url.searchParams.set('mp', 'TOTAL');
        window.history.replaceState(null, '', url.pathname + url.search);
    }

    function showTotal() {
        normalViews.forEach(function (node) { node.hidden = true; });
        marketplaceTabs.forEach(function (tab) {
            tab.classList.remove('active');
            tab.setAttribute('aria-selected', 'false');
        });
        button.classList.add('active');
        button.setAttribute('aria-selected', 'true');
        view.hidden = false;
        layout.classList.remove('is-hidden');
        if (placeholder) placeholder.hidden = true;
        updateAddress();
    }

    function loadTotal() {
        showTotal();
        if (loaded || loading || !storeSlug) return;
        loading = true;
        button.disabled = true;
        setStatus('Загружаем тотал по трём площадкам...', false);
        fetch('/stock/' + encodeURIComponent(storeSlug) + '/total-data')
            .then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (data) {
                    if (!response.ok || data.ok === false) {
                        throw new Error(data.detail || data.error || 'Не удалось загрузить тотал');
                    }
                    return data;
                });
            })
            .then(function (data) {
                renderRows(data.rows || []);
                loaded = true;
                setStatus('Показан общий остаток WB + OZON + Яндекс Маркета', false);
            })
            .catch(function (error) {
                body.innerHTML = '<tr class="empty-row"><td colspan="23">Не удалось загрузить остатки</td></tr>';
                setStatus('Ошибка: ' + error.message, true);
            })
            .finally(function () {
                loading = false;
                button.disabled = false;
            });
    }

    table.addEventListener('tablefilterchange', updateTotals);
    button.addEventListener('click', loadTotal);

    if (new URLSearchParams(window.location.search).get('mp') === 'TOTAL') loadTotal();
}());
