(function () {
    'use strict';
    var root = document.getElementById('uetp');
    if (!root) return;
    var config = JSON.parse(document.getElementById('uetp-config').textContent);
    function node(id) { return document.getElementById('uetp-' + id); }
    function escape(value) { return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    }); }
    function numeric(value) { return value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value); }
    function format(value) { return numeric(value) === null ? '—' : Number(value).toLocaleString('ru-RU', { maximumFractionDigits:2 }); }
    function copyIdentifier(article) {
        return '<button class="copy-identifier" type="button" data-copy-kind="Артикул" data-copy-value="'
            + escape(article) + '" data-copy-tooltip="Нажмите, чтобы скопировать" aria-label="Скопировать артикул '
            + escape(article) + '">' + escape(article) + '</button>';
    }
    var columns = ['product', 'store_name', 'current_price', 'current_drr', 'current_roi', 'target_price', 'target_drr', 'target_roi'];
    var rows = [], page = 1, pageSize = 50, sort = 'target_price', descending = true, requestId = 0;
    var periodFrom = '', periodTo = '', calculatorRow = null, calculatorInitial = null;
    var table = node('table'), tableFilters = {};
    function columnValue(row, index) {
        index = Number(index);
        if (index === 0) return row.name + ' · Арт. ' + row.article;
        if (index === 1) return String(row.store_name || '');
        var value = numeric(row[columns[index]]);
        return value === null ? '—' : String(value);
    }
    function normalizeSearch(value) {
        return String(value).toLocaleLowerCase('ru-RU').replace(/,/g, '.').replace(/\u2212/g, '-').replace(/\s+/g, ' ').trim();
    }
    function matchesSearch(row, query) {
        var text = columns.map(function (key, index) {
            return columnValue(row, index) + (index > 1 ? ' ' + format(row[key]) : '');
        }).join(' ');
        text = normalizeSearch(text);
        return query.split(' ').every(function (part) { return text.includes(part); });
    }
    function externallyFilteredRows() {
        var query = normalizeSearch(node('search').value);
        return rows.filter(function (row) { return !query || matchesSearch(row, query); });
    }
    function applySort(key, direction) { sort = key; descending = direction === 'desc'; page = 1; render(); }
    table._tfAdapter = {
        values: function (index) { return externallyFilteredRows().map(function (row) { return columnValue(row, index); }); },
        filter: function (filters) { tableFilters = filters || {}; page = 1; render(); },
        sort: function (index, direction) { applySort(columns[Number(index)], direction); }
    };
    var help = node('help');
    help.addEventListener('mouseenter', function () { help.open = true; });
    help.addEventListener('mouseleave', function () {
        if (!help.contains(document.activeElement)) help.open = false;
    });
    help.addEventListener('focusout', function (event) {
        if (!help.contains(event.relatedTarget)) help.open = false;
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') help.open = false;
    });
    document.addEventListener('click', function (event) {
        if (!help.contains(event.target)) help.open = false;
    });
    function status(message, error) {
        node('status').textContent = message;
        node('status').hidden = !message;
        node('status').classList.toggle('is-error', Boolean(error));
    }
    config.stores.forEach(function (store) {
        var option = document.createElement('option'); option.value = store.slug; option.textContent = store.name;
        node('store').appendChild(option);
    });
    function below(row) { return row.current_price != null && row.target_price != null && row.current_price < row.target_price; }
    function cell(value, warnings, extra, explanation, notes) {
        var warningText = (warnings || []).join('\n');
        var noteText = (notes || []).join('\n');
        var title = [explanation || '', noteText, warningText].filter(Boolean).join('\n');
        return '<td data-filter-value="' + escape(numeric(value) === null ? '—' : String(Number(value)))
            + '" class="' + (warningText ? 'is-warning ' : '') + (extra || '') + '"><span'
            + (title ? ' tabindex="0" title="' + escape(title) + '" aria-label="' + escape(format(value) + '. ' + title) + '"' : '')
            + '>' + format(value) + '</span></td>';
    }
    function filteredRows() {
        var activeColumns = Object.keys(tableFilters);
        var visible = externallyFilteredRows().filter(function (row) {
            return activeColumns.every(function (index) { return tableFilters[index].has(columnValue(row, index)); });
        });
        visible.sort(function (a, b) {
            var index = columns.indexOf(sort);
            if (index < 2) return columnValue(a, index).localeCompare(columnValue(b, index), 'ru') * (descending ? -1 : 1);
            var left = numeric(a[sort]), right = numeric(b[sort]);
            if (left === null) return right === null ? 0 : 1;
            if (right === null) return -1;
            return (left - right) * (descending ? -1 : 1);
        });
        return visible;
    }
    function render() {
        var visible = filteredRows();
        var pages = Math.max(1, Math.ceil(visible.length / pageSize)); page = Math.min(page, pages);
        node('rows').innerHTML = visible.slice((page - 1) * pageSize, page * pageSize).map(function (row) {
            var image = /^https?:\/\//i.test(row.image_url) ? '<img src="' + escape(row.image_url) + '" alt="" loading="lazy">' : '';
            var detail = 'Период: ' + row.weekly.period_from + ' — ' + row.weekly.period_to + '. Данные за '
                + row.weekly.days + ' из 7 дней. Реклама: ' + format(row.weekly.spend) + ' ₽; заказы: '
                + format(row.weekly.orders_amount) + ' ₽ (' + format(row.weekly.orders_count) + ' шт.); выкуп: ' + format(row.weekly.buyout_percent) + '%.';
            var drrDetail = detail + (row.current_drr === 0 ? (row.weekly.spend === 0
                ? '\nДРР равен 0: расходы на рекламу за доступные дни равны 0 ₽.'
                : '\nДРР близок к нулю и округлён до двух знаков после запятой.') : '');
            var roiDetail = detail + (row.current_roi == null ? '\nROI не рассчитан: см. причины ниже.'
                : '\nROI рассчитан по текущей цене и параметрам калькулятора.') + ' Реклама на выкупленную штуку: '
                + format(row.weekly.advertising_per_unit) + ' ₽.';
            var targetDetail = row.target_retail_price == null ? '' : 'До СПП: ' + format(row.target_retail_price) + ' ₽; с СПП: '
                + format(row.target_spp_price) + ' ₽. ROI после округления: ' + format(row.target_actual_roi) + '%.';
            return '<tr><td data-filter-value="' + escape(columnValue(row, 0)) + '"><div class="uetp-product">' + image
                + '<div><strong><button class="uetp-product-link" type="button" data-open-product="' + escape(row.store_slug + '|' + row.article)
                + '" title="Открыть калькулятор">' + escape(row.name) + '</button></strong><small>Арт. '
                + copyIdentifier(row.article) + '</small></div></div></td><td>' + escape(row.store_name) + '</td>'
                + cell(row.current_price, [], below(row) ? 'is-below' : '', row.price_date ? 'Цена WB за ' + row.price_date : '')
                + cell(row.current_drr, row.current_drr_warnings, '', drrDetail, row.current_drr_notes)
                + cell(row.current_roi, row.current_warnings, row.current_roi < 0 ? 'is-negative' : '', roiDetail, row.current_notes)
                + cell(row.target_price, row.target_warnings, 'uetp-target-value', targetDetail)
                + cell(row.target_drr, [], row.target_overridden ? 'uetp-target-custom' : '', row.target_overridden ? 'Индивидуальная цель товара' : 'Цель из настроек кабинета ' + row.store_name)
                + cell(row.target_roi, [], row.target_overridden ? 'uetp-target-custom' : '', row.target_overridden ? 'Индивидуальная цель товара' : 'Цель из настроек кабинета ' + row.store_name) + '</tr>';
        }).join('') || '<tr class="empty-row"><td colspan="8">Нет товаров по выбранным условиям.</td></tr>';
        node('count').textContent = 'Товаров: ' + visible.length + ' · Цена ниже целевой: ' + visible.filter(below).length;
        node('page').textContent = page + ' / ' + pages; node('prev').disabled = page <= 1; node('next').disabled = page >= pages;
        table.querySelectorAll('th[data-filter-column]').forEach(function (th) {
            var selected = columns[Number(th.dataset.filterColumn)] === sort;
            th.setAttribute('aria-sort', selected ? (descending ? 'descending' : 'ascending') : 'none');
        });
    }
    async function load() {
        var id = ++requestId; node('reload').disabled = true;
        status('Загрузка отчёта…'); rows = []; page = 1; render();
        try {
            var response = await window.fetch('/api/unit-economics-1c/reports/target-price?store=' + encodeURIComponent(node('store').value), {
                headers: { 'Accept':'application/json', 'X-Requested-With':'fetch' }, cache:'no-store'
            });
            var data = await response.json(); if (id !== requestId) return;
            if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось загрузить отчёт');
            rows = data.rows; periodFrom = data.period_from || ''; periodTo = data.period_to || ''; status('');
            render();
            if (window.CheckStockTableFilter) window.CheckStockTableFilter.refresh(table);
            return true;
        } catch (error) { if (id === requestId) status(error.message || 'Ошибка загрузки', true); }
        finally { if (id === requestId) node('reload').disabled = false; }
        return false;
    }
    node('reload').addEventListener('click', load); node('store').addEventListener('change', load);
    node('search').addEventListener('input', function () { page = 1; render(); });
    node('page-size').addEventListener('change', function () {
        var selectedSize = Number(node('page-size').value);
        pageSize = [20, 50, 100].indexOf(selectedSize) === -1 ? 50 : selectedSize;
        page = 1;
        render();
    });
    node('prev').addEventListener('click', function () { page -= 1; render(); });
    node('next').addEventListener('click', function () { page += 1; render(); });
    // The shared filter wraps/clones header contents, so use event delegation.
    table.addEventListener('click', function (event) {
        var opener = event.target.closest('[data-open-product]');
        if (opener) {
            var key = opener.dataset.openProduct;
            var row = rows.find(function (item) { return item.store_slug + '|' + item.article === key; });
            if (row) openCalculator(row);
            return;
        }
        var button = event.target.closest('[data-sort]');
        if (button) applySort(button.dataset.sort, sort === button.dataset.sort && descending ? 'asc' : 'desc');
    });
    function calcValue(key) { return numeric(root.querySelector('[data-calc="' + key + '"]').value); }
    function setCalc(key, value) {
        var input = root.querySelector('[data-calc="' + key + '"]');
        input.value = numeric(value) === null ? '' : String(Math.round(Number(value) * 100) / 100);
    }
    function calculatedAdvertisingRub(retail, drr, buyoutPercent) {
        if (retail === null || drr === null || buyoutPercent === null) return null;
        var buyoutRatio = Math.min(Math.max(buyoutPercent, 0), 100) / 100;
        var amount = Math.max(retail, 0) * Math.max(drr, 0) / 100 * buyoutRatio;
        return Math.round(amount * 100) / 100;
    }
    function calculatedDrrPercent(retail, advertisingRub, buyoutPercent) {
        if (retail === null || retail <= 0 || advertisingRub === null
            || buyoutPercent === null || buyoutPercent <= 0) return null;
        return advertisingRub / (retail * buyoutPercent / 100) * 100;
    }
    function syncLogisticsForBuyout() {
        var buyoutPercent = calcValue('buyout_percent');
        if (!calculatorInitial || buyoutPercent === null) return;
        var delivery = numeric(calculatorInitial.delivery_wb_rub) || 0;
        var returnCost = numeric(calculatorInitial.return_cost_rub) || 0;
        var acceptance = numeric(calculatorInitial.paid_acceptance_cost) || 0;
        var buyoutRatio = Math.min(Math.max(buyoutPercent, 0), 100) / 100;
        setCalc('delivery_with_returns', delivery * buyoutRatio
            + (returnCost + delivery * 2) * (1 - buyoutRatio) + acceptance);
    }
    function syncPrices(source) {
        var retail = calcValue('retail'), client = calcValue('client'), wallet = calcValue('wallet');
        var spp = calcValue('spp'), walletPercent = calcValue('wallet_percent');
        if ((source === 'retail' || source === 'spp') && retail !== null && spp !== null) {
            client = Math.round(retail * (1 - spp / 100)); setCalc('client', client);
            if (walletPercent !== null) setCalc('wallet', client - Math.ceil(client * walletPercent / 100));
        } else if (source === 'client' && client !== null && spp !== null) {
            retail = client / Math.max(.0001, 1 - spp / 100); setCalc('retail', retail);
            if (walletPercent !== null) setCalc('wallet', client - Math.ceil(client * walletPercent / 100));
        } else if (source === 'wallet_percent' && client !== null && walletPercent !== null) {
            setCalc('wallet', client - Math.ceil(client * walletPercent / 100));
        } else if (source === 'wallet' && wallet !== null && walletPercent !== null && spp !== null) {
            client = Math.round(wallet / Math.max(.0001, 1 - walletPercent / 100)); setCalc('client', client);
            setCalc('retail', client / Math.max(.0001, 1 - spp / 100));
        }
    }
    function calculatorTaxSystem() {
        return calculatorInitial && calculatorInitial.tax_system === 'osno' ? 'osno' : 'usn';
    }
    function syncPercentAndRub(percentKey, rubKey, source, base, priceChanged) {
        var percent = calcValue(percentKey), rubles = calcValue(rubKey);
        if (source === rubKey) {
            setCalc(percentKey, base === null || base <= 0 || rubles === null ? null : rubles / base * 100);
        } else if (source === percentKey || source === 'fill' || priceChanged) {
            setCalc(rubKey, base === null || percent === null ? null : base * percent / 100);
        }
    }
    function syncCalculatedAmounts(source) {
        var retail = calcValue('retail'), client = calcValue('client');
        var priceChanged = ['retail','spp','client','wallet_percent','wallet'].indexOf(source) !== -1;
        syncPercentAndRub('wb_commission_percent', 'wb_commission_rub', source, retail, priceChanged);
        syncPercentAndRub('acquiring_percent', 'acquiring_rub', source, retail, priceChanged);
        syncPercentAndRub('team_commission_percent', 'team_commission_rub', source, retail, priceChanged);

        var storageRate = calcValue('storage_wb_rub'), turnoverDays = calcValue('turnover_days');
        var storageTotal = calcValue('storage_total');
        if (source === 'storage_total') {
            setCalc('storage_wb_rub', turnoverDays === null || turnoverDays <= 0 || storageTotal === null
                ? null : storageTotal / turnoverDays);
        } else if (source === 'storage_wb_rub' || source === 'turnover_days' || source === 'fill') {
            setCalc('storage_total', storageRate === null || turnoverDays === null ? null : storageRate * turnoverDays);
        }

        var vatPercent = calcValue('vat_percent'), vatRub = calcValue('vat_rub');
        if (source === 'vat_rub') {
            setCalc('vat_percent', client === null || vatRub === null || vatRub >= client
                ? null : vatRub * 100 / (client - vatRub));
        } else if (source === 'vat_percent' || source === 'fill' || priceChanged) {
            setCalc('vat_rub', client === null || vatPercent === null ? null : client * vatPercent / (100 + vatPercent));
        }

        vatRub = calcValue('vat_rub');
        var secondaryPercent = calcValue('secondary_tax_percent');
        var secondaryRub = calcValue('secondary_tax_rub');
        var secondaryBase = calculatorTaxSystem() === 'osno' ? client
            : client === null || vatRub === null ? null : client - vatRub;
        if (source === 'secondary_tax_rub') {
            setCalc('secondary_tax_percent', secondaryBase === null || secondaryBase <= 0 || secondaryRub === null
                ? null : secondaryRub / secondaryBase * 100);
        } else if (source === 'secondary_tax_percent' || source === 'vat_percent' || source === 'vat_rub'
            || source === 'fill' || priceChanged) {
            setCalc('secondary_tax_rub', secondaryBase === null || secondaryPercent === null
                ? null : secondaryBase * secondaryPercent / 100);
        }
    }
    function calculate() {
        if (!calculatorRow) return;
        var retail = calcValue('retail'), client = calcValue('client'), purchase = calcValue('purchase_price');
        var required = ['acquiring_rub','delivery_with_returns','storage_total','wb_commission_rub','advertising_rub',
            'fulfillment_cost','team_commission_rub','vat_rub','secondary_tax_rub'];
        if (retail === null || client === null || purchase === null || required.some(function (key) { return calcValue(key) === null; })) {
            node('calculator-results').innerHTML = '<p>Недостаточно данных для расчёта.</p>'; return;
        }
        var vat = calcValue('vat_rub'), secondary = calcValue('secondary_tax_rub');
        var acquiring = calcValue('acquiring_rub'), commission = calcValue('wb_commission_rub');
        var team = calcValue('team_commission_rub'), storage = calcValue('storage_total');
        var net = retail - acquiring - calcValue('delivery_with_returns') - storage - commission - calcValue('advertising_rub');
        var margin = net - purchase - calcValue('fulfillment_cost') - team - vat - secondary;
        var roi = purchase > 0 ? margin / purchase * 100 : null;
        node('calculator-results').innerHTML = '<div><span>Чистая прибыль</span><strong class="' + (margin < 0 ? 'is-negative' : 'is-positive') + '">' + format(margin) + ' ₽</strong></div>'
            + '<div><span>ROI</span><strong class="' + (roi !== null && roi < 0 ? 'is-negative' : 'is-positive') + '">' + format(roi) + '%</strong></div>';
    }
    function solveTargetPrice() {
        if (!calculatorInitial) return;
        var spp = calcValue('spp'), targetRoi = calcValue('target_roi'), purchase = calcValue('purchase_price');
        var drr = calcValue('drr'), buyoutPercent = calcValue('buyout_percent');
        var required = ['acquiring_percent','delivery_with_returns','storage_wb_rub','turnover_days',
            'wb_commission_percent','fulfillment_cost','team_commission_percent',
            'vat_percent','secondary_tax_percent'];
        if (spp === null || spp >= 100 || targetRoi === null || purchase === null || purchase <= 0
            || drr === null || buyoutPercent === null || buyoutPercent <= 0
            || required.some(function (key) { return calcValue(key) === null; })) return;
        var customerFactor = Math.max(.0001, 1 - spp / 100);
        var vatFactor = customerFactor * calcValue('vat_percent') / (100 + calcValue('vat_percent'));
        var secondaryFactor = calculatorTaxSystem() === 'osno'
            ? customerFactor * calcValue('secondary_tax_percent') / 100
            : (customerFactor - vatFactor) * calcValue('secondary_tax_percent') / 100;
        var revenueFactor = 1 - (calcValue('acquiring_percent') + calcValue('wb_commission_percent')
            + calcValue('team_commission_percent')) / 100 - vatFactor - secondaryFactor;
        revenueFactor -= drr / 100 * buyoutPercent / 100;
        if (revenueFactor <= 0) return;
        var requiredRetail = (purchase + purchase * targetRoi / 100 + calcValue('fulfillment_cost')
            + calcValue('delivery_with_returns') + calcValue('storage_wb_rub') * calcValue('turnover_days'))
            / revenueFactor;
        if (!Number.isFinite(requiredRetail) || requiredRetail > 1000000000) return;
        var centerCents = Math.max(Math.round(requiredRetail * 100), 1);
        var best = null;
        for (var retailCents = Math.max(centerCents - 150, 1);
            retailCents <= centerCents + 150; retailCents += 1) {
            var retailCandidate = retailCents / 100;
            var clientCandidate = Math.max(Math.round(retailCandidate * customerFactor), 1);
            var advertisingCandidate = calculatedAdvertisingRub(retailCandidate, drr, buyoutPercent);
            var acquiring = retailCandidate * calcValue('acquiring_percent') / 100;
            var commission = retailCandidate * calcValue('wb_commission_percent') / 100;
            var team = retailCandidate * calcValue('team_commission_percent') / 100;
            var vat = clientCandidate * calcValue('vat_percent') / (100 + calcValue('vat_percent'));
            var secondary = calculatorTaxSystem() === 'osno'
                ? clientCandidate * calcValue('secondary_tax_percent') / 100
                : (clientCandidate - vat) * calcValue('secondary_tax_percent') / 100;
            var margin = retailCandidate - acquiring - calcValue('delivery_with_returns')
                - calcValue('storage_wb_rub') * calcValue('turnover_days') - commission
                - advertisingCandidate - purchase - calcValue('fulfillment_cost') - team - vat - secondary;
            var actualRoi = margin / purchase * 100;
            var score = Math.abs(actualRoi - targetRoi);
            if (!best || score < best.score
                || (score === best.score && Math.abs(retailCents - requiredRetail * 100) < best.distance)) {
                best = { client:clientCandidate, retail:retailCandidate, advertising:advertisingCandidate,
                    score:score, distance:Math.abs(retailCents - requiredRetail * 100) };
            }
        }
        if (!best) return;
        var client = best.client, retail = best.retail;
        setCalc('retail', retail); setCalc('client', client);
        var walletPercent = calcValue('wallet_percent');
        if (walletPercent !== null) setCalc('wallet', client - Math.ceil(client * walletPercent / 100));
        syncCalculatedAmounts('retail');
        setCalc('advertising_rub', best.advertising);
    }
    function fillCalculator(values) {
        root.querySelectorAll('[data-calc]').forEach(function (input) { input.value = ''; });
        Object.keys(values || {}).forEach(function (key) { if (root.querySelector('[data-calc="' + key + '"]')) setCalc(key, values[key]); });
        setCalc('secondary_tax_percent', values.tax_system === 'osno' ? values.osno_percent : values.usn_percent);
        var taxName = values.tax_system === 'osno' ? 'ОСНО' : 'УСН';
        node('tax-label').textContent = taxName;
        node('tax-rub-label').textContent = 'Налог ' + taxName;
        syncCalculatedAmounts('fill');
        calculate();
    }
    function openCalculator(row) {
        calculatorRow = row; calculatorInitial = Object.assign({}, row.calculator || {});
        var initial = String(row.name || row.article || '?').slice(0, 1).toLocaleUpperCase('ru-RU');
        node('drawer-thumb').innerHTML = '<span>' + escape(initial) + '</span>'
            + (/^https?:\/\//i.test(row.image_url || '') ? '<img src="' + escape(row.image_url) + '" alt="">' : '');
        var drawerImage = node('drawer-thumb').querySelector('img');
        if (drawerImage) drawerImage.addEventListener('error', function () { drawerImage.remove(); }, { once:true });
        node('drawer-title').textContent = row.name;
        node('drawer-meta').innerHTML = escape(row.store_name) + ' · Арт. ' + copyIdentifier(row.article);
        targetStatus('', false);
        root.classList.add('has-calculator'); document.body.classList.add('uetp-calculator-open');
        node('drawer').classList.add('is-open'); node('overlay').classList.add('is-open');
        node('drawer').setAttribute('aria-hidden', 'false'); fillCalculator(calculatorInitial);
    }
    function closeCalculator() {
        calculatorRow = null; root.classList.remove('has-calculator'); document.body.classList.remove('uetp-calculator-open');
        node('drawer').classList.remove('is-open');
        node('overlay').classList.remove('is-open'); node('drawer').setAttribute('aria-hidden', 'true');
    }
    root.querySelectorAll('[data-calc]').forEach(function (input) { input.addEventListener('input', function () {
        var source = input.dataset.calc; syncPrices(source);
        var priceChanged = ['retail','spp','client','wallet_percent','wallet'].indexOf(source) !== -1;
        if (source === 'buyout_percent') syncLogisticsForBuyout();
        if (source === 'drr' || source === 'buyout_percent' || priceChanged) {
            setCalc('advertising_rub', calculatedAdvertisingRub(
                calcValue('retail'), calcValue('drr'), calcValue('buyout_percent')
            ));
        }
        if (source === 'drr' || source === 'buyout_percent') {
            solveTargetPrice();
        } else if (source === 'target_roi') solveTargetPrice();
        else if (source === 'advertising_rub') setCalc('drr', calculatedDrrPercent(
            calcValue('retail'), calcValue('advertising_rub'), calcValue('buyout_percent')
        ));
        syncCalculatedAmounts(source);
        calculate();
    }); });
    function targetStatus(message, error) {
        node('target-status').textContent = message;
        node('target-status').hidden = !message;
        node('target-status').classList.toggle('is-error', Boolean(error));
    }
    async function refreshOpenCalculator(message) {
        var key = calculatorRow.store_slug + '|' + calculatorRow.article;
        if (!await load()) return false;
        var updated = rows.find(function (item) { return item.store_slug + '|' + item.article === key; });
        if (!updated) { closeCalculator(); return false; }
        openCalculator(updated); targetStatus(message, false); return true;
    }
    node('target-save').disabled = !config.canEdit;
    node('calculator-reset').disabled = !config.canEdit;
    node('target-save').addEventListener('click', async function () {
        if (!calculatorRow || !config.canEdit) return;
        var drr = calcValue('drr'), roi = calcValue('target_roi');
        if (drr === null || drr < 0 || drr > 100 || roi === null || roi < 0 || roi > 1000000) {
            targetStatus('Проверьте целевые ДРР и ROI.', true); return;
        }
        var button = node('target-save'); button.disabled = true; node('calculator-reset').disabled = true;
        try {
            var response = await fetch('/api/unit-economics-1c/reports/target-price/'
                + encodeURIComponent(calculatorRow.store_slug) + '/targets', {
                method:'PUT', headers:{'Content-Type':'application/json','X-Requested-With':'fetch'},
                body:JSON.stringify({article:calculatorRow.article,target_drr_percent:drr,target_roi_percent:roi})
            });
            var data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить цели');
            await refreshOpenCalculator('Индивидуальные цели товара сохранены.');
        } catch (error) { targetStatus(error.message || 'Не удалось сохранить цели', true); }
        finally { button.disabled = !config.canEdit; node('calculator-reset').disabled = !config.canEdit; }
    });
    node('calculator-reset').addEventListener('click', async function () {
        if (!calculatorRow || !config.canEdit) return;
        var button = node('calculator-reset'); button.disabled = true; node('target-save').disabled = true;
        try {
            var response = await fetch('/api/unit-economics-1c/reports/target-price/'
                + encodeURIComponent(calculatorRow.store_slug) + '/targets?article='
                + encodeURIComponent(calculatorRow.article), {
                method:'DELETE', headers:{'Accept':'application/json','X-Requested-With':'fetch'}
            });
            var data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || 'Не удалось сбросить цели');
            await refreshOpenCalculator('');
        } catch (error) { targetStatus(error.message || 'Не удалось сбросить цели', true); }
        finally { button.disabled = !config.canEdit; node('target-save').disabled = !config.canEdit; }
    });
    node('drawer-close').addEventListener('click', closeCalculator); node('overlay').addEventListener('click', closeCalculator);
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && calculatorRow) closeCalculator(); });
    node('export').addEventListener('click', async function () {
        var button = node('export'); button.disabled = true;
        try {
            var response = await fetch('/api/unit-economics-1c/reports/target-price.xlsx', { method:'POST',
                headers:{'Content-Type':'application/json','X-Requested-With':'fetch'},
                body:JSON.stringify({period_from:periodFrom,period_to:periodTo,rows:filteredRows().map(function (row) {
                    return {store_slug:row.store_slug,store_name:row.store_name,name:row.name,article:row.article,
                        current_price:row.current_price,current_drr:row.current_drr,current_roi:row.current_roi,
                        target_price:row.target_price,target_drr:row.target_drr,target_roi:row.target_roi};
                })}) });
            if (!response.ok) throw new Error('Не удалось сформировать XLSX');
            var blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
            link.href = url; link.download = 'target_price_' + periodFrom + '_' + periodTo + '.xlsx'; link.click();
            setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        } catch (error) { status(error.message, true); } finally { button.disabled = false; }
    });
    function resizeTable() {
        var wrap = node('table-wrap');
        var viewportHeight = window.visualViewport ? window.visualViewport.height : window.innerHeight;
        var top = wrap.getBoundingClientRect().top + window.scrollY;
        wrap.style.maxHeight = Math.max(240, Math.floor(viewportHeight - top - node('footer').offsetHeight - 16)) + 'px';
    }
    var resizeFrame;
    function scheduleResize() { window.cancelAnimationFrame(resizeFrame); resizeFrame = window.requestAnimationFrame(resizeTable); }
    window.addEventListener('resize', scheduleResize);
    var workspace = root.closest('.workspace');
    if (workspace) workspace.addEventListener('transitionend', scheduleResize);
    if (window.visualViewport) window.visualViewport.addEventListener('resize', scheduleResize);
    if (window.ResizeObserver) {
        var observer = new ResizeObserver(scheduleResize);
        [root.querySelector('.uetp-heading'), root.querySelector('.uetp-controls'), node('status'), node('footer')].forEach(function (element) {
            observer.observe(element);
        });
    }
    scheduleResize();
    load();
})();
