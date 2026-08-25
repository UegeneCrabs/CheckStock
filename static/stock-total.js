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

    function visibleRows(table) {
        return Array.prototype.filter.call(table.querySelectorAll('tbody tr:not(.empty-row)'), function (row) {
            return row.style.display !== 'none';
        });
    }

    function updateTotals(table) {
        var rows = visibleRows(table);
        var positions = table.querySelector('[data-total-positions]');
        if (positions) positions.textContent = 'позиций: ' + formatNumber(rows.length);

        for (var column = 3; column <= 15; column++) {
            var total = rows.reduce(function (sum, row) {
                return sum + numberValue(row.children[column]);
            }, 0);
            var target = table.querySelector('[data-total-column="' + column + '"]');
            if (target) target.textContent = formatNumber(total);
        }

        var summaryPositions = document.querySelector('[data-summary-positions]');
        var summaryGrand = document.querySelector('[data-summary-grand]');
        if (summaryPositions) summaryPositions.textContent = formatNumber(rows.length);
        if (summaryGrand) {
            summaryGrand.textContent = formatNumber(rows.reduce(function (sum, row) {
                return sum + numberValue(row.children[3]);
            }, 0));
        }
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

        function applyStore() {
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
        }

        table.addEventListener('tablefilterchange', function () { updateTotals(table); });
        select.addEventListener('change', applyStore);
        applyStore();
    });
})();
