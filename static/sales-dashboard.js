(function () {
    'use strict';

    var root = document.querySelector('[data-sales-dashboard]');
    if (!root) return;

    var form = root.querySelector('[data-sales-filters]');
    var chart = root.querySelector('.sales-chart');
    var chartPanel = root.querySelector('[data-sales-chart-panel]');
    var chartStateLayer = root.querySelector('[data-sales-chart-state]');
    var errorBox = root.querySelector('[data-sales-error]');
    var applyButton = root.querySelector('[data-sales-apply]');
    var exportLink = root.querySelector('[data-sales-export]');
    var syncLabel = root.querySelector('[data-sales-sync-label]');
    var syncDot = root.querySelector('[data-sales-sync-dot]');
    var subtitle = root.querySelector('[data-sales-chart-subtitle]');
    var hoverLayer = root.querySelector('[data-sales-hover]');
    var referenceLine = root.querySelector('[data-sales-reference-line]');
    var pointsLayer = root.querySelector('[data-sales-points]');
    var hitArea = root.querySelector('[data-sales-chart-hitarea]');
    var tooltip = root.querySelector('[data-sales-tooltip]');
    var xLabels = root.querySelector('[data-sales-x-labels]');
    var yLabels = Array.prototype.slice.call(root.querySelectorAll('[data-y-label]'));
    var presets = Array.prototype.slice.call(root.querySelectorAll('[data-sales-preset]'));
    var legendButtons = Array.prototype.slice.call(root.querySelectorAll('[data-sales-toggle]'));
    var seriesKeys = ['orders', 'fbo', 'fbs', 'cancellations', 'sales'];
    var lineElements = {};
    var tooltipElements = {};
    var tooltipGroups = {};
    var kpiElements = {};
    var kpiNotes = {};

    seriesKeys.forEach(function (key) {
        lineElements[key] = root.querySelector('[data-sales-line="' + key + '"]');
        tooltipElements[key] = root.querySelector('[data-sales-tooltip-' + key + ']');
        tooltipGroups[key] = root.querySelector('[data-sales-tooltip-series="' + key + '"]');
    });
    ['orders', 'sales', 'cancellations', 'count'].forEach(function (key) {
        kpiElements[key] = root.querySelector('[data-sales-kpi="' + key + '"]');
        kpiNotes[key] = root.querySelector('[data-sales-kpi-note="' + key + '"]');
    });

    var area = root.querySelector('[data-sales-area]');
    var tooltipPeriod = root.querySelector('[data-sales-tooltip-period]');
    var tooltipRect = tooltip.querySelector('rect');
    var currentState = null;
    var activeIndex = 0;
    var requestNumber = 0;
    var bounds = { left: 72, right: 988, top: 48, bottom: 330 };
    var marketplaceNames = {
        'WB': 'Wildberries',
        'OZON': 'Ozon',
        'YANDEX MARKET': 'Яндекс Маркет'
    };
    var rangeLimits = { 'WB': 90, 'OZON': 365, 'YANDEX MARKET': 365 };
    var colors = {
        orders: 'total', fbo: 'fbo', fbs: 'fbs',
        cancellations: 'cancellations', sales: 'sales'
    };
    var activeSeries = {
        orders: true, fbo: true, fbs: true,
        cancellations: true, sales: true
    };

    function activeSeriesKeys() {
        return seriesKeys.filter(function (key) { return activeSeries[key]; });
    }

    function seriesLabel(key) {
        var button = legendButtons.find(function (item) { return item.dataset.salesToggle === key; });
        return button ? button.textContent.trim() : key;
    }

    function syncLegend() {
        legendButtons.forEach(function (button) {
            var enabled = activeSeries[button.dataset.salesToggle];
            button.classList.toggle('is-off', !enabled);
            button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
        });
    }

    function layoutTooltip(keys) {
        seriesKeys.forEach(function (key) {
            var group = tooltipGroups[key];
            var rowIndex = keys.indexOf(key);
            group.style.display = rowIndex === -1 ? 'none' : '';
            if (rowIndex === -1) return;
            var textY = 52 + rowIndex * 28;
            group.querySelector('circle').setAttribute('cy', textY - 4);
            Array.prototype.forEach.call(group.querySelectorAll('text'), function (textElement) {
                textElement.setAttribute('y', textY);
            });
        });
        var height = 38 + keys.length * 28;
        tooltipRect.setAttribute('height', height);
        return height;
    }

    function number(value) {
        var parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function formatInputDate(value) {
        var year = value.getFullYear();
        var month = String(value.getMonth() + 1).padStart(2, '0');
        var day = String(value.getDate()).padStart(2, '0');
        return year + '-' + month + '-' + day;
    }

    function parseInputDate(value) {
        var parts = String(value || '').split('-').map(Number);
        return new Date(parts[0], parts[1] - 1, parts[2]);
    }

    function dayCount() {
        var start = parseInputDate(form.elements.date_from.value);
        var end = parseInputDate(form.elements.date_to.value);
        return Math.round((end - start) / 86400000) + 1;
    }

    function money(value) {
        return number(value).toLocaleString('ru-RU', {
            style: 'currency', currency: 'RUB', maximumFractionDigits: 0
        });
    }

    function compactMoney(value) {
        value = number(value);
        if (Math.abs(value) >= 1000000) {
            return (value / 1000000).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млн ₽';
        }
        if (Math.abs(value) >= 1000) {
            return (value / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' тыс. ₽';
        }
        return money(value);
    }

    function axisMoney(value) {
        if (!value) return '0';
        if (Math.abs(value) >= 1000000) {
            return (value / 1000000).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' млн';
        }
        if (Math.abs(value) >= 1000) {
            return (value / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' тыс.';
        }
        return value.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
    }

    function formatDate(value, includeYear) {
        var date = parseInputDate(value);
        return date.toLocaleDateString('ru-RU', includeYear ? {
            day: 'numeric', month: 'short', year: 'numeric'
        } : { day: 'numeric', month: 'short' }).replace('.', '');
    }

    function formatDateTime(value) {
        if (!value) return '';
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) return '';
        return date.toLocaleString('ru-RU', {
            day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
        });
    }

    function niceMaximum(value) {
        if (value <= 0) return 1;
        var exponent = Math.pow(10, Math.floor(Math.log10(value)));
        var fraction = value / exponent;
        var nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
        return nice * exponent;
    }

    function points(values, maximum) {
        var count = values.length;
        var width = bounds.right - bounds.left;
        return values.map(function (value, index) {
            var x = count === 1 ? bounds.left + width / 2 : bounds.left + width * index / (count - 1);
            var y = bounds.bottom - number(value) / maximum * (bounds.bottom - bounds.top);
            return [Number(x.toFixed(1)), Number(y.toFixed(1))];
        });
    }

    function smoothPath(items) {
        if (!items.length) return '';
        if (items.length === 1) return 'M ' + items[0][0] + ' ' + items[0][1];
        var path = 'M ' + items[0][0] + ' ' + items[0][1];
        for (var index = 0; index < items.length - 1; index += 1) {
            var current = items[index];
            var next = items[index + 1];
            var previous = items[index - 1] || current;
            var afterNext = items[index + 2] || next;
            var cp1x = current[0] + (next[0] - previous[0]) / 6;
            var cp1y = current[1] + (next[1] - previous[1]) / 6;
            var cp2x = next[0] - (afterNext[0] - current[0]) / 6;
            var cp2y = next[1] - (afterNext[1] - current[1]) / 6;
            path += ' C ' + cp1x.toFixed(1) + ' ' + cp1y.toFixed(1) + ', ' +
                cp2x.toFixed(1) + ' ' + cp2y.toFixed(1) + ', ' + next[0] + ' ' + next[1];
        }
        return path;
    }

    function showChartState(title, loading) {
        chartStateLayer.hidden = false;
        chartStateLayer.classList.toggle('is-loading', Boolean(loading));
        chartStateLayer.querySelector('strong').textContent = title;
    }

    function hideChartState() {
        chartStateLayer.hidden = true;
    }

    function showError(message) {
        errorBox.textContent = message || '';
        errorBox.hidden = !message;
    }

    function setLoading(loading) {
        chartPanel.classList.toggle('is-loading', loading);
        applyButton.disabled = loading;
        if (loading) {
            showError('');
            showChartState('Загружаем данные', true);
            hoverLayer.classList.remove('is-visible');
        }
    }

    function updateExportLink() {
        var params = new URLSearchParams({
            date_from: form.elements.date_from.value,
            date_to: form.elements.date_to.value,
            marketplace: form.elements.marketplace.value
        });
        if (form.elements.store.value) params.set('store', form.elements.store.value);
        exportLink.href = '/sales/orders.xlsx?' + params.toString();
    }

    function deltaText(value) {
        if (value === null || typeof value === 'undefined') return '';
        var sign = number(value) > 0 ? '+' : '';
        return sign + number(value).toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + '% к прошлому периоду';
    }

    function renderKpi(data) {
        var totals = data.totals;
        kpiElements.orders.textContent = compactMoney(totals.orders_amount);
        kpiNotes.orders.textContent = 'FBO ' + compactMoney(totals.fbo_amount) + ' · FBS ' + compactMoney(totals.fbs_amount);

        kpiElements.sales.textContent = compactMoney(totals.sales_amount);
        kpiNotes.sales.textContent = totals.sales_count.toLocaleString('ru-RU') + ' шт.' +
            (deltaText(totals.sales_delta) ? ' · ' + deltaText(totals.sales_delta) : '');

        kpiElements.cancellations.textContent = compactMoney(totals.cancellations_amount);
        kpiNotes.cancellations.textContent = totals.cancel_rate.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) +
            '% · ' + totals.cancellations_count.toLocaleString('ru-RU') + ' шт.';

        kpiElements.count.textContent = totals.orders_count.toLocaleString('ru-RU') + ' шт.';
        kpiNotes.count.textContent = deltaText(totals.orders_delta) || 'Без отмен';
    }

    function renderSync(data) {
        var sync = data.sync;
        syncDot.classList.remove('is-ready', 'is-warning', 'is-waiting');
        if (sync.errors && !sync.last_success_at) {
            syncDot.classList.add('is-warning');
            syncLabel.textContent = 'Нет доступа к заказам: ' + sync.errors + ' кабинета';
            return;
        }
        if (!sync.last_success_at) {
            syncDot.classList.add('is-waiting');
            syncLabel.textContent = 'Синхронизация еще не завершена';
            return;
        }
        if (sync.errors) {
            syncDot.classList.add('is-warning');
            syncLabel.textContent = formatDateTime(sync.last_success_at) + ' · ошибок: ' + sync.errors;
            return;
        }
        syncDot.classList.add('is-ready');
        syncLabel.textContent = 'Обновлено ' + formatDateTime(sync.last_success_at);
    }

    function renderLabels(series) {
        var count = series.length;
        var desired = 11;
        var step = Math.max(1, Math.ceil((count - 1) / desired));
        var indices = [];
        for (var index = 0; index < count; index += step) indices.push(index);
        if (indices[indices.length - 1] !== count - 1) indices.push(count - 1);
        xLabels.innerHTML = indices.map(function (itemIndex) {
            var x = count === 1 ? (bounds.left + bounds.right) / 2 :
                bounds.left + (bounds.right - bounds.left) * itemIndex / (count - 1);
            return '<text x="' + x.toFixed(1) + '" y="370">' + formatDate(series[itemIndex].date, false) + '</text>';
        }).join('');
    }

    function renderChart(data) {
        var series = data.series;
        var keys = activeSeriesKeys();
        var maximumValue = 0;
        keys.forEach(function (key) {
            series.forEach(function (item) { maximumValue = Math.max(maximumValue, number(item[key])); });
        });
        var maximum = niceMaximum(maximumValue * 1.08);
        var chartPoints = {};
        seriesKeys.forEach(function (key) {
            chartPoints[key] = points(series.map(function (item) { return item[key]; }), maximum);
            lineElements[key].setAttribute('d', activeSeries[key] ? smoothPath(chartPoints[key]) : '');
            lineElements[key].setAttribute('aria-hidden', activeSeries[key] ? 'false' : 'true');
        });
        var ordersPath = smoothPath(chartPoints.orders);
        area.setAttribute('d', activeSeries.orders && ordersPath ? ordersPath + ' L ' + bounds.right + ' ' + bounds.bottom +
            ' L ' + bounds.left + ' ' + bounds.bottom + ' Z' : '');
        yLabels.forEach(function (label) {
            var level = number(label.getAttribute('data-y-label'));
            label.textContent = axisMoney(maximum * level / 4);
        });
        renderLabels(series);
        currentState = { data: data, points: chartPoints, activeKeys: keys };
        setActivePoint(Math.floor((series.length - 1) / 2), false);
        if (!keys.length) showChartState('Выберите хотя бы один показатель', false);
        else if (maximumValue) hideChartState();
        else showChartState('За выбранный период данных нет', false);
    }

    function setActivePoint(index, visible) {
        if (!currentState || !currentState.data.series.length) return;
        var keys = currentState.activeKeys;
        if (!keys.length) {
            hoverLayer.classList.remove('is-visible');
            hoverLayer.setAttribute('aria-hidden', 'true');
            return;
        }
        var series = currentState.data.series;
        activeIndex = Math.max(0, Math.min(series.length - 1, index));
        var item = series[activeIndex];
        var referenceKey = activeSeries.orders ? 'orders' : keys[0];
        var point = currentState.points[referenceKey][activeIndex];
        var anchorY = Math.min.apply(null, keys.map(function (key) {
            return currentState.points[key][activeIndex][1];
        }));
        var tooltipHeight = layoutTooltip(keys);
        var tooltipX = point[0] > 720 ? point[0] - 266 : point[0] + 16;
        var tooltipY = Math.max(50, Math.min(bounds.bottom - tooltipHeight - 4, anchorY - 70));
        referenceLine.setAttribute('x1', point[0]);
        referenceLine.setAttribute('x2', point[0]);
        pointsLayer.innerHTML = keys.map(function (key) {
            var activePoint = currentState.points[key][activeIndex];
            var radius = key === 'orders' ? 5.5 : 4;
            return '<circle class="sales-dot sales-dot--' + colors[key] + '" cx="' + activePoint[0] +
                '" cy="' + activePoint[1] + '" r="' + radius + '"></circle>';
        }).join('');
        tooltip.setAttribute('transform', 'translate(' + tooltipX + ' ' + tooltipY + ')');
        tooltipPeriod.textContent = formatDate(item.date, true);
        Object.keys(tooltipElements).forEach(function (key) {
            tooltipElements[key].textContent = money(item[key]);
        });
        hoverLayer.classList.toggle('is-visible', visible);
        hoverLayer.setAttribute('aria-hidden', visible ? 'false' : 'true');
        hitArea.setAttribute('aria-valuemax', series.length);
        hitArea.setAttribute('aria-valuenow', activeIndex + 1);
        hitArea.setAttribute('aria-valuetext', formatDate(item.date, true) + ', ' +
            seriesLabel(referenceKey).toLowerCase() + ' ' + money(item[referenceKey]));
    }

    function indexFromPointer(event) {
        if (!currentState) return 0;
        var rectangle = chart.getBoundingClientRect();
        var viewX = (event.clientX - rectangle.left) / rectangle.width * 1020;
        var ratio = Math.max(0, Math.min(1, (viewX - bounds.left) / (bounds.right - bounds.left)));
        return Math.round(ratio * (currentState.data.series.length - 1));
    }

    function validateRange() {
        var start = parseInputDate(form.elements.date_from.value);
        var end = parseInputDate(form.elements.date_to.value);
        if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 'Укажите обе даты';
        if (end < start) return 'Дата начала должна быть раньше даты окончания';
        var days = dayCount();
        var marketplace = form.elements.marketplace.value;
        if (days > rangeLimits[marketplace]) {
            return 'Для ' + marketplaceNames[marketplace] + ' можно выбрать не больше ' + rangeLimits[marketplace] + ' дней';
        }
        var maximum = parseInputDate(form.elements.date_to.max || root.dataset.defaultTo);
        var oldest = new Date(maximum.getFullYear(), maximum.getMonth(), maximum.getDate() - rangeLimits[marketplace] + 1);
        if (start < oldest) {
            return 'Для ' + marketplaceNames[marketplace] + ' доступны данные с ' +
                oldest.toLocaleDateString('ru-RU');
        }
        return '';
    }

    function updateDateMinimum() {
        var marketplace = form.elements.marketplace.value;
        var maximum = parseInputDate(form.elements.date_to.max || root.dataset.defaultTo);
        var oldest = new Date(maximum.getFullYear(), maximum.getMonth(), maximum.getDate() - rangeLimits[marketplace] + 1);
        form.elements.date_from.min = formatInputDate(oldest);
        form.elements.date_to.min = formatInputDate(oldest);
    }

    function updatePresetState() {
        var days = dayCount();
        presets.forEach(function (button) {
            button.classList.toggle('is-active', number(button.dataset.salesPreset) === days);
        });
    }

    async function loadData() {
        var validationError = validateRange();
        if (validationError) {
            showError(validationError);
            return;
        }
        updatePresetState();
        updateExportLink();
        setLoading(true);
        var thisRequest = ++requestNumber;
        var params = new URLSearchParams({
            date_from: form.elements.date_from.value,
            date_to: form.elements.date_to.value,
            marketplace: form.elements.marketplace.value
        });
        if (form.elements.store.value) params.set('store', form.elements.store.value);
        try {
            var response = await fetch('/api/sales?' + params.toString(), { headers: { Accept: 'application/json' } });
            var data = await response.json();
            if (!response.ok || data.ok === false) throw new Error(data.error || 'Не удалось загрузить данные');
            if (thisRequest !== requestNumber) return;
            subtitle.textContent = form.elements.store.options[form.elements.store.selectedIndex].text + ' · ' +
                marketplaceNames[form.elements.marketplace.value] + ' · ' +
                formatDate(data.period.from, false) + ' — ' + formatDate(data.period.to, false);
            rangeLimits[data.marketplace] = data.limits.max_range_days;
            renderChart(data);
            renderKpi(data);
            renderSync(data);
        } catch (error) {
            if (thisRequest !== requestNumber) return;
            showError(error.message || 'Не удалось загрузить данные');
            showChartState('Данные временно недоступны', false);
        } finally {
            if (thisRequest === requestNumber) setLoading(false);
        }
    }

    presets.forEach(function (button) {
        button.addEventListener('click', function () {
            var days = number(button.dataset.salesPreset);
            var end = parseInputDate(form.elements.date_to.value || root.dataset.defaultTo);
            var start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - days + 1);
            form.elements.date_from.value = formatInputDate(start);
            updatePresetState();
        });
    });
    legendButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            var key = button.dataset.salesToggle;
            activeSeries[key] = !activeSeries[key];
            syncLegend();
            if (currentState) renderChart(currentState.data);
        });
    });
    form.elements.date_from.addEventListener('change', updatePresetState);
    form.elements.date_to.addEventListener('change', updatePresetState);
    form.elements.marketplace.addEventListener('change', function () {
        updateDateMinimum();
        showError(validateRange());
        updateExportLink();
    });
    form.elements.store.addEventListener('change', updateExportLink);
    form.addEventListener('submit', function (event) {
        event.preventDefault();
        loadData();
    });
    hitArea.addEventListener('pointerenter', function (event) { setActivePoint(indexFromPointer(event), true); });
    hitArea.addEventListener('pointermove', function (event) { setActivePoint(indexFromPointer(event), true); });
    hitArea.addEventListener('pointerdown', function (event) { setActivePoint(indexFromPointer(event), true); });
    hitArea.addEventListener('pointerleave', function () {
        hoverLayer.classList.remove('is-visible');
        hoverLayer.setAttribute('aria-hidden', 'true');
    });
    hitArea.addEventListener('focus', function () { setActivePoint(activeIndex, true); });
    hitArea.addEventListener('blur', function () {
        hoverLayer.classList.remove('is-visible');
        hoverLayer.setAttribute('aria-hidden', 'true');
    });
    hitArea.addEventListener('keydown', function (event) {
        if (!currentState) return;
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        if (event.key === 'Home') activeIndex = 0;
        else if (event.key === 'End') activeIndex = currentState.data.series.length - 1;
        else if (event.key === 'ArrowLeft') activeIndex -= 1;
        else activeIndex += 1;
        setActivePoint(activeIndex, true);
    });

    updateDateMinimum();
    updateExportLink();
    syncLegend();
    loadData();
})();
