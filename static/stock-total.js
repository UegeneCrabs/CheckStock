(function () {
    'use strict';

    function numberValue(cell) {
        if (!cell) return 0;
        var raw = cell.getAttribute('data-filter-value') || cell.textContent || '0';
        return Number(String(raw).replace(/[\s\u00a0\u202f]/g, '').replace(',', '.')) || 0;
    }

    function formatNumber(value) {
        return Math.round(value).toLocaleString('ru-RU');
    }

    var moneyFormat = new Intl.NumberFormat('ru-RU', {
        style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2
    });

    function visibleRows(table) {
        return Array.prototype.filter.call(table.querySelectorAll('tbody tr:not(.empty-row)'), function (row) {
            return row.style.display !== 'none';
        });
    }

    function updateTotals(table) {
        var rows = visibleRows(table);
        var positions = table.querySelector('[data-total-positions]');
        if (positions) positions.textContent = 'позиций: ' + formatNumber(rows.length);

        table.querySelectorAll('[data-total-column]').forEach(function (target) {
            var column = Number(target.getAttribute('data-total-column'));
            var total = rows.reduce(function (sum, row) {
                return sum + numberValue(row.children[column]);
            }, 0);
            target.textContent = formatNumber(total);
        });

        var pricedRows = rows.filter(function (row) {
            return row.dataset.purchasePrice !== '' && Number.isFinite(Number(row.dataset.purchasePrice));
        });
        var costPositions = table.querySelector('[data-cost-total-positions]');
        if (costPositions) {
            costPositions.textContent = 'ЗЦ: ' + formatNumber(pricedRows.length)
                + ' из ' + formatNumber(rows.length) + ' поз.';
        }
        table.querySelectorAll('[data-cost-total-column]').forEach(function (target) {
            var column = Number(target.getAttribute('data-cost-total-column'));
            var total = pricedRows.reduce(function (sum, row) {
                return sum + numberValue(row.children[column]) * Number(row.dataset.purchasePrice);
            }, 0);
            target.textContent = moneyFormat.format(total);
        });

    }

    function sortByGrandTotal(table) {
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.querySelectorAll('tr:not(.empty-row)'));
        rows.sort(function (left, right) {
            var difference = Number(right.dataset.grandTotal || 0) - Number(left.dataset.grandTotal || 0);
            if (difference) return difference;
            return left.textContent.localeCompare(right.textContent, 'ru');
        });
        rows.forEach(function (row) { body.appendChild(row); });
    }

    document.addEventListener('DOMContentLoaded', function () {
        var table = document.getElementById('stock-total-table');
        var select = document.getElementById('stock-total-store');
        if (!table || !select) return;

        function syncLinks(selected, updateAddress) {
            var query = selected ? '?store=' + encodeURIComponent(selected) : '';
            var download = document.querySelector('.stock-total-download');
            if (download) download.setAttribute('href', '/stock/total.xlsx' + query);
            if (updateAddress && window.history && window.history.replaceState) {
                window.history.replaceState(null, '', '/stock/total' + query);
            }
        }

        function applyStore(updateAddress) {
            var selected = select.value;
            table.querySelectorAll('tbody tr:not(.empty-row)').forEach(function (row) {
                row.dataset.externalHidden = selected && row.dataset.store !== selected ? 'true' : 'false';
            });
            sortByGrandTotal(table);
            if (window.CheckStockTableFilter) {
                window.CheckStockTableFilter.refresh(table);
            } else {
                table.querySelectorAll('tbody tr:not(.empty-row)').forEach(function (row) {
                    row.style.display = row.dataset.externalHidden === 'true' ? 'none' : '';
                });
                updateTotals(table);
            }
            syncLinks(selected, updateAddress === true);
        }

        table.addEventListener('tablefilterchange', function () { updateTotals(table); });
        select.addEventListener('change', function () { applyStore(true); });
        applyStore(false);
    });
})();
