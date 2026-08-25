(function () {
    'use strict';

    var root = document.getElementById('unit-economics-1c');
    var configNode = document.getElementById('ue1c-config');
    if (!root || !configNode) return;

    var config = JSON.parse(configNode.textContent || '{}');
    var products = Array.isArray(config.products) ? config.products : [];
    var stores = Array.isArray(config.stores) ? config.stores : [];
    var canEdit = config.canEdit === true;
    var productsById = {};
    products.forEach(function (product) { productsById[product.id] = product; });

    var integer = new Intl.NumberFormat('ru-RU');
    var decimal = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    var money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
    var preciseMoney = new Intl.NumberFormat('ru-RU', {
        style: 'currency', currency: 'RUB', minimumFractionDigits: 2, maximumFractionDigits: 2
    });
    var commentsKey = 'checkstock.unit-economics-1c.comments';
    var comments = readJson(commentsKey, {});
    var toastTimer = 0;
    var sendingPrice = false;
    var editedPriceKind = 'retail';
    var pendingPriceChange = null;
    var state = {
        query: '', store: 'all', status: 'all', page: 1, pageSize: 20,
        selected: null, sortColumn: 11, sortDirection: -1, tableFilters: {}
    };
    var nodes = {
        table: id('ue1c-table'), rows: id('ue1c-product-rows'), empty: id('ue1c-empty'), search: id('ue1c-search'),
        store: id('ue1c-store-filter'), tableWrap: id('ue1c-table-wrap'), pageSize: id('ue1c-page-size'),
        pagePrev: id('ue1c-page-prev'), pageNext: id('ue1c-page-next'), pageNumbers: id('ue1c-page-numbers'),
        summary: id('ue1c-pagination-summary'), refresh: id('ue1c-refresh'), overlay: id('ue1c-overlay'),
        detail: id('ue1c-detail'), detailClose: id('ue1c-detail-close'), drawerThumb: id('ue1c-drawer-thumb'),
        drawerTitle: id('ue1c-drawer-title'), drawerMeta: id('ue1c-drawer-meta'), priceInput: id('ue1c-price-input'),
        sppPriceInput: id('ue1c-spp-price-input'), walletPriceInput: id('ue1c-wallet-price-input'),
        calculatorReset: id('ue1c-calculator-reset'), calculatorMode: id('ue1c-calculator-mode'),
        calculatorFields: id('ue1c-calculator-inputs'),
        calculatorInputs: root.querySelectorAll('[data-calculator-input]'),
        breakEven: id('ue1c-break-even'), saveState: id('ue1c-save-state'), savePrice: id('ue1c-save-price'),
        priceMetrics: id('ue1c-price-metrics'), parameters: id('ue1c-parameter-groups'),
        secondaryTaxLabel: id('ue1c-secondary-tax-label'),
        chart: id('ue1c-chart'), chartWrap: id('ue1c-chart-wrap'), chartTooltip: id('ue1c-chart-tooltip'),
        confirmModal: id('ue1c-price-confirm-modal'), confirmClose: id('ue1c-price-confirm-close'),
        confirmCancel: id('ue1c-price-confirm-cancel'), confirmSend: id('ue1c-price-confirm-send'),
        confirmProduct: id('ue1c-price-confirm-product'), confirmTarget: id('ue1c-price-confirm-target'),
        confirmGrid: id('ue1c-price-confirm-grid'), confirmWarning: id('ue1c-price-confirm-warning'),
        toast: id('ue1c-toast')
    };

    function id(value) { return document.getElementById(value); }
    function readJson(key, fallback) {
        try {
            var parsed = JSON.parse(window.localStorage.getItem(key));
            return parsed && typeof parsed === 'object' ? parsed : fallback;
        } catch (error) { return fallback; }
    }
    function writeJson(key, value) {
        try { window.localStorage.setItem(key, JSON.stringify(value)); return true; }
        catch (error) { return false; }
    }
    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function finite(value, fallback) {
        if (value === null || value === undefined || value === '') return fallback;
        var parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }
    function pluralProducts(value) {
        var mod10 = value % 10;
        var mod100 = value % 100;
        if (mod10 === 1 && mod100 !== 11) return 'товар';
        if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'товара';
        return 'товаров';
    }
    function commentText(productId) {
        var saved = comments[productId];
        if (typeof saved === 'string') return saved;
        if (!saved || typeof saved !== 'object') return '';
        return [saved.first, saved.second].filter(Boolean).join('\n');
    }
    function setCommentText(productId, value) {
        var normalized = String(value || '').replace(/\r/g, '').trim();
        if (!normalized) { delete comments[productId]; return; }
        var lines = normalized.split('\n');
        comments[productId] = { first: String(lines.shift() || '').trim(), second: lines.join('\n').trim() };
    }
    function calculateSppPercent(product) {
        var price = product.price || {};
        var withoutSpp = finite(price.current, null);
        var withSpp = finite(price.with_spp, null);
        if (withoutSpp === null || withoutSpp <= 0 || withSpp === null) return null;
        return (withoutSpp - withSpp) / withoutSpp * 100;
    }
    function nullText(value) {
        return value === null || value === undefined || value === '' ? '—' : String(value);
    }
    function moneyOrNull(value) {
        var parsed = finite(value, null);
        return parsed === null ? '—' : money.format(parsed);
    }
    function tagData(product) {
        if (product.tag_data) return product.tag_data;
        var match = String(product.tag || '').match(
            /([^/]+)\/([^/]+)\/([^/]+)\/([^|]+)\|([^/]+)\/\s*Ф:([^/]+)\/\s*П:(.+)/
        );
        return match ? {
            goal_week: match[1].trim(), goal_day: match[2].trim(), status: match[3].trim(),
            ends: match[4].trim(), code: match[5].trim(), fact: match[6].trim(), plan: match[7].trim()
        } : { goal_week: null, goal_day: null, status: null, ends: null, code: null, fact: null, plan: null };
    }
    function mediaHtml(product, className) {
        var initial = (product.name || product.article || '?').slice(0, 1).toUpperCase();
        var image = product.image_url && /^https?:\/\//.test(product.image_url)
            ? '<img src="' + escapeHtml(product.image_url) + '" alt="" loading="lazy" decoding="async">' : '';
        return '<span class="' + className + '" aria-hidden="true"><span>'
            + escapeHtml(initial) + '</span>' + image + '</span>';
    }
    function copyValue(label, value) {
        if (value === null || value === undefined || value === '') {
            return '<span>' + escapeHtml(label) + ' —</span>';
        }
        return '<button class="ue1c-copy-value" type="button" data-copy-value="' + escapeHtml(value)
            + '" data-copy-tooltip="Нажмите, чтобы скопировать">' + escapeHtml(label)
            + ' <strong>' + escapeHtml(value) + '</strong></button>';
    }
    function productState(product) {
        var stockTotal = finite(product.stock.total, null);
        var drr = finite(product.advertising.drr, null);
        if (stockTotal !== null && stockTotal < 80) return 'low';
        if ((drr !== null && drr >= 18) || String(tagData(product).status || '').toUpperCase() === 'LOW') {
            return 'risk';
        }
        return 'ok';
    }
    function productSearchValue(product) {
        var tag = tagData(product);
        return [product.name, product.article, product.barcode, product.store_name, commentText(product.id),
            tag.goal_week, tag.goal_day, tag.status, tag.ends, tag.code, tag.fact, tag.plan,
            product.advertising.drr, product.advertising.spend,
            product.economics_7d && product.economics_7d.turnover,
            product.economics_7d && product.economics_7d.margin,
            product.economics_7d && product.economics_7d.roi, product.stock.total, product.stock.fbs,
            product.stock.fbo, product.stock.fulfillment, product.stock.days].join(' ').toLocaleLowerCase('ru-RU');
    }
    function columnValue(product, columnIndex) {
        var tag = tagData(product);
        var values = [
            [product.name, product.article, product.barcode, product.store_name].join(' · '),
            commentText(product.id), tag.goal_week, tag.goal_day, tag.status, tag.ends, tag.code,
            tag.fact, tag.plan, product.advertising.drr, product.advertising.spend,
            product.economics_7d && product.economics_7d.turnover,
            product.economics_7d && product.economics_7d.margin,
            product.economics_7d && product.economics_7d.roi,
            product.stock.total, product.stock.fbs, product.stock.fbo,
            product.stock.fulfillment, product.stock.days
        ];
        var value = values[Number(columnIndex)];
        return String(value === null || value === undefined ? '—' : value);
    }
    function renderStores() {
        stores.forEach(function (store) {
            var option = document.createElement('option');
            option.value = store.slug;
            option.textContent = store.name;
            nodes.store.appendChild(option);
        });
    }
    function renderProduct(product) {
        var tag = tagData(product);
        var rowClasses = [];
        if (state.selected === product.id) rowClasses.push('is-selected');
        var status = tag.status ? String(tag.status).toLowerCase() : 'unknown';
        var drr = finite(product.advertising.drr, null);
        var drrClass = drr !== null && drr >= 18 ? ' is-high' : (drr !== null && drr >= 12 ? ' is-medium' : '');
        var advertisingTitle = 'Период ' + nullText(product.advertising.period_from)
            + ' — ' + nullText(product.advertising.period_to) + ' · заказали на '
            + nullable(product.advertising.orders_amount, preciseMoney);
        var economics = product.economics_7d || {};
        var economicsTitle = 'Период ' + nullText(economics.period_from) + ' — '
            + nullText(economics.period_to) + ' · ТО по всем заказам';
        var stock = product.stock || {};
        var stockTitle = 'Заказы за ' + integer.format(finite(stock.period_days, 21)) + ' дн.: '
            + integer.format(finite(stock.orders_21d, 0)) + ' · среднесуточно: '
            + decimal.format(finite(stock.average_daily_orders, 0));
        return '<tr data-product-id="' + escapeHtml(product.id) + '"'
            + (rowClasses.length ? ' class="' + rowClasses.join(' ') + '"' : '') + '>'
            + '<td><div class="ue1c-product">' + mediaHtml(product, 'ue1c-product-thumb')
            + '<div><button class="ue1c-product-name" type="button" data-product-open="' + escapeHtml(product.id)
            + '" title="Открыть карточку товара">' + escapeHtml(product.name)
            + '</button><div class="ue1c-product-meta">' + copyValue('Арт.', product.article)
            + copyValue('Баркод', product.barcode) + '<span>' + escapeHtml(product.store_name) + '</span>'
            + '<span title="Рейтинг товара">★ ' + escapeHtml(nullText(product.rating)) + '</span>'
            + '</div></div></div></td>'
            + '<td class="ue1c-col-comments"><textarea class="ue1c-comment-input" data-comment-id="'
            + escapeHtml(product.id) + '" maxlength="480" placeholder="Добавить комментарий…"'
            + ' aria-label="Комментарий к товару ' + escapeHtml(product.name) + '">'
            + escapeHtml(commentText(product.id)) + '</textarea></td>'
            + '<td class="ue1c-col-tag ue1c-group-start ue1c-num"><strong>' + escapeHtml(nullText(tag.goal_week)) + '</strong></td>'
            + '<td class="ue1c-col-tag ue1c-num">' + escapeHtml(nullText(tag.goal_day)) + '</td>'
            + '<td class="ue1c-col-tag"><span class="ue1c-tag-status is-' + escapeHtml(status) + '">'
            + escapeHtml(nullText(tag.status)) + '</span></td><td class="ue1c-col-tag">' + escapeHtml(nullText(tag.ends)) + '</td>'
            + '<td class="ue1c-col-tag"><span class="ue1c-code-pill">' + escapeHtml(nullText(tag.code)) + '</span></td>'
            + '<td class="ue1c-col-tag ue1c-num">' + escapeHtml(nullText(tag.fact)) + '</td>'
            + '<td class="ue1c-col-tag ue1c-num">' + escapeHtml(nullText(tag.plan)) + '</td>'
            + '<td class="ue1c-num ue1c-group-start"><span class="ue1c-drr' + drrClass + '" title="'
            + escapeHtml(advertisingTitle) + '">' + (drr === null ? '—' : decimal.format(drr) + '%')
            + '</span></td><td class="ue1c-num"><strong title="' + escapeHtml(advertisingTitle) + '">'
            + nullable(product.advertising.spend, money) + '</strong></td>'
            + '<td class="ue1c-num ue1c-group-start"><strong title="' + escapeHtml(economicsTitle) + '">'
            + nullable(economics.turnover, money) + '</strong></td>'
            + '<td class="ue1c-num"><strong title="' + escapeHtml(economicsTitle) + '">'
            + nullable(economics.margin, money) + '</strong></td>'
            + '<td class="ue1c-num"><strong title="' + escapeHtml(economicsTitle) + '">'
            + nullable(economics.roi, decimal, '%') + '</strong></td>'
            + '<td class="ue1c-num ue1c-group-start"><strong>' + nullable(product.stock.total, integer) + '</strong></td>'
            + '<td class="ue1c-num"><span class="ue1c-stock-channel is-fbs">' + nullable(product.stock.fbs, integer)
            + '</span></td><td class="ue1c-num"><span class="ue1c-stock-channel is-fbo">'
            + nullable(product.stock.fbo, integer) + '</span></td><td class="ue1c-num"><span class="ue1c-stock-channel">'
            + nullable(product.stock.fulfillment, integer) + '</span></td><td class="ue1c-num"><strong>'
            + '<span title="' + escapeHtml(stockTitle) + '">' + nullable(product.stock.days, integer)
            + '</span></strong></td></tr>';
    }
    function externallyFilteredProducts() {
        var query = state.query.trim().toLocaleLowerCase('ru-RU');
        return products.filter(function (product) {
            return (state.store === 'all' || product.store_slug === state.store)
                && (state.status === 'all' || productState(product) === state.status)
                && (!query || productSearchValue(product).indexOf(query) !== -1);
        });
    }
    function filteredProducts() {
        var activeColumns = Object.keys(state.tableFilters);
        var result = externallyFilteredProducts().filter(function (product) {
            return activeColumns.every(function (columnIndex) {
                return state.tableFilters[columnIndex].has(columnValue(product, columnIndex));
            });
        });
        result.sort(function (left, right) {
            var leftValue = columnValue(left, state.sortColumn);
            var rightValue = columnValue(right, state.sortColumn);
            var leftNumber = finite(leftValue, null);
            var rightNumber = finite(rightValue, null);
            if (leftNumber !== null && rightNumber !== null) return (leftNumber - rightNumber) * state.sortDirection;
            return String(leftValue || '').localeCompare(String(rightValue || ''), 'ru') * state.sortDirection;
        });
        return result;
    }
    function tableFilterValues(columnIndex) {
        return externallyFilteredProducts().map(function (product) {
            return columnValue(product, columnIndex);
        });
    }
    function applyTableFilters(filters) {
        state.tableFilters = filters || {};
        resetPageAndRender();
    }
    function applyTableSort(columnIndex, direction) {
        state.sortColumn = Number(columnIndex);
        state.sortDirection = direction === 'desc' ? -1 : 1;
        resetPageAndRender();
    }
    function paginationItems(totalPages) {
        if (totalPages <= 7) return Array.from({ length: totalPages }, function (_, index) { return index + 1; });
        var items = [1];
        var start = Math.max(2, state.page - 1);
        var end = Math.min(totalPages - 1, state.page + 1);
        if (state.page <= 4) end = 5;
        if (state.page >= totalPages - 3) start = totalPages - 4;
        if (start > 2) items.push('gap');
        for (var page = start; page <= end; page += 1) items.push(page);
        if (end < totalPages - 1) items.push('gap');
        items.push(totalPages);
        return items;
    }
    function renderPagination(total) {
        var totalPages = Math.max(1, Math.ceil(total / state.pageSize));
        state.page = Math.min(Math.max(1, state.page), totalPages);
        var first = total ? (state.page - 1) * state.pageSize + 1 : 0;
        var last = total ? Math.min(state.page * state.pageSize, total) : 0;
        nodes.summary.textContent = total ? 'Показано ' + integer.format(first) + '–' + integer.format(last)
            + ' из ' + integer.format(total) + ' ' + pluralProducts(total) : 'Показано 0 товаров';
        nodes.pagePrev.disabled = !total || state.page <= 1;
        nodes.pageNext.disabled = !total || state.page >= totalPages;
        nodes.pageNumbers.innerHTML = total ? paginationItems(totalPages).map(function (item) {
            if (typeof item !== 'number') return '<span class="ue1c-page-gap" aria-hidden="true">…</span>';
            return '<button class="ue1c-page-button' + (item === state.page ? ' is-current' : '')
                + '" type="button" data-page="' + item + '"' + (item === state.page ? ' aria-current="page"' : '')
                + '>' + item + '</button>';
        }).join('') : '';
    }
    function renderPage() {
        var filtered = filteredProducts();
        state.page = Math.min(Math.max(1, state.page), Math.max(1, Math.ceil(filtered.length / state.pageSize)));
        var offset = (state.page - 1) * state.pageSize;
        nodes.rows.innerHTML = filtered.slice(offset, offset + state.pageSize).map(renderProduct).join('');
        Array.prototype.forEach.call(nodes.rows.querySelectorAll('.ue1c-product-thumb img'), function (image) {
            image.addEventListener('error', function () { image.hidden = true; }, { once: true });
        });
        nodes.empty.hidden = filtered.length > 0;
        renderPagination(filtered.length);
        nodes.tableWrap.scrollTop = 0;
    }
    function resetPageAndRender() { state.page = 1; renderPage(); }
    function showToast(message) {
        window.clearTimeout(toastTimer);
        nodes.toast.textContent = message;
        nodes.toast.classList.add('is-visible');
        toastTimer = window.setTimeout(function () { nodes.toast.classList.remove('is-visible'); }, 1800);
    }
    async function copyText(button) {
        var value = button.dataset.copyValue || '';
        var copied = false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(value);
                copied = true;
            }
        } catch (error) { copied = false; }
        if (!copied) {
            var helper = document.createElement('textarea');
            helper.value = value;
            helper.style.position = 'fixed';
            helper.style.opacity = '0';
            document.body.appendChild(helper);
            helper.select();
            try { copied = document.execCommand('copy'); } catch (error) { copied = false; }
            helper.remove();
        }
        button.dataset.copyTooltip = copied ? 'Скопировано' : 'Не удалось скопировать';
        window.setTimeout(function () { button.dataset.copyTooltip = 'Нажмите, чтобы скопировать'; }, 1300);
    }

    function databaseCalculatorValues(product) {
        var details = product.details || {};
        var saved = product.product_settings || {};
        var retail = finite(product.price && product.price.current, null);
        var client = finite(product.price && product.price.with_spp, retail);
        var wallet = finite(product.price && product.price.with_wallet, null);
        var commissionPercent = finite(details.commission_percent, null);
        var acquiringPercent = finite(details.acquiring, null);
        var teamPercent = finite(details.team_commission_percent, null);
        var storageRate = finite(saved.storage_wb_rub, null);
        var storageDays = finite(details.storage_days, null);
        var vatPercent = finite(details.vat_percent, null);
        var taxSystem = productTaxSystem(product);
        var walletPercent = walletDiscountPercent(product);
        if (walletPercent === null && client !== null && client > 0 && wallet !== null) {
            walletPercent = Math.max(0, (client - wallet) / client * 100);
        }
        var activeTaxValue = taxSystem === 'osno'
            ? finite(details.osno_value, null) : finite(details.usn_value, null);
        return {
            retail: retail,
            client: client,
            wallet: wallet,
            spp: retail !== null && retail > 0 && client !== null
                ? (retail - client) / retail * 100 : null,
            walletPercent: walletPercent,
            commission: commissionPercent,
            commissionRub: finite(details.commission_value,
                retail === null || commissionPercent === null ? null : retail * commissionPercent / 100),
            drr: finite(product.advertising && product.advertising.drr, null),
            advertisingRub: finite(product.advertising && product.advertising.spend, null),
            logistics: finite(details.delivery_with_returns, finite(details.logistics, null)),
            storage: storageRate,
            storageTotal: finite(details.storage_sum,
                storageRate === null || storageDays === null ? null : storageRate * storageDays),
            acquiringPercent: acquiringPercent,
            acquiringRub: retail === null || acquiringPercent === null
                ? null : retail * acquiringPercent / 100,
            purchase: finite(details.purchase_cost, null),
            team: teamPercent,
            teamRub: retail === null || teamPercent === null ? null : retail * teamPercent / 100,
            fulfillment: finite(details.fulfillment_cost, null),
            vat: vatPercent,
            vatRub: finite(details.vat_value,
                client === null || vatPercent === null ? null : client * vatPercent / (100 + vatPercent)),
            usn: finite(details.usn_percent, null),
            osno: finite(details.osno_percent, null),
            secondaryTaxRub: activeTaxValue
        };
    }
    function sppPriceFactor(product) {
        var withoutSpp = finite(product.price && product.price.current, null);
        var withSpp = finite(product.price && product.price.with_spp, withoutSpp);
        return withoutSpp !== null && withoutSpp > 0 && withSpp !== null
            ? Math.max(0, withSpp) / withoutSpp
            : 1;
    }
    function walletPriceFactor(product) {
        var withSpp = finite(product.price && product.price.with_spp, null);
        var withWallet = finite(product.price && product.price.with_wallet, null);
        return withSpp !== null && withSpp > 0 && withWallet !== null && withWallet > 0
            ? withWallet / withSpp
            : null;
    }
    function walletDiscountPercent(product) {
        var withSpp = finite(product.price && product.price.with_spp, null);
        var withWallet = finite(product.price && product.price.with_wallet, null);
        if (withSpp === null || withSpp <= 0 || withWallet === null || withWallet <= 0) return null;
        for (var percent = 0; percent < 100; percent += 1) {
            if (withSpp - Math.ceil(withSpp * percent / 100) === Math.round(withWallet)) return percent;
        }
        return null;
    }
    function walletPriceFromClient(product, client) {
        var percent = walletDiscountPercent(product);
        var factor = walletPriceFactor(product);
        if (percent !== null) return Math.round(client) - Math.ceil(Math.round(client) * percent / 100);
        return factor === null ? null : roundedRubles(client * factor);
    }
    function clientPriceFromWallet(product, wallet) {
        var percent = walletDiscountPercent(product);
        var factor = walletPriceFactor(product);
        if (percent === null) {
            return factor === null ? null : roundedRubles(wallet / Math.max(factor, 0.0001));
        }
        var estimate = Math.round(wallet / Math.max(1 - percent / 100, 0.0001));
        var matches = [];
        for (var candidate = Math.max(1, estimate - 8); candidate <= estimate + 8; candidate += 1) {
            if (candidate - Math.ceil(candidate * percent / 100) === Math.round(wallet)) matches.push(candidate);
        }
        return matches.length ? matches.reduce(function (best, candidate) {
            return Math.abs(candidate - estimate) < Math.abs(best - estimate) ? candidate : best;
        }, matches[0]) : estimate;
    }
    function roundedRubles(value) { return Math.round(Math.max(0, value)); }
    function precisePrice(value) { return Math.round(Math.max(0, value) * 100) / 100; }
    function syncLinkedPriceInputs(product, source) {
        var retail = finite(nodes.priceInput.value, null);
        var client = finite(nodes.sppPriceInput.value, null);
        var wallet = finite(nodes.walletPriceInput.value, null);
        var sppFactor = Math.max(sppPriceFactor(product), 0.0001);
        var walletFactor = walletPriceFactor(product);
        if (source === 'retail' && retail !== null) {
            client = roundedRubles(retail * sppFactor);
            wallet = walletPriceFromClient(product, client);
        } else if (source === 'client' && client !== null) {
            retail = precisePrice(client / sppFactor);
            wallet = walletPriceFromClient(product, client);
        } else if (source === 'wallet' && wallet !== null && walletFactor !== null) {
            client = clientPriceFromWallet(product, wallet);
            retail = precisePrice(client / sppFactor);
        }
        if (source !== 'retail') nodes.priceInput.value = retail === null ? '' : String(retail);
        if (source !== 'client') nodes.sppPriceInput.value = client === null ? '' : String(client);
        if (source !== 'wallet') nodes.walletPriceInput.value = wallet === null ? '' : String(wallet);
    }
    function fillCalculator(product) {
        var values = databaseCalculatorValues(product);
        Array.prototype.forEach.call(nodes.calculatorInputs, function (input) {
            var value = values[input.dataset.calculatorInput];
            input.value = value === null ? '' : String(Math.round(Number(value) * 100) / 100);
        });
    }
    function productTaxSystem(product) {
        return product.store_slug === 'gogol'
            && product.details && product.details.tax_system === 'osno' ? 'osno' : 'usn';
    }
    function syncTaxCalculatorLabel(product) {
        nodes.secondaryTaxLabel.textContent = productTaxSystem(product) === 'osno'
            ? 'Налог ОСНО, руб' : 'Налог УСН, руб';
    }
    function calculatorValues() {
        var values = {};
        Array.prototype.forEach.call(nodes.calculatorInputs, function (input) {
            values[input.dataset.calculatorInput] = finite(input.value, null);
        });
        return values;
    }
    function calculatorInput(key) {
        return root.querySelector('[data-calculator-input="' + key + '"]');
    }
    function setCalculatorValue(key, value) {
        var input = calculatorInput(key);
        if (!input) return;
        input.value = value === null || value === undefined || !Number.isFinite(Number(value))
            ? '' : String(Math.round(Number(value) * 100) / 100);
    }
    function walletPriceWithPercent(clientPrice, percent) {
        if (clientPrice === null || percent === null) return null;
        var roundedClient = Math.round(Math.max(0, clientPrice));
        return roundedClient - Math.ceil(roundedClient * Math.max(0, percent) / 100);
    }
    function clientPriceWithWalletPercent(walletPrice, percent) {
        if (walletPrice === null || percent === null) return null;
        var estimate = Math.round(walletPrice / Math.max(0.0001, 1 - percent / 100));
        var matches = [];
        for (var candidate = Math.max(1, estimate - 10); candidate <= estimate + 10; candidate += 1) {
            if (walletPriceWithPercent(candidate, percent) === Math.round(walletPrice)) matches.push(candidate);
        }
        return matches.length ? matches.reduce(function (best, candidate) {
            return Math.abs(candidate - estimate) < Math.abs(best - estimate) ? candidate : best;
        }, matches[0]) : estimate;
    }
    function syncPriceDependentAmounts(product) {
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        var client = finite(values.client, null);
        var commissionPercent = finite(values.commission, null);
        var acquiringPercent = finite(values.acquiringPercent, null);
        var teamPercent = finite(values.team, null);
        setCalculatorValue('commissionRub', retail === null || commissionPercent === null
            ? null : retail * commissionPercent / 100);
        setCalculatorValue('acquiringRub', retail === null || acquiringPercent === null
            ? null : retail * acquiringPercent / 100);
        setCalculatorValue('teamRub', retail === null || teamPercent === null
            ? null : retail * teamPercent / 100);

        var vatPercent = finite(values.vat, null);
        var vat = client === null || vatPercent === null
            ? null : client * vatPercent / (100 + vatPercent);
        setCalculatorValue('vatRub', vat);
        var taxSystem = productTaxSystem(product);
        var activeTaxPercent = finite(taxSystem === 'osno' ? values.osno : values.usn, null);
        var secondaryTax = client === null || vat === null || activeTaxPercent === null
            ? null : taxSystem === 'osno'
                ? client * activeTaxPercent / 100
                : (client - vat) * activeTaxPercent / 100;
        setCalculatorValue('secondaryTaxRub', secondaryTax);
    }
    function syncDetailedPriceInputs(product, source) {
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        var client = finite(values.client, null);
        var wallet = finite(values.wallet, null);
        var spp = finite(values.spp, null);
        var walletPercent = finite(values.walletPercent, null);
        if ((source === 'retail' || source === 'spp') && retail !== null && spp !== null) {
            client = roundedRubles(retail * Math.max(0, 1 - spp / 100));
            wallet = walletPriceWithPercent(client, walletPercent);
            setCalculatorValue('client', client);
            setCalculatorValue('wallet', wallet);
        } else if (source === 'client' && client !== null && spp !== null) {
            retail = precisePrice(client / Math.max(0.0001, 1 - spp / 100));
            wallet = walletPriceWithPercent(client, walletPercent);
            setCalculatorValue('retail', retail);
            setCalculatorValue('wallet', wallet);
        } else if (source === 'walletPercent' && client !== null && walletPercent !== null) {
            setCalculatorValue('wallet', walletPriceWithPercent(client, walletPercent));
        } else if (source === 'wallet' && wallet !== null && walletPercent !== null && spp !== null) {
            client = clientPriceWithWalletPercent(wallet, walletPercent);
            retail = precisePrice(client / Math.max(0.0001, 1 - spp / 100));
            setCalculatorValue('client', client);
            setCalculatorValue('retail', retail);
        }
        if (source === 'retail' || source === 'spp' || source === 'client' || source === 'wallet') {
            syncPriceDependentAmounts(product);
        }
    }
    function syncDetailedCalculatorInputs(product, source) {
        if (['retail', 'spp', 'client', 'walletPercent', 'wallet'].indexOf(source) !== -1) {
            syncDetailedPriceInputs(product, source);
        }
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        function syncPair(percentKey, rubKey, changedKey, base) {
            var percent = finite(values[percentKey], null);
            var rubles = finite(values[rubKey], null);
            if (changedKey === percentKey) {
                setCalculatorValue(rubKey, base === null || percent === null ? null : base * percent / 100);
            } else if (changedKey === rubKey) {
                setCalculatorValue(percentKey, base === null || base <= 0 || rubles === null
                    ? null : rubles / base * 100);
            }
        }
        syncPair('commission', 'commissionRub', source, retail);
        syncPair('acquiringPercent', 'acquiringRub', source, retail);
        syncPair('team', 'teamRub', source, retail);

        var advertisingRub = finite(values.advertisingRub, null);
        var drr = finite(values.drr, null);
        var ordersAmount = finite(product.advertising && product.advertising.orders_amount, null);
        var advertisingBase = ordersAmount !== null && ordersAmount > 0 ? ordersAmount : retail;
        if (source === 'drr') {
            setCalculatorValue('advertisingRub', advertisingBase === null || drr === null
                ? null : advertisingBase * drr / 100);
        } else if (source === 'advertisingRub') {
            setCalculatorValue('drr', advertisingBase === null || advertisingBase <= 0 || advertisingRub === null
                ? null : advertisingRub / advertisingBase * 100);
        }
    }
    function syncCompactCalculatorInputs(product) {
        var values = calculatorValues();
        var retail = finite(values.retail, null);
        var client = finite(values.client, null);
        setCalculatorValue('spp', retail !== null && retail > 0 && client !== null
            ? (retail - client) / retail * 100 : null);
        syncPriceDependentAmounts(product);
    }
    function calculatePrice(product, simulation) {
        var details = product.details || {};
        var values = simulation || databaseCalculatorValues(product);
        var currentValue = finite(values.retail, null);
        var current = currentValue === null ? null : Math.max(0, currentValue);
        var clientValue = finite(values.client, current);
        var clientPrice = clientValue === null ? null : Math.max(0, clientValue);
        var walletValue = finite(values.wallet, clientPrice);
        var walletPrice = walletValue === null ? null : Math.max(0, walletValue);
        var commissionPercent = finite(values.commission, null);
        var teamCommissionPercent = finite(values.team, null);
        var vatPercent = finite(values.vat, null);
        var usnPercent = finite(values.usn, null);
        var osnoPercent = finite(values.osno, null);
        var taxSystem = productTaxSystem(product);
        var acquiringPercent = finite(values.acquiringPercent, finite(details.acquiring, null));
        var drrPercent = finite(values.drr, null);
        var acquiring = finite(values.acquiringRub, current === null || acquiringPercent === null
            ? null : current * acquiringPercent / 100);
        var commission = finite(values.commissionRub, current === null || commissionPercent === null
            ? null : current * commissionPercent / 100);
        var advertising = current === null || drrPercent === null
            ? null : current * drrPercent / 100;
        var teamCommission = finite(values.teamRub, current === null || teamCommissionPercent === null
            ? null : current * teamCommissionPercent / 100);
        var calculatedVat = clientPrice === null || vatPercent === null
            ? null : clientPrice * vatPercent / (100 + vatPercent);
        var vat = finite(values.vatRub, calculatedVat);
        var activeTaxPercent = taxSystem === 'osno' ? osnoPercent : usnPercent;
        var calculatedSecondaryTax = clientPrice === null || vat === null || activeTaxPercent === null
            ? null
            : taxSystem === 'osno'
                ? clientPrice * activeTaxPercent / 100
                : (clientPrice - vat) * activeTaxPercent / 100;
        var secondaryTax = finite(values.secondaryTaxRub, calculatedSecondaryTax);
        var tax = vat === null || secondaryTax === null ? null : vat + secondaryTax;
        var purchase = finite(values.purchase, finite(details.purchase_cost, null));
        var fulfillment = finite(values.fulfillment, null);
        var logistics = finite(values.logistics, null);
        var storageRate = finite(values.storage, null);
        var turnoverDays = finite(details.storage_days, null);
        var storage = finite(values.storageTotal,
            storageRate === null || turnoverDays === null ? null : storageRate * turnoverDays);
        var revenueComplete = [current, acquiring, logistics, storage, commission, advertising].every(function (value) {
            return value !== null;
        });
        var netRevenue = revenueComplete
            ? current - acquiring - logistics - storage - commission - advertising
            : null;
        var profitComplete = [netRevenue, purchase, fulfillment, teamCommission, tax].every(function (value) {
            return value !== null;
        });
        var margin = profitComplete
            ? netRevenue - purchase - fulfillment - teamCommission - tax
            : null;
        return {
            current: current, sppPrice: clientPrice, walletPrice: walletPrice, netRevenue: netRevenue,
            purchase: purchase, fulfillment: fulfillment, acquiring: acquiring,
            commission: commission, teamCommission: teamCommission, logistics: logistics,
            storage: storage, vat: vat, secondaryTax: secondaryTax,
            secondaryTaxLabel: taxSystem === 'osno' ? 'ОСНО' : 'УСН',
            tax: tax, advertising: advertising, margin: margin,
            roi: margin !== null && purchase !== null && purchase > 0 ? margin / purchase * 100 : null
        };
    }
    function metric(label, value, className) {
        return '<div><span>' + escapeHtml(label) + '</span><strong class="' + (className || '') + '">'
            + escapeHtml(value) + '</strong></div>';
    }
    function renderPriceCalculation(product) {
        var values = calculatorValues();
        var metrics = calculatePrice(product, values);
        nodes.secondaryTaxLabel.textContent = 'Налог ' + metrics.secondaryTaxLabel + ', руб';
        var marginClass = metrics.margin === null ? '' : metrics.margin < 0 ? 'is-negative' : 'is-positive';
        nodes.priceMetrics.innerHTML = metric('Чистая прибыль', nullable(metrics.margin, preciseMoney), marginClass)
            + metric('ROI', nullable(metrics.roi, decimal, '%'), metrics.roi === null ? '' : metrics.roi < 0 ? 'is-negative' : 'is-positive');
    }
    function parameter(label, value) {
        return '<div class="ue1c-parameter"><span>' + escapeHtml(label) + '</span><strong title="'
            + escapeHtml(value) + '">' + escapeHtml(value) + '</strong></div>';
    }
    function nullable(value, formatter, suffix) {
        if (value === null || value === undefined || value === '') return '—';
        return formatter.format(value) + (suffix || '');
    }
    function editableParameter(label, key, value, suffix, maximum) {
        return '<label class="ue1c-parameter ue1c-parameter--editable"><span>' + escapeHtml(label)
            + '</span><span class="ue1c-parameter-input"><input type="number" min="0" step="0.01"'
            + (maximum ? ' max="' + maximum + '"' : '') + ' data-product-setting="' + escapeHtml(key)
            + '" value="' + escapeHtml(finite(value, 0)) + '"' + (canEdit ? '' : ' disabled') + '>'
            + (suffix ? '<b>' + escapeHtml(suffix) + '</b>' : '') + '</span></label>';
    }
    function renderParameters(product) {
        var item = product.details || {};
        var saved = product.product_settings || {};
        var groups = [
            ['Товар', [
                parameter('Предмет', item.subject === null ? '—' : item.subject),
                parameter('Артикул', product.article), parameter('Баркод', product.barcode),
                parameter('Магазин', product.store_name),
                parameter('Закупочная цена', nullable(item.purchase_cost, preciseMoney)),
                parameter('Схема комиссии', item.commission_scheme === null ? '—' : item.commission_scheme)
            ]],
            ['Комиссии и налоги', [
                parameter('Комиссия СУ', nullable(item.subject_commission_percent, decimal, '%')),
                parameter('Доп. тарифы WB', nullable(item.wb_extra_tariff_percent, decimal, '%')),
                parameter('Процент WB', nullable(item.commission_percent, decimal, '%')),
                parameter('Налоговая система', item.tax_system === 'osno' ? 'ОСНО' : 'УСН'),
                parameter('НДС', nullable(item.vat_percent, decimal, '%')),
                parameter(item.tax_system === 'osno' ? 'ОСНО' : 'УСН', nullable(
                    item.tax_system === 'osno' ? item.osno_percent : item.usn_percent, decimal, '%')),
                parameter('Эквайринг', nullable(item.acquiring, decimal, '%')),
                parameter('Комиссия команды', nullable(item.team_commission_percent, decimal, '%'))
            ]],
            ['Продажи и реклама', [
                parameter('Выкуп факт за 7 дней', nullable(product.advertising.buyout_percent, decimal, '%')),
                editableParameter('Выкуп для логистики', 'buyout_percent', saved.buyout_percent, '%', 100),
                parameter('СПП', nullable(calculateSppPercent(product), decimal, '%')),
                parameter('ДРР', nullable(product.advertising.drr, decimal, '%')),
                parameter('Реклама факт', nullable(item.actual_advertising, preciseMoney))
            ]],
            ['Логистика', [
                parameter('Фулфилмент', nullable(item.fulfillment_cost, preciseMoney)),
                editableParameter('Доставка WB', 'delivery_wb_rub', saved.delivery_wb_rub, '₽'),
                editableParameter('За 1 возврат', 'return_cost_rub', saved.return_cost_rub, '₽'),
                editableParameter('Объём 1 товара', 'volume_l', saved.volume_l, 'л'),
                parameter('Стоимость платной приёмки', nullable(item.paid_acceptance_cost, preciseMoney)),
                parameter('Доставка с учётом возвратов', nullable(item.delivery_with_returns, preciseMoney))
            ]],
            ['Хранение', [
                editableParameter('Хранение WB', 'storage_wb_rub', saved.storage_wb_rub, '₽/шт.'),
                parameter('Дней', nullable(item.storage_days, integer)),
                parameter('Сумма', nullable(item.storage_sum, preciseMoney))
            ]]
        ];
        nodes.parameters.innerHTML = groups.map(function (group) {
            return '<section class="ue1c-parameter-group"><h4>' + escapeHtml(group[0])
                + '</h4><div class="ue1c-parameter-grid">' + group[1].join('') + '</div></section>';
        }).join('') + '<div class="ue1c-parameter-save"><span data-product-settings-state>'
            + (saved.updated_at ? 'Параметры сохранены' : 'Значения по умолчанию')
            + '</span><button type="button" data-save-product-settings' + (canEdit ? '' : ' disabled')
            + '>Сохранить параметры</button></div>';
    }
    function chartPath(points) {
        return points.map(function (point, index) {
            return (index ? 'L' : 'M') + point[0].toFixed(1) + ' ' + point[1].toFixed(1);
        }).join(' ');
    }
    function renderChart(product) {
        var history = Array.isArray(product.history) ? product.history : [];
        if (!history.length) { nodes.chart.innerHTML = ''; return; }
        var left = 42;
        var right = 398;
        var top = 16;
        var bottom = 174;
        var width = right - left;
        var height = bottom - top;
        var moneyValues = [];
        history.forEach(function (item) {
            var marginValue = finite(item.margin_rub, null);
            var advertisingValue = finite(item.advertising_rub, null);
            if (marginValue !== null) moneyValues.push(marginValue);
            if (advertisingValue !== null) moneyValues.push(advertisingValue);
        });
        var minimum = Math.min.apply(null, [0].concat(moneyValues));
        var maximum = Math.max.apply(null, [1].concat(moneyValues));
        var range = maximum - minimum || 1;
        var stockValues = history.map(function (item) { return finite(item.fbs_units, null); })
            .filter(function (value) { return value !== null; });
        var stockMaximum = Math.max.apply(null, [1].concat(stockValues));
        var drrValues = history.map(function (item) { return finite(item.drr_percent, null); })
            .filter(function (value) { return value !== null; });
        var drrObservedMaximum = Math.max.apply(null, [0].concat(drrValues));
        var drrMaximum = drrObservedMaximum <= 10 ? 10
            : drrObservedMaximum <= 25 ? 25
                : drrObservedMaximum <= 50 ? 50
                    : drrObservedMaximum <= 100 ? 100 : Math.ceil(drrObservedMaximum / 50) * 50;
        function x(index) { return history.length < 2 ? left : left + width * index / (history.length - 1); }
        function moneyY(value) { return bottom - (value - minimum) / range * height; }
        function stockY(value) { return bottom - value / stockMaximum * height * 0.75; }
        function drrY(value) { return bottom - value / drrMaximum * height; }
        var marginComplete = history.every(function (item) { return finite(item.margin_rub, null) !== null; });
        var adsComplete = history.every(function (item) { return finite(item.advertising_rub, null) !== null; });
        var drrComplete = history.every(function (item) { return finite(item.drr_percent, null) !== null; });
        var marginPoints = marginComplete ? history.map(function (item, index) {
            return [x(index), moneyY(finite(item.margin_rub, 0))];
        }) : [];
        var adsPoints = adsComplete ? history.map(function (item, index) {
            return [x(index), moneyY(finite(item.advertising_rub, 0))];
        }) : [];
        var drrPoints = drrComplete ? history.map(function (item, index) {
            return [x(index), drrY(finite(item.drr_percent, 0))];
        }) : [];
        var grid = [0, 1, 2, 3].map(function (index) {
            var y = top + height * index / 3;
            var value = maximum - range * index / 3;
            return '<line class="ue1c-chart-grid" x1="' + left + '" y1="' + y + '" x2="' + right
                + '" y2="' + y + '"></line><text class="ue1c-chart-y" x="2" y="' + (y + 3)
                 + '">' + escapeHtml(integer.format(Math.round(value))) + '</text>';
        }).join('');
        var drrAxis = [0, 0.5, 1].map(function (ratio) {
            var y = bottom - height * ratio;
            return '<text class="ue1c-chart-y ue1c-chart-y--drr" x="438" y="' + (y + 3)
                + '" text-anchor="end">' + escapeHtml(decimal.format(drrMaximum * ratio)) + '%</text>';
        }).join('');
        var bars = history.map(function (item, index) {
            var stockValue = finite(item.fbs_units, null);
            if (stockValue === null) return '';
            var y = stockY(stockValue);
            return '<rect class="ue1c-chart-bar" x="' + (x(index) - 11) + '" y="' + y
                + '" width="22" height="' + (bottom - y) + '" rx="3"></rect>';
        }).join('');
        var labels = history.map(function (item, index) {
            return '<text class="ue1c-chart-x" x="' + x(index) + '" y="204" text-anchor="middle">'
                + escapeHtml(item.label) + '</text>';
        }).join('');
        var hits = history.map(function (item, index) {
            var hitLeft = index === 0 ? left : (x(index - 1) + x(index)) / 2;
            var hitRight = index === history.length - 1 ? right : (x(index) + x(index + 1)) / 2;
            return '<rect class="ue1c-chart-hit" data-chart-index="' + index + '" x="' + hitLeft
                + '" y="' + top + '" width="' + (hitRight - hitLeft) + '" height="190" tabindex="0"></rect>';
        }).join('');
        nodes.chart.innerHTML = grid + drrAxis + '<line class="ue1c-chart-zero" x1="' + left + '" y1="'
            + moneyY(0) + '" x2="' + right + '" y2="' + moneyY(0) + '"></line>' + bars
            + (marginComplete ? '<path class="ue1c-chart-line is-margin" d="' + chartPath(marginPoints) + '"></path>' : '')
            + (adsComplete ? '<path class="ue1c-chart-line is-ads" d="' + chartPath(adsPoints) + '"></path>' : '')
            + (drrComplete ? '<path class="ue1c-chart-line is-drr" d="' + chartPath(drrPoints) + '"></path>' : '')
            + '<line class="ue1c-chart-cursor" data-chart-cursor x1="' + left + '" y1="' + top
            + '" x2="' + left + '" y2="' + bottom + '"></line>' + labels + hits;
        Array.prototype.forEach.call(nodes.chart.querySelectorAll('[data-chart-index]'), function (hit) {
            function show() { showChartTooltip(product, Number(hit.dataset.chartIndex), x); }
            hit.addEventListener('mouseenter', show);
            hit.addEventListener('focus', show);
            hit.addEventListener('blur', hideChartTooltip);
        });
    }
    function showChartTooltip(product, index, xFunction) {
        var item = product.history[index];
        if (!item) return;
        var x = xFunction(index);
        var cursor = nodes.chart.querySelector('[data-chart-cursor]');
        if (cursor) {
            cursor.setAttribute('x1', x);
            cursor.setAttribute('x2', x);
            cursor.classList.add('is-visible');
        }
        nodes.chartTooltip.innerHTML = '<strong>' + escapeHtml(item.label) + '</strong>'
            + '<span>Заказы / выкуп <b>' + nullable(item.orders_count, decimal, ' шт.') + ' / '
            + nullable(item.purchased_units, decimal, ' шт.') + ' ('
            + nullable(item.buyout_percent, decimal, '%') + ')</b></span>'
            + '<span>Реклама <b>' + nullable(item.advertising_rub, preciseMoney) + '</b></span>'
            + '<span>ДРР <b>' + nullable(item.drr_percent, decimal, '%') + '</b></span>'
            + '<span>Чистая прибыль <b>' + nullable(item.margin_rub, preciseMoney) + '</b></span>'
            + '<span>Остаток FBS <b>' + nullable(item.fbs_units, integer, ' шт.') + ' · '
            + nullable(item.purchase_value, preciseMoney) + '</b></span>';
        nodes.chartTooltip.hidden = false;
        nodes.chartTooltip.style.left = (x / 440 * 100) + '%';
        nodes.chartTooltip.style.top = '28px';
        nodes.chartTooltip.style.transform = x > 270 ? 'translateX(-100%)' : 'translateX(0)';
    }
    function hideChartTooltip() {
        nodes.chartTooltip.hidden = true;
        var cursor = nodes.chart.querySelector('[data-chart-cursor]');
        if (cursor) cursor.classList.remove('is-visible');
    }

    function setDetailTab(tabName) {
        Array.prototype.forEach.call(root.querySelectorAll('[data-detail-tab]'), function (button) {
            var active = button.dataset.detailTab === tabName;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
        });
        id('ue1c-panel-economics').hidden = tabName !== 'economics';
        id('ue1c-panel-params').hidden = tabName !== 'params';
    }
    function updateSaveState(product) {
        var labels = { retail: 'без СПП', client: 'с СПП', wallet: 'с WB Кошельком' };
        nodes.saveState.textContent = sendingPrice
            ? 'Отправляем цену и обновляем данные…'
            : 'Цель: цена ' + labels[editedPriceKind] + ' · отправка сразу в WB';
    }
    function renderDrawerMedia(product) {
        var initial = (product.name || product.article || '?').slice(0, 1).toUpperCase();
        nodes.drawerThumb.innerHTML = '<span>' + escapeHtml(initial) + '</span>'
            + (product.image_url && /^https?:\/\//.test(product.image_url)
                ? '<img src="' + escapeHtml(product.image_url) + '" alt="">' : '');
        var image = nodes.drawerThumb.querySelector('img');
        if (image) image.addEventListener('error', function () { image.hidden = true; }, { once: true });
    }
    function openDetail(product) {
        state.selected = product.id;
        editedPriceKind = 'retail';
        root.classList.add('has-detail');
        nodes.detail.classList.add('is-open');
        nodes.detail.setAttribute('aria-hidden', 'false');
        nodes.overlay.classList.add('is-open');
        renderDrawerMedia(product);
        nodes.drawerTitle.textContent = product.name;
        nodes.drawerMeta.textContent = product.store_name + ' · Арт. ' + product.article
            + ' · ★ ' + nullText(product.rating);
        syncTaxCalculatorLabel(product);
        fillCalculator(product);
        updateSaveState(product);
        renderPriceCalculation(product);
        renderParameters(product);
        renderChart(product);
        setDetailTab('economics');
        Array.prototype.forEach.call(nodes.rows.querySelectorAll('tr'), function (row) {
            row.classList.toggle('is-selected', row.dataset.productId === product.id);
        });
    }
    function closeDetail() {
        state.selected = null;
        root.classList.remove('has-detail');
        nodes.detail.classList.remove('is-open');
        nodes.detail.setAttribute('aria-hidden', 'true');
        nodes.overlay.classList.remove('is-open');
        hideChartTooltip();
        Array.prototype.forEach.call(nodes.rows.querySelectorAll('tr.is-selected'), function (row) {
            row.classList.remove('is-selected');
        });
    }
    function breakEvenPrice(product, values) {
        var item = product.details || {};
        var commissionPercent = finite(values.commission, null);
        var teamCommissionPercent = finite(values.team, null);
        var vatPercent = finite(values.vat, null);
        var usnPercent = finite(values.usn, null);
        var osnoPercent = finite(values.osno, null);
        var taxSystem = productTaxSystem(product);
        var activeTaxPercent = taxSystem === 'osno' ? osnoPercent : usnPercent;
        var acquiringPercent = finite(values.acquiringPercent, finite(item.acquiring, null));
        var drrPercent = finite(values.drr, null);
        var purchase = finite(values.purchase, finite(item.purchase_cost, null));
        var fulfillment = finite(values.fulfillment, null);
        var logistics = finite(values.logistics, null);
        var storageRate = finite(values.storage, null);
        var turnoverDays = finite(item.storage_days, null);
        var storage = finite(values.storageTotal,
            storageRate === null || turnoverDays === null ? null : storageRate * turnoverDays);
        if ([commissionPercent, teamCommissionPercent, vatPercent, activeTaxPercent,
            acquiringPercent, drrPercent,
            purchase, fulfillment, logistics, storage].some(function (value) {
            return value === null;
        })) return null;
        var retailValue = finite(values.retail, null);
        var clientValue = finite(values.client, retailValue);
        var walletValue = finite(values.wallet, clientValue);
        var clientPriceFactor = retailValue !== null && retailValue > 0 && clientValue !== null
            ? Math.max(clientValue, 0) / retailValue : sppPriceFactor(product);
        var walletPriceFactor = retailValue !== null && retailValue > 0 && walletValue !== null
            ? Math.max(walletValue, 0) / retailValue : clientPriceFactor;
        var vatFactor = vatPercent / (100 + vatPercent);
        var secondaryTaxFactor = taxSystem === 'osno'
            ? osnoPercent / 100
            : (1 - vatFactor) * usnPercent / 100;
        var variableFactor = 1 - (commissionPercent + drrPercent) / 100
            - acquiringPercent / 100
            - teamCommissionPercent / 100
            - clientPriceFactor * (vatFactor + secondaryTaxFactor);
        var fixed = purchase + fulfillment + logistics + storage;
        return {
            retail: Math.ceil(fixed / Math.max(0.01, variableFactor) / 10) * 10,
            clientPriceFactor: clientPriceFactor,
            walletPriceFactor: walletPriceFactor
        };
    }
    function priceKindLabel(kind) {
        return { retail: 'Цена без СПП', spp: 'Цена с СПП', wallet: 'Цена с WB Кошельком' }[kind] || 'Цена';
    }
    function calculatorTargetKind() {
        return editedPriceKind === 'client' ? 'spp' : editedPriceKind;
    }
    function calculatorTargetValue() {
        var input = editedPriceKind === 'client' ? nodes.sppPriceInput
            : editedPriceKind === 'wallet' ? nodes.walletPriceInput : nodes.priceInput;
        var value = finite(input.value, null);
        if (value === null) return null;
        return editedPriceKind === 'retail' ? precisePrice(value) : Math.round(value);
    }
    function confirmationRow(label, previousValue, nextValue, suffix) {
        function formatted(value) {
            var parsed = finite(value, null);
            return parsed === null ? '—' : decimal.format(parsed) + (suffix || '');
        }
        return '<span>' + escapeHtml(label) + '</span><span class="is-old">'
            + escapeHtml(formatted(previousValue)) + '</span><span class="is-new">→ '
            + escapeHtml(formatted(nextValue)) + '</span>';
    }
    function openPriceConfirmation(product, payload, plan) {
        pendingPriceChange = { productId: product.id, payload: payload, plan: plan };
        nodes.confirmProduct.textContent = product.store_name + ' · Арт. ' + product.article
            + ' · ' + product.name;
        nodes.confirmTarget.textContent = priceKindLabel(plan.target_kind) + ': '
            + decimal.format(plan.target_price) + ' ₽';
        nodes.confirmGrid.innerHTML = confirmationRow(
            'Базовая цена WB', plan.previous_base_price, plan.base_price, ' ₽'
        ) + confirmationRow(
            'Скидка продавца', plan.previous_discount, plan.discount, '%'
        ) + confirmationRow(
            'Цена без СПП', plan.previous_retail_price, plan.display_retail_price, ' ₽'
        ) + confirmationRow(
            'Цена с СПП', plan.previous_spp_price, plan.predicted_spp_price, ' ₽'
        ) + confirmationRow(
            'С WB Кошельком', plan.previous_wallet_price, plan.predicted_wallet_price, ' ₽'
        );
        nodes.confirmWarning.hidden = !plan.quarantine_risk;
        nodes.confirmWarning.textContent = plan.quarantine_risk
            ? 'Новая цена более чем в 3 раза ниже текущей. WB может поместить её в карантин.' : '';
        nodes.confirmModal.classList.add('is-open');
        nodes.confirmModal.setAttribute('aria-hidden', 'false');
        nodes.confirmSend.focus();
    }
    function closePriceConfirmation() {
        if (sendingPrice) return;
        pendingPriceChange = null;
        nodes.confirmModal.classList.remove('is-open');
        nodes.confirmModal.setAttribute('aria-hidden', 'true');
    }
    async function saveSelectedPrice() {
        var product = productsById[state.selected];
        if (!product || !canEdit) {
            if (!canEdit) showToast('Нет права изменять цены');
            return;
        }
        var targetValue = calculatorTargetValue();
        if (targetValue === null || targetValue <= 0) {
            showToast('Укажите целевую цену больше нуля');
            return;
        }
        var payload = {
            data: [{
                store_slug: product.store_slug,
                article: product.article,
                target_kind: calculatorTargetKind(),
                target_price: targetValue
            }]
        };
        nodes.savePrice.disabled = true;
        nodes.savePrice.textContent = 'Проверяем…';
        try {
            var response = await window.fetch('/api/unit-economics-1c/prices/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify(payload)
            });
            var result = await response.json();
            var plan = Array.isArray(result.accepted) ? result.accepted[0] : null;
            if (!response.ok || !plan) {
                var previewError = Array.isArray(result.errors) && result.errors.length
                    ? result.errors[0].error : result.error;
                throw new Error(previewError || 'Не удалось рассчитать параметры WB');
            }
            openPriceConfirmation(product, payload, plan);
        } catch (error) {
            showToast(error.message || 'Не удалось рассчитать параметры WB');
        } finally {
            nodes.savePrice.disabled = false;
            nodes.savePrice.textContent = 'Сохранить цену';
        }
    }
    async function sendConfirmedPrice() {
        if (!pendingPriceChange || sendingPrice) return;
        var pending = pendingPriceChange;
        var product = productsById[pending.productId];
        sendingPrice = true;
        nodes.confirmSend.disabled = true;
        nodes.confirmCancel.disabled = true;
        nodes.confirmClose.disabled = true;
        nodes.confirmSend.textContent = 'Отправляем и обновляем…';
        if (product) updateSaveState(product);
        try {
            var response = await window.fetch('/api/unit-economics-1c/prices', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify(pending.payload)
            });
            var result = await response.json();
            var accepted = Array.isArray(result.accepted) ? result.accepted : [];
            var firstError = Array.isArray(result.errors) && result.errors.length
                ? result.errors[0].error : result.error;
            if (!response.ok || !accepted.length) {
                throw new Error(firstError || 'WB не принял изменение цены');
            }
            pendingPriceChange = null;
            nodes.confirmModal.classList.remove('is-open');
            nodes.confirmModal.setAttribute('aria-hidden', 'true');
            if (result.price_data_refreshed) {
                showToast('Цена отправлена в WB, через 10 секунд данные обновлены');
                window.setTimeout(function () { window.location.reload(); }, 700);
            } else {
                var syncError = Array.isArray(result.sync_errors) && result.sync_errors.length
                    ? result.sync_errors[0].error : null;
                showToast('Цена отправлена в WB, но обновление данных не завершилось'
                    + (syncError ? ': ' + syncError : ''));
            }
        } catch (error) {
            showToast(error.message || 'Не удалось передать цену в WB');
        } finally {
            sendingPrice = false;
            nodes.confirmSend.disabled = false;
            nodes.confirmCancel.disabled = false;
            nodes.confirmClose.disabled = false;
            nodes.confirmSend.textContent = 'Подтвердить и отправить';
            if (product) updateSaveState(product);
        }
    }
    async function saveSelectedProductSettings() {
        var product = productsById[state.selected];
        if (!product || !canEdit) return;
        var payload = { article: product.article };
        var valid = true;
        Array.prototype.forEach.call(nodes.parameters.querySelectorAll('[data-product-setting]'), function (input) {
            var value = Number(input.value);
            if (!Number.isFinite(value) || value < 0 || (input.dataset.productSetting === 'buyout_percent' && value > 100)) {
                valid = false;
                return;
            }
            payload[input.dataset.productSetting] = value;
        });
        if (!valid) { showToast('Проверьте значения параметров'); return; }

        var button = nodes.parameters.querySelector('[data-save-product-settings]');
        var stateNode = nodes.parameters.querySelector('[data-product-settings-state]');
        button.disabled = true;
        button.textContent = 'Сохраняем…';
        try {
            var response = await window.fetch(
                '/api/unit-economics-1c/product-settings/' + encodeURIComponent(product.store_slug),
                {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json', 'Accept': 'application/json',
                        'X-Requested-With': 'fetch'
                    },
                    body: JSON.stringify(payload)
                }
            );
            var result = await response.json();
            if (!response.ok || !result.ok) throw new Error(result.error || 'Не удалось сохранить параметры');
            var saved = result.settings;
            product.product_settings = {
                store_slug: saved.store_slug, marketplace: saved.marketplace, article: saved.article,
                delivery_wb_rub: saved.delivery_wb_rub, buyout_percent: saved.buyout_percent,
                return_cost_rub: saved.return_cost_rub, volume_l: saved.volume_l,
                storage_wb_rub: saved.storage_wb_rub, updated_at: saved.updated_at,
                updated_by_user_id: saved.updated_by_user_id, updated_by_name: saved.updated_by_name
            };
            product.details.delivery_wb_rub = saved.delivery_wb_rub;
            product.details.buyout_percent = saved.buyout_percent;
            product.details.return_cost_rub = saved.return_cost_rub;
            product.details.volume_l = saved.volume_l;
            product.details.storage_wb_rub = saved.storage_wb_rub;
            product.details.paid_acceptance_cost = saved.paid_acceptance_cost;
            product.details.delivery_with_returns = saved.delivery_with_returns;
            product.details.logistics = saved.delivery_with_returns;
            renderParameters(product);
            fillCalculator(product);
            renderPriceCalculation(product);
            showToast('Параметры товара сохранены');
        } catch (error) {
            button.disabled = false;
            button.textContent = 'Сохранить параметры';
            if (stateNode) stateNode.textContent = 'Не удалось сохранить';
            showToast(error.message || 'Не удалось сохранить параметры');
        }
    }
    nodes.search.addEventListener('input', function () {
        state.query = nodes.search.value;
        resetPageAndRender();
    });
    nodes.store.addEventListener('change', function () {
        state.store = nodes.store.value;
        resetPageAndRender();
    });
    Array.prototype.forEach.call(root.querySelectorAll('[data-state-filter]'), function (button) {
        button.addEventListener('click', function () {
            state.status = button.dataset.stateFilter;
            Array.prototype.forEach.call(root.querySelectorAll('[data-state-filter]'), function (item) {
                item.classList.toggle('is-active', item === button);
            });
            resetPageAndRender();
        });
    });
    nodes.pageSize.addEventListener('change', function () {
        var value = Number(nodes.pageSize.value);
        state.pageSize = [20, 50, 100].indexOf(value) === -1 ? 20 : value;
        resetPageAndRender();
    });
    nodes.pagePrev.addEventListener('click', function () {
        if (state.page <= 1) return;
        state.page -= 1;
        renderPage();
    });
    nodes.pageNext.addEventListener('click', function () { state.page += 1; renderPage(); });
    nodes.pageNumbers.addEventListener('click', function (event) {
        var button = event.target.closest('[data-page]');
        if (!button) return;
        state.page = Number(button.dataset.page) || 1;
        renderPage();
    });
    nodes.rows.addEventListener('click', function (event) {
        var copy = event.target.closest('[data-copy-value]');
        if (copy) { copyText(copy); return; }
        var opener = event.target.closest('[data-product-open]');
        if (opener && productsById[opener.dataset.productOpen]) openDetail(productsById[opener.dataset.productOpen]);
    });
    nodes.rows.addEventListener('keydown', function (event) {
        var field = event.target.closest('[data-comment-id]');
        if (!field || event.key !== 'Enter' || event.shiftKey) return;
        event.preventDefault();
        field.blur();
    });
    nodes.rows.addEventListener('focusout', function (event) {
        var field = event.target.closest('[data-comment-id]');
        if (!field) return;
        setCommentText(field.dataset.commentId, field.value);
        writeJson(commentsKey, comments);
        field.value = commentText(field.dataset.commentId);
        showToast('Комментарий сохранён');
        if (state.query || state.tableFilters[1] || state.sortColumn === 1) renderPage();
    });
    nodes.refresh.addEventListener('click', async function () {
        nodes.refresh.disabled = true;
        var originalText = nodes.refresh.textContent;
        nodes.refresh.textContent = 'Обновляем…';
        try {
            var response = await window.fetch('/api/unit-economics-1c/prices/sync', {
                method: 'POST', headers: { 'X-Requested-With': 'fetch' }
            });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            window.location.reload();
        } catch (error) {
            nodes.refresh.disabled = false;
            nodes.refresh.textContent = originalText;
            showToast('Не удалось обновить цены');
        }
    });
    nodes.detailClose.addEventListener('click', closeDetail);
    nodes.overlay.addEventListener('click', closeDetail);
    Array.prototype.forEach.call(root.querySelectorAll('[data-detail-tab]'), function (button) {
        button.addEventListener('click', function () { setDetailTab(button.dataset.detailTab); });
    });
    Array.prototype.forEach.call(nodes.calculatorInputs, function (input) {
        input.addEventListener('input', function () {
            var product = productsById[state.selected];
            if (!product) return;
            var source = input.dataset.calculatorInput;
            if (input === nodes.priceInput || input === nodes.sppPriceInput || input === nodes.walletPriceInput) {
                editedPriceKind = source;
                if (nodes.calculatorMode.checked) syncDetailedCalculatorInputs(product, source);
                else {
                    syncLinkedPriceInputs(product, editedPriceKind);
                    syncCompactCalculatorInputs(product);
                }
                updateSaveState(product);
            } else if (nodes.calculatorMode.checked) {
                syncDetailedCalculatorInputs(product, source);
                if (source === 'spp') editedPriceKind = 'client';
                else if (source === 'walletPercent') editedPriceKind = 'wallet';
                if (source === 'spp' || source === 'walletPercent') updateSaveState(product);
            } else if (source === 'drr') syncDetailedCalculatorInputs(product, source);
            renderPriceCalculation(product);
        });
    });
    [nodes.priceInput, nodes.sppPriceInput, nodes.walletPriceInput].forEach(function (input) {
        input.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            saveSelectedPrice();
        });
    });
    nodes.calculatorMode.addEventListener('change', function () {
        nodes.calculatorFields.classList.toggle('is-expanded', nodes.calculatorMode.checked);
    });
    nodes.breakEven.addEventListener('click', function () {
        var product = productsById[state.selected];
        if (!product) return;
        var result = breakEvenPrice(product, calculatorValues());
        if (result === null) {
            showToast('Недостаточно реальных данных для расчёта цены без убытка');
            return;
        }
        nodes.priceInput.value = String(result.retail);
        editedPriceKind = 'retail';
        if (nodes.calculatorMode.checked) syncDetailedCalculatorInputs(product, editedPriceKind);
        else {
            syncLinkedPriceInputs(product, editedPriceKind);
            syncCompactCalculatorInputs(product);
        }
        updateSaveState(product);
        renderPriceCalculation(product);
    });
    nodes.calculatorReset.addEventListener('click', function () {
        var product = productsById[state.selected];
        if (!product) return;
        editedPriceKind = 'retail';
        fillCalculator(product);
        updateSaveState(product);
        renderPriceCalculation(product);
        showToast('Параметры возвращены к значениям из базы');
    });
    nodes.savePrice.addEventListener('click', saveSelectedPrice);
    nodes.parameters.addEventListener('click', function (event) {
        if (event.target.closest('[data-save-product-settings]')) saveSelectedProductSettings();
    });
    nodes.chartWrap.addEventListener('mouseleave', hideChartTooltip);
    nodes.confirmClose.addEventListener('click', closePriceConfirmation);
    nodes.confirmCancel.addEventListener('click', closePriceConfirmation);
    nodes.confirmSend.addEventListener('click', sendConfirmedPrice);
    nodes.confirmModal.addEventListener('click', function (event) {
        if (event.target === nodes.confirmModal) closePriceConfirmation();
    });
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') return;
        if (nodes.confirmModal.classList.contains('is-open')) closePriceConfirmation();
        else if (state.selected) closeDetail();
    });

    renderStores();
    nodes.table._tfAdapter = {
        values: tableFilterValues,
        filter: applyTableFilters,
        sort: applyTableSort
    };
    renderPage();
})();
