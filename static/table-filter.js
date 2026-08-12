(function () {
    'use strict';

    var FILTER_ICON =
        '<svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" ' +
        'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' +
        '<path d="M2 3h12l-4.6 5.4v4L7.6 14v-5.6z"/></svg>';

    var popover = null;
    var current = null;

    function ensurePopover() {
        if (popover) return popover;
        popover = document.createElement('div');
        popover.className = 'tf-popover';
        document.body.appendChild(popover);
        popover.addEventListener('click', function (e) { e.stopPropagation(); });
        return popover;
    }

    function isEmptyTable(table) {
        return !!table.querySelector('tbody tr.empty-row');
    }

    function dataRows(table) {
        return Array.prototype.filter.call(table.querySelectorAll('tbody tr'), function (tr) {
            return !tr.classList.contains('empty-row');
        });
    }



    var COLOR_COL = 'color';
    var COLOR_RED = 'Красные (нет в продаже)';
    var COLOR_NONE = 'Без выделения';

    function cellValue(row, colIndex) {
        if (colIndex === COLOR_COL) {
            return row.classList.contains('row-alert') ? COLOR_RED : COLOR_NONE;
        }
        var cell = row.children[colIndex];
        return cell ? cell.textContent.trim() : '';
    }

    function isNumericSeries(values) {
        var real = values.filter(function (v) { return v !== '' && v !== '—'; });
        if (!real.length) return false;
        return real.every(function (v) { return /^-?\d+([.,]\d+)?$/.test(v); });
    }

    function compareValues(va, vb, numeric) {
        if (numeric) {
            var na = va === '' || va === '—' ? -Infinity : parseFloat(va.replace(',', '.'));
            var nb = vb === '' || vb === '—' ? -Infinity : parseFloat(vb.replace(',', '.'));
            return na - nb;
        }
        return (va === '—' ? '' : va).localeCompare(vb === '—' ? '' : vb, 'ru');
    }

    function uniqueValues(table, colIndex) {
        var seen = {};
        var out = [];
        dataRows(table).forEach(function (row) {
            var v = cellValue(row, colIndex);
            if (!Object.prototype.hasOwnProperty.call(seen, v)) {
                seen[v] = true;
                out.push(v);
            }
        });

        var numeric = isNumericSeries(out);
        out.sort(function (a, b) { return compareValues(a, b, numeric); });
        return out;
    }

    function applyAllFilters(table) {
        var filters = table._tfFilters || {};
        var activeCols = Object.keys(filters);
        var query = table._tfSearch || '';

        dataRows(table).forEach(function (row) {
            var visible = true;

            for (var i = 0; i < activeCols.length; i++) {
                var col = activeCols[i];
                if (!filters[col].has(cellValue(row, col))) {
                    visible = false;
                    break;
                }
            }


            if (visible && query) {
                visible = row.textContent.toLowerCase().indexOf(query) !== -1;
            }

            row.style.display = visible ? '' : 'none';
        });
    }

    function updateButtonState(table, colIndex, button) {
        var active = table._tfFilters && Object.prototype.hasOwnProperty.call(table._tfFilters, colIndex);
        button.classList.toggle('tf-btn--active', !!active);
    }

    function sortColumn(table, colIndex, dir) {
        var tbody = table.querySelector('tbody');
        var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        var numeric = isNumericSeries(rows.map(function (r) { return cellValue(r, colIndex); }));

        rows.sort(function (a, b) {
            var cmp = compareValues(cellValue(a, colIndex), cellValue(b, colIndex), numeric);
            return dir === 'desc' ? -cmp : cmp;
        });

        rows.forEach(function (row) { tbody.appendChild(row); });
    }

    function closePopover() {
        if (!popover) return;
        popover.classList.remove('open');
        if (current) current.button.classList.remove('tf-btn--menu-open');
        current = null;
    }

    function renderPopoverContent(table, colIndex) {
        var values = uniqueValues(table, colIndex);
        var existing = table._tfFilters ? table._tfFilters[colIndex] : null;

        var itemsHtml = values.map(function (v) {
            var checked = !existing || existing.has(v);
            var label = v === '' ? '(пусто)' : v;
            return (
                '<label class="tf-value-item">' +
                '<input type="checkbox" value="' + escapeAttr(v) + '"' + (checked ? ' checked' : '') + '>' +
                '<span>' + escapeHtml(label) + '</span>' +
                '</label>'
            );
        }).join('');

        popover.innerHTML =
            '<div class="tf-sort">' +
            '<button type="button" class="tf-sort-btn" data-dir="asc">Сортировать А &rarr; Я</button>' +
            '<button type="button" class="tf-sort-btn" data-dir="desc">Сортировать Я &rarr; А</button>' +
            '</div>' +
            '<div class="tf-divider"></div>' +
            '<div class="tf-quick-row">' +
            '<button type="button" class="tf-link" data-action="select-all">Выбрать все (' + values.length + ')</button>' +
            '<button type="button" class="tf-link" data-action="reset">Сбросить</button>' +
            '<span class="tf-shown">Показано: <span class="tf-shown-count"></span></span>' +
            '</div>' +
            '<div class="tf-search-wrap">' +
            '<input type="text" class="tf-search-input" placeholder="Поиск">' +
            '</div>' +
            '<div class="tf-values">' + (itemsHtml || '<div class="tf-values-empty">Нет значений</div>') + '</div>' +
            '<div class="tf-actions">' +
            '<button type="button" class="tf-btn-cancel" data-action="cancel">Отмена</button>' +
            '<button type="button" class="tf-btn-ok" data-action="ok">OK</button>' +
            '</div>';

        wirePopover(table, colIndex);
        updateShownCount();
    }

    function escapeHtml(s) {
        return s.replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function escapeAttr(s) {
        return escapeHtml(s);
    }

    function updateShownCount() {
        var checked = popover.querySelectorAll('.tf-value-item input[type=checkbox]:checked').length;
        var el = popover.querySelector('.tf-shown-count');
        if (el) el.textContent = String(checked);
    }

    function wirePopover(table, colIndex) {
        popover.querySelectorAll('.tf-value-item input[type=checkbox]').forEach(function (cb) {
            cb.addEventListener('change', updateShownCount);
        });

        var search = popover.querySelector('.tf-search-input');
        search.addEventListener('input', function () {
            var q = search.value.trim().toLowerCase();
            popover.querySelectorAll('.tf-value-item').forEach(function (item) {
                var text = item.textContent.trim().toLowerCase();
                item.style.display = !q || text.indexOf(q) !== -1 ? '' : 'none';
            });
        });

        popover.querySelectorAll('.tf-sort-btn').forEach(function (btn) {
            btn.addEventListener('click', function () {
                sortColumn(table, colIndex, btn.getAttribute('data-dir'));
                closePopover();
            });
        });

        popover.querySelector('[data-action="select-all"]').addEventListener('click', function () {
            popover.querySelectorAll('.tf-value-item input[type=checkbox]').forEach(function (cb) { cb.checked = true; });
            updateShownCount();
        });

        popover.querySelector('[data-action="reset"]').addEventListener('click', function () {
            popover.querySelectorAll('.tf-value-item input[type=checkbox]').forEach(function (cb) { cb.checked = false; });
            updateShownCount();
        });

        popover.querySelector('[data-action="cancel"]').addEventListener('click', closePopover);

        popover.querySelector('[data-action="ok"]').addEventListener('click', function () {
            var boxes = Array.prototype.slice.call(popover.querySelectorAll('.tf-value-item input[type=checkbox]'));
            var selected = boxes.filter(function (cb) { return cb.checked; }).map(function (cb) { return cb.value; });

            table._tfFilters = table._tfFilters || {};
            if (selected.length === boxes.length) {
                delete table._tfFilters[colIndex];
            } else {
                table._tfFilters[colIndex] = new Set(selected);
            }

            applyAllFilters(table);
            updateButtonState(table, colIndex, current.button);
            closePopover();
        });
    }

    function positionPopover(button) {
        popover.style.visibility = 'hidden';
        popover.classList.add('open');

        var rect = button.getBoundingClientRect();
        var pw = popover.offsetWidth;
        var ph = popover.offsetHeight;

        var left = rect.left;
        if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
        if (left < 8) left = 8;

        var top = rect.bottom + 6;
        if (top + ph > window.innerHeight - 8) {
            top = rect.top - ph - 6;
            if (top < 8) top = 8;
        }

        popover.style.left = left + 'px';
        popover.style.top = top + 'px';
        popover.style.visibility = 'visible';
    }

    function openFilter(table, colIndex, button) {
        ensurePopover();
        if (current && current.button === button) {
            closePopover();
            return;
        }
        closePopover();
        current = { table: table, colIndex: colIndex, button: button };
        button.classList.add('tf-btn--menu-open');
        renderPopoverContent(table, colIndex);
        positionPopover(button);
    }

    function buildHeaderButton(th, table, colIndex) {
        var inner = document.createElement('span');
        inner.className = 'tf-th-inner';

        var label = document.createElement('span');
        label.className = 'tf-th-label';
        label.innerHTML = th.innerHTML;

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'tf-btn';
        btn.innerHTML = FILTER_ICON;
        btn.setAttribute('aria-label', 'Фильтр по столбцу');
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            openFilter(table, colIndex, btn);
        });

        inner.appendChild(label);
        inner.appendChild(btn);
        th.innerHTML = '';
        th.appendChild(inner);
    }


    function buildToolbar(table) {
        var wrap = table.closest('.table-wrap');
        if (!wrap || !wrap.parentNode) return;

        var bar = document.createElement('div');
        bar.className = 'tf-toolbar';

        var search = document.createElement('input');
        search.type = 'search';
        search.className = 'tf-search';
        search.placeholder = 'Поиск по таблице';
        search.autocomplete = 'off';

        var found = document.createElement('span');
        found.className = 'tf-found';



        search.addEventListener('input', function () {
            table._tfSearch = search.value.trim().toLowerCase();
            applyAllFilters(table);

            var rows = dataRows(table);
            var visible = rows.filter(function (r) { return r.style.display !== 'none'; }).length;
            found.textContent = table._tfSearch
                ? 'найдено: ' + visible + ' из ' + rows.length
                : '';
        });

        bar.appendChild(search);
        bar.appendChild(found);

        if (table.hasAttribute('data-color-filter')) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'tf-btn tf-btn--color';
            btn.innerHTML = FILTER_ICON + '<span>Цвет</span>';
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                openFilter(table, COLOR_COL, btn);
            });
            bar.appendChild(btn);
        }

        wrap.parentNode.insertBefore(bar, wrap);
    }

    function initTable(table) {
        if (isEmptyTable(table)) return;
        var headerRow = table.querySelector('thead tr');
        if (!headerRow) return;

        Array.prototype.forEach.call(headerRow.children, function (th, colIndex) {
            if (th.classList.contains('col-filler')) return;
            buildHeaderButton(th, table, colIndex);
        });

        buildToolbar(table);
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('table.data-table').forEach(initTable);
    });

    document.addEventListener('click', function (e) {
        if (popover && popover.classList.contains('open') && !popover.contains(e.target)) {
            closePopover();
        }
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closePopover();
    });

    window.addEventListener('scroll', function (e) {


        if (current && popover && !popover.contains(e.target)) closePopover();
    }, true);
})();
