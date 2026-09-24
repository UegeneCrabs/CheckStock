(function () {
    'use strict';

    var root = document.querySelector('.finance-report');
    if (!root) return;

    var range = root.querySelector('#finance-report-range');
    var dateFrom = root.querySelector('#finance-date-from');
    var dateTo = root.querySelector('#finance-date-to');
    var rangePicker = root.querySelector('#finance-date-range-picker');
    var rangeLabel = root.querySelector('#finance-date-range-label');
    var rangePanel = root.querySelector('#finance-date-range-panel');
    var calendarTitle = root.querySelector('[data-finance-calendar-title]');
    var calendarDays = root.querySelector('[data-finance-calendar-days]');
    var storeSelect = root.querySelector('#finance-store');
    var applyButton = root.querySelector('#finance-apply');
    var formatter = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
    var calendarMonth;

    function format(value, kind) {
        var amount = Number(value);
        if (!Number.isFinite(amount)) return '—';
        if (kind === 'integer') return new Intl.NumberFormat('ru-RU').format(amount);
        if (kind === 'percent') return formatter.format(amount) + '%';
        return formatter.format(amount);
    }

    function formatDate(value) {
        return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' })
            .format(new Date(value + 'T00:00:00'));
    }

    function isoDate(value) {
        var offset = value.getTimezoneOffset() * 60000;
        return new Date(value.getTime() - offset).toISOString().slice(0, 10);
    }

    function setDefaultDates() {
        var today = new Date();
        var start = new Date(today);
        start.setDate(today.getDate() - 7);
        today.setDate(today.getDate() - 1);
        dateFrom.value = isoDate(start);
        dateTo.value = isoDate(today);
    }

    function parseIsoDate(value) {
        var parts = value.split('-').map(Number);
        return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    function updateRangeLabel() {
        if (!dateFrom.value) {
            rangeLabel.textContent = 'Выберите период';
            return;
        }
        rangeLabel.textContent = formatDate(dateFrom.value) + (dateTo.value ? ' — ' + formatDate(dateTo.value) : ' — …');
    }

    function renderCalendar() {
        if (!calendarMonth) calendarMonth = parseIsoDate(dateTo.value || dateFrom.value || isoDate(new Date()));
        var year = calendarMonth.getFullYear();
        var month = calendarMonth.getMonth();
        var first = new Date(year, month, 1);
        var gridStart = new Date(year, month, 1 - ((first.getDay() + 6) % 7));
        var from = dateFrom.value;
        var to = dateTo.value;
        calendarTitle.textContent = new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' }).format(first)
            .replace(/^./, function (letter) { return letter.toUpperCase(); });
        calendarDays.innerHTML = Array.from({ length: 42 }, function (_, index) {
            var day = new Date(gridStart);
            day.setDate(gridStart.getDate() + index);
            var value = isoDate(day);
            var classes = [];
            if (day.getMonth() !== month) classes.push('is-outside');
            if (from && to && value > from && value < to) classes.push('is-in-range');
            if (value === from || value === to) classes.push('is-range-edge');
            return '<button type="button" data-finance-calendar-day="' + value + '" class="' + classes.join(' ') + '">' + day.getDate() + '</button>';
        }).join('');
    }

    function openCalendar() {
        calendarMonth = parseIsoDate(dateTo.value || dateFrom.value || isoDate(new Date()));
        renderCalendar();
        rangePanel.hidden = false;
        rangePicker.setAttribute('aria-expanded', 'true');
    }

    function closeCalendar() {
        rangePanel.hidden = true;
        rangePicker.setAttribute('aria-expanded', 'false');
    }

    function selectCalendarDay(value) {
        if (!dateFrom.value || dateTo.value) {
            dateFrom.value = value;
            dateTo.value = '';
        } else if (value < dateFrom.value) {
            dateFrom.value = value;
        } else {
            dateTo.value = value;
            closeCalendar();
        }
        updateRangeLabel();
        renderCalendar();
        if (dateFrom.value && dateTo.value) loadReport();
    }

    function escapeHtml(value) {
        var node = document.createElement('span');
        node.textContent = value;
        return node.innerHTML;
    }

    async function loadStoreOptions() {
        try {
            var response = await fetch('/api/finance-reports/yandex/stores', {
                headers: { Accept: 'application/json' }, cache: 'no-store'
            });
            var payload = await response.json();
            if (!response.ok || !payload.ok) throw new Error('Не удалось загрузить магазины');
            storeSelect.innerHTML = '<option value="">Все магазины</option>' + (payload.stores || []).map(function (store) {
                return '<option value="' + escapeHtml(store.slug) + '">' + escapeHtml(store.name) + '</option>';
            }).join('');
        } catch (error) {
            console.error('Не удалось загрузить список магазинов', error);
        }
    }

    function renderValues(values) {
        root.querySelectorAll('[data-finance-key]').forEach(function (cell) {
            var key = cell.getAttribute('data-finance-key');
            var value = values[key];
            var percent = cell.getAttribute('data-finance-percent');
            cell.textContent = value === null || value === undefined ? '—' : format(value, cell.getAttribute('data-finance-format'));
            if (percent !== null && value !== null && value !== undefined && values.sales) {
                var caption = document.createElement('small');
                caption.textContent = formatter.format(Number(value) / Number(values.sales) * 100) + '%';
                cell.appendChild(caption);
            }
        });
    }

    async function loadReport() {
        var start = dateFrom.value;
        var end = dateTo.value;
        var selectedStore = storeSelect.value;
        if (!start || !end || start > end) return;
        applyButton.disabled = true;
        try {
            var response = await fetch('/api/finance-reports/yandex?date_from=' + encodeURIComponent(start) + '&date_to=' + encodeURIComponent(end) + (selectedStore ? '&store_slug=' + encodeURIComponent(selectedStore) : ''), {
                headers: { Accept: 'application/json' }, cache: 'no-store'
            });
            var payload = await response.json();
            if (!response.ok || !payload.ok) throw new Error(payload.error || 'Не удалось загрузить отчет');
            range.textContent = formatDate(start) + ' — ' + formatDate(end);
            renderValues(payload.values || {});
        } catch (error) {
            console.error('Не удалось загрузить финансовый отчет', error);
        } finally {
            applyButton.disabled = false;
        }
    }

    applyButton.addEventListener('click', loadReport);
    storeSelect.addEventListener('change', function () {
        if (dateFrom.value && dateTo.value) loadReport();
    });
    rangePicker.addEventListener('click', function () {
        if (rangePanel.hidden) openCalendar();
        else closeCalendar();
    });
    rangePanel.addEventListener('click', function (event) {
        // Selecting a day redraws this panel. Do not let the same click be
        // treated as an outside click after the selected button is replaced.
        event.stopPropagation();
        var day = event.target.closest('[data-finance-calendar-day]');
        if (day) {
            selectCalendarDay(day.getAttribute('data-finance-calendar-day'));
            return;
        }
        if (event.target.closest('[data-finance-calendar-prev]')) {
            calendarMonth.setMonth(calendarMonth.getMonth() - 1);
            renderCalendar();
        }
        if (event.target.closest('[data-finance-calendar-next]')) {
            calendarMonth.setMonth(calendarMonth.getMonth() + 1);
            renderCalendar();
        }
    });
    document.addEventListener('click', function (event) {
        if (!event.target.closest('.finance-range-picker')) closeCalendar();
    });
    setDefaultDates();
    updateRangeLabel();
    loadStoreOptions();
    loadReport();
})();
