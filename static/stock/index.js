(function () {
    try {
        var storage = window.sessionStorage;
        for (var i = storage.length - 1; i >= 0; i--) {
            var key = storage.key(i);
            if (key && key.indexOf('paketa.access.') === 0) storage.removeItem(key);
        }
    } catch (e) {}
})();

(function () {
    var panel = document.getElementById('sync-products-panel');
    var btn = document.getElementById('sync-products-btn');
    var status = document.getElementById('sync-products-status');
    var results = document.getElementById('sync-products-results');
    var lastSync = document.getElementById('sync-products-last');
    if (!btn) return;

    var AUTO_HIDE_MS = 60000;
    var hideTimer = null;

    var escapeHtml = window.CheckStockUI.escapeHtml;

    function badge(label, state, text) {
        var suffix = text === undefined || text === null || text === '' ? '' : ': ' + escapeHtml(text);
        return window.CheckStockUI.render('stock/index/badge', {
            state: state,
            label: label,
            suffix: suffix,
        });
    }

    function renderEndpoint(label, entry) {
        if (!entry) return badge(label, 'muted', '—');
        if (entry.ok) return badge(label, 'ok', String(entry.count));
        return badge(label, 'error', entry.error || 'ошибка');
    }

    function mpBlock(title, hasToken, body) {
        if (!hasToken) {
            return window.CheckStockUI.render('stock/index/mp-block', {
                title: title,
                content: badge('нет токена', 'muted', ''),
            });
        }
        return window.CheckStockUI.render('stock/index/mp-block-2', { title: title, body: body });
    }

    function catalogBadge(cat) {
        if (!cat) return badge('товаров', 'muted', '—');
        if (cat && cat.ok === false) return badge('каталог', 'error', cat.error);

        var changes = [];
        if (cat.added) changes.push('+' + cat.added);
        if (cat.removed) changes.push('−' + cat.removed);
        return badge(
            'товаров',
            'ok',
            Number(cat.total || 0) + (changes.length ? ' (' + changes.join(', ') + ')' : ''),
        );
    }

    function ozonBody(entry) {
        return catalogBadge(entry.ozon_catalog) + renderEndpoint('остатки', entry.ozon);
    }

    function renderResults(report) {
        var slugs = Object.keys(report || {});
        if (!slugs.length) {
            results.innerHTML = '';
            return;
        }
        results.innerHTML = slugs
            .map(function (slug) {
                var entry = report[slug] || {};
                var name = slug.toUpperCase();

                var wb = mpBlock(
                    'WB',
                    entry.token,
                    catalogBadge(entry.wb_catalog) +
                        renderEndpoint('FBS', entry.fbs) +
                        renderEndpoint('FBO', entry.fbo),
                );
                var ozon = mpBlock('OZON', entry.ozon_token, ozonBody(entry));
                var yandex = mpBlock(
                    'ЯНДЕКС',
                    entry.yandex_token,
                    catalogBadge(entry.yandex_catalog) + renderEndpoint('остатки', entry.yandex),
                );

                return window.CheckStockUI.render('stock/index/render-results', {
                    name: name,
                    wb: wb,
                    ozon: ozon,
                    yandex: yandex,
                });
            })
            .join('');
    }

    function reportHasErrors(report) {
        var fields = ['wb_catalog', 'fbs', 'fbo', 'ozon_catalog', 'ozon', 'yandex_catalog', 'yandex'];
        return Object.keys(report || {}).some(function (slug) {
            var entry = report[slug] || {};
            return fields.some(function (field) {
                return entry[field] && entry[field].ok === false;
            });
        });
    }

    function responseData(response) {
        return response.text().then(function (body) {
            var data = {};
            if (body) {
                try {
                    data = JSON.parse(body);
                } catch (e) {
                    data = {};
                }
            }
            if (!response.ok) {
                throw new Error(data.error || data.detail || 'HTTP ' + response.status);
            }
            return data;
        });
    }

    function setBusy(isBusy) {
        btn.disabled = isBusy;
        btn.setAttribute('aria-busy', isBusy ? 'true' : 'false');
        panel.classList.toggle('is-syncing', isBusy);
    }

    function scheduleAutoHide() {
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = setTimeout(function () {
            panel.classList.add('is-fading');
            setTimeout(function () {
                status.textContent = '';
                results.innerHTML = '';
                panel.classList.remove('is-fading');
            }, 600);
        }, AUTO_HIDE_MS);
    }

    btn.addEventListener('click', function () {
        if (hideTimer) clearTimeout(hideTimer);
        panel.classList.remove('is-fading');
        setBusy(true);
        status.textContent = 'Обновление остатков...';
        results.innerHTML = '';
        fetch('/admin/sync-stock', {
            method: 'POST',
            headers: { Accept: 'application/json' },
        })
            .then(responseData)
            .then(function (data) {
                if (!data.report || typeof data.report !== 'object') {
                    throw new Error('Сервер не вернул отчёт о синхронизации');
                }
                status.textContent = reportHasErrors(data.report)
                    ? 'Синхронизация завершена с ошибками'
                    : 'Товары и остатки обновлены';
                renderResults(data.report);
                if (data.last_sync && lastSync) {
                    lastSync.textContent = data.last_sync;
                }
            })
            .catch(function (err) {
                status.textContent = 'Ошибка синхронизации: ' + (err.message || String(err));
            })
            .finally(function () {
                setBusy(false);
                scheduleAutoHide();
            });
    });
})();
