window.stockTable = (function () {
    var whSelect = document.getElementById('wh-select');
    var status = document.getElementById('ff-select-status');
    var table = document.getElementById('stock-table');

    var storeSlug = document.getElementById('store-layout').dataset.store;
    var requestSeq = 0;

    function currentFf() {
        return whSelect ? whSelect.value : '';
    }

    function currentMp() {
        var layout = document.getElementById('store-layout');
        return layout ? layout.getAttribute('data-marketplace') : '';
    }

    function applyValues(cellClass, stock) {
        if (!table) return;
        table.querySelectorAll('tbody tr[data-article]').forEach(function (row) {
            var article = row.getAttribute('data-article');
            var cell = row.querySelector(cellClass);
            if (!cell) return;
            var value = Object.prototype.hasOwnProperty.call(stock, article) ? stock[article] : 0;
            cell.textContent = value === 0 ? '—' : String(value);
        });
    }

    function query(path, params) {
        var qs = Object.keys(params)
            .filter(function (k) {
                return params[k];
            })
            .map(function (k) {
                return k + '=' + encodeURIComponent(params[k]);
            })
            .join('&');
        return '/stock/' + storeSlug + '/' + path + (qs ? '?' + qs : '');
    }

    var SCHEMES = ((table && table.getAttribute('data-schemes')) || 'fbs,fbo').split(',');

    function num(cell) {
        if (!cell) return 0;
        var t = cell.textContent.replace(/[\s ]/g, '');
        if (!t || t === '—') return 0;
        var n = parseInt(t, 10);
        return isNaN(n) ? 0 : n;
    }

    function fmt(value) {
        return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    }

    function recalcTotals() {
        if (!table) return;
        var ff = 0,
            transit = 0,
            saleTotals = {};
        SCHEMES.forEach(function (scheme) {
            saleTotals[scheme] = 0;
        });

        table.querySelectorAll('tbody tr[data-article]').forEach(function (row) {
            var rowFf = num(row.querySelector('.col-ff-available'));
            var rowTransit = num(row.querySelector('.col-transit'));
            var rowSale = 0;
            var rowByScheme = {};
            SCHEMES.forEach(function (scheme) {
                var v = num(row.querySelector('.col-' + scheme));
                rowByScheme[scheme] = v;
                rowSale += v;
            });
            var rowTotal = rowFf + rowTransit + rowSale;

            var totalCell = row.querySelector('.col-row-total');
            if (totalCell) totalCell.textContent = rowTotal === 0 ? '—' : fmt(rowTotal);

            row.classList.toggle('row-alert', rowFf > 0 && rowSale === 0);

            if (row.style.display === 'none') return;
            ff += rowFf;
            transit += rowTransit;
            SCHEMES.forEach(function (scheme) {
                saleTotals[scheme] += rowByScheme[scheme];
            });
        });

        var set = function (sel, v) {
            var el = table.querySelector(sel);
            if (el) el.textContent = fmt(v);
        };
        var grand = ff + transit;
        set('.tot-ff', ff);
        set('.tot-transit', transit);
        SCHEMES.forEach(function (scheme) {
            set('.tot-' + scheme, saleTotals[scheme]);
            grand += saleTotals[scheme];
        });
        set('.tot-grand', grand);
    }

    function syncStickyOffset() {
        if (!table) return;

        var headRow = table.querySelector('thead tr');
        if (headRow) table.style.setProperty('--thead-h', headRow.offsetHeight + 'px');
    }

    function refresh() {
        if (!table) return Promise.resolve();
        var ff = currentFf();
        var mp = currentMp();
        var seq = ++requestSeq;
        if (status) status.textContent = 'Загрузка...';

        function loadStock(endpoint, field) {
            return fetch(query(endpoint, { ff: ff, mp: mp }))
                .then(function (response) {
                    if (!response.ok) throw new Error('HTTP ' + response.status);
                    return response.json();
                })
                .then(function (data) {
                    if (!data[field] || typeof data[field] !== 'object')
                        throw new Error('Некорректный ответ сервера');
                    return data;
                });
        }
        return Promise.all([
            loadStock('fbs', 'fbs'),
            loadStock('ff-available', 'ff_available'),
            loadStock('transit', 'transit'),
        ])
            .then(function (results) {
                if (seq !== requestSeq) return;
                applyValues('.col-fbs', results[0].fbs || {});
                applyValues('.col-rfbs', results[0].rfbs || {});
                applyValues('.col-ff-available', results[1].ff_available || {});
                applyValues('.col-transit', results[2].transit || {});
                recalcTotals();
                if (status) {
                    status.textContent =
                        (ff ? 'Показаны остатки по: ' + ff : 'Показано общее по всем складам') +
                        (mp ? ' · ' + mp : ' · все маркетплейсы');
                }
            })
            .catch(function (err) {
                if (seq !== requestSeq) return;
                if (status) status.textContent = 'Не удалось загрузить остатки: ' + err;
            });
    }

    var xlsxLink = document.getElementById('stock-xlsx-link');
    function syncXlsxLink() {
        if (!xlsxLink) return;
        var url = '/stock/' + storeSlug + '/stock.xlsx?mp=' + encodeURIComponent(currentMp());
        var ff = currentFf();
        if (ff) url += '&ff=' + encodeURIComponent(ff);
        xlsxLink.setAttribute('href', url);
    }
    syncXlsxLink();

    if (whSelect)
        whSelect.addEventListener('change', function () {
            syncXlsxLink();
            refresh();
        });

    (function () {
        document.querySelectorAll('.mp-tab[data-mp]').forEach(function (tab) {
            tab.addEventListener('click', function () {
                if (tab.classList.contains('active')) return;
                window.location =
                    '/stock/' + storeSlug + '?mp=' + encodeURIComponent(tab.getAttribute('data-mp'));
            });
        });

        var placeholder = document.getElementById('mp-placeholder');
        var layout = document.getElementById('store-layout');
        if (placeholder && layout) {
            var ready = placeholder.getAttribute('data-ready') === '1';
            placeholder.classList.toggle('is-hidden', ready);
            layout.classList.toggle('is-hidden', !ready);
        }
    })();

    if (table) {
        var observer = new MutationObserver(recalcTotals);
        table.querySelectorAll('tbody tr[data-article]').forEach(function (row) {
            observer.observe(row, { attributes: true, attributeFilter: ['style'] });
        });

        window.addEventListener('load', syncStickyOffset);
        window.addEventListener('resize', syncStickyOffset);
        syncStickyOffset();
    }

    return { refresh: refresh, recalcTotals: recalcTotals, currentFf: currentFf, currentMp: currentMp };
})();
