(() => {
    'use strict';
    const e = window.CheckStockUI.escapeHtml;
    const statuses = { updated: 'Обновлён', unchanged: 'Без изменений', skipped: 'Пропущен', unknown: 'Есть замечания' };

    function products(report) {
        const rows = new Map();
        const get = item => {
            if (!rows.has(item.row)) rows.set(item.row, { row: item.row, article: item.article, project: item.project, checked: null, written: null, unchanged: null, problems: new Map(), hardIssue: false, future: new Set() });
            return rows.get(item.row);
        };
        for (const item of report.products || []) {
            Object.assign(get(item), { checked: item.checked_cells, written: item.written_cells, unchanged: item.unchanged_cells });
        }
        for (const [items, kind] of [[report.issues, 'issue'], [report.missing_data, 'future'], [report.zero_filled, 'incomplete']]) {
            for (const item of items || []) {
                const product = get(item);
                product.hardIssue ||= kind === 'issue';
                if (kind === 'future') product.future.add(item.week || item.cell);
                const key = item.reason || (kind === 'future' ? 'future' : 'missing_data');
                if (!product.problems.has(key)) product.problems.set(key, { reason: key, entries: new Map() });
                const problem = product.problems.get(key);
                const entryKey = JSON.stringify([item.week || '', item.cell || '']);
                if (!problem.entries.has(entryKey)) problem.entries.set(entryKey, { week: item.week || '', cell: item.cell || '', days: new Set() });
                for (const day of item.missing_days || []) problem.entries.get(entryKey).days.add(day);
            }
        }
        for (const product of rows.values()) {
            product.status = product.checked === null
                ? product.hardIssue || product.future.size >= Math.max(1, (report.periods || []).length) ? 'skipped' : 'unknown'
                : !product.checked ? 'skipped' : product.written ? 'updated' : 'unchanged';
        }
        return Array.from(rows.values());
    }

    function totals(report, rows) {
        const exact = Array.isArray(report.products);
        const updated = exact ? rows.filter(row => row.status === 'updated').length : report.written_cells === 0 ? 0 : null;
        const skipped = rows.filter(row => row.status === 'skipped').length;
        const unprocessed = rows.filter(row => row.status === 'skipped' && (row.problems.has('source') || row.future.size)).length;
        const unchanged = exact ? rows.filter(row => row.status === 'unchanged').length
            : report.written_cells === 0 && report.checked_cells > 0 ? Math.max(0, report.matched_products - unprocessed) : null;
        const incomplete = rows.filter(row => row.problems.has('missing_data') || row.problems.has('future')).length;
        return { updated, skipped, unchanged, incomplete };
    }

    function summaryHtml(report, action, previous = false) {
        if (!report) return '<p class="week-update-hint">Выгрузка ещё не запускалась.</p>';
        const counts = totals(report, products(report));
        const metric = (label, value, filter, tone) => {
            const unavailable = value === null || value === 0 || !Array.isArray(report.products) && filter === 'unchanged';
            return `<button class="week-product-metric" type="button" data-week-products="${e(action)}" data-week-product-filter="${filter}" data-tone="${tone}"${unavailable ? ' disabled' : ''}><strong>${value ?? '—'}</strong><span>${label}</span></button>`;
        };
        const when = report.processed_at ? new Date(report.processed_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) + ' МСК' : '';
        return `<div class="week-product-summary">${previous ? `<p class="week-previous-result">Предыдущий результат${when ? ' · ' + e(when) : ''}</p>` : ''}<div class="week-product-metrics" aria-label="Результат по товарам">${metric('Обновлено', counts.updated, 'updated', 'success')}${metric('Пропущено', counts.skipped, 'skipped', 'warning')}${metric('Без изменений', counts.unchanged, 'unchanged', 'neutral')}</div><div class="week-product-footer">${counts.incomplete ? `<button class="week-product-warning" type="button" data-week-products="${e(action)}" data-week-product-filter="incomplete">Неполные данные: ${counts.incomplete} товаров</button>` : ''}<button class="btn-secondary" type="button" data-week-products="${e(action)}">Подробнее по товарам</button></div></div>`;
    }

    function show(report, title, reason, initialFilter = 'all', marketplace = report?.marketplace || 'WB') {
        if (!report) return;
        const previous = document.querySelector('[data-week-products-dialog]');
        if (previous) { previous.close(); previous.remove(); }
        const rows = products(report);
        const priority = { skipped: 0, unknown: 1, updated: 2, unchanged: 3 };
        rows.sort((a, b) => priority[a.status] - priority[b.status] || a.row - b.row);
        const dialog = document.createElement('dialog');
        dialog.className = 'week-products-dialog';
        dialog.dataset.weekProductsDialog = '';
        dialog.setAttribute('aria-labelledby', 'week-products-title');
        dialog.innerHTML = `<div class="week-products-head"><h2 id="week-products-title">${e(title)}</h2><button class="btn-secondary" type="button" data-products-close aria-label="Закрыть подробности">Закрыть</button></div>${Array.isArray(report.products) ? '' : '<p class="week-update-hint">Показаны товары с замечаниями. Полный список и точное число обновлённых товаров появятся после следующей выгрузки.</p>'}<div class="week-products-filters"><label class="week-field"><span>Артикул или проект</span><input class="input-control" type="search" data-products-search placeholder="Найти товар…" /></label><label class="week-field"><span>Результат</span><select class="integration-select" data-products-filter><option value="all">Все товары</option><option value="skipped">Пропущенные</option><option value="updated">Обновлённые</option><option value="unchanged">Без изменений</option><option value="incomplete">С неполными данными</option></select></label></div><div class="week-products-table"><table><thead><tr><th scope="col">Товар</th><th scope="col">Результат</th><th scope="col">Подробности</th></tr></thead><tbody data-products-rows></tbody></table></div><p data-products-empty hidden>Товары не найдены.</p><div class="week-products-pages"><button class="btn-secondary" type="button" data-products-prev>Назад</button><span data-products-count role="status" aria-live="polite"></span><button class="btn-secondary" type="button" data-products-next>Далее</button></div>`;
        const query = selector => dialog.querySelector(selector);
        const pageSize = 50;
        const imageCache = new Map();
        const pendingImages = new Set();
        const imageKey = product => JSON.stringify([product.project, product.article]);
        let page = 0;
        const cellLink = (cell, text = cell) => `<a href="${e(report.spreadsheet_url + '#gid=' + report.sheet_id + '&range=' + encodeURIComponent(cell))}" target="_blank" rel="noopener noreferrer">${e(text)}</a>`;
        function imageUrl(value) {
            try {
                const url = new URL(value);
                return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
            } catch (_) { return ''; }
        }
        function renderImages() {
            if (!dialog.isConnected) return;
            dialog.querySelectorAll('[data-product-photo]').forEach(media => {
                const key = media.dataset.productPhoto;
                const url = imageCache.get(key);
                const placeholder = media.querySelector('span');
                if (!url) {
                    if (imageCache.has(key)) { placeholder.textContent = 'Нет фото'; media.title = 'Фото недоступно'; }
                    return;
                }
                if (media.querySelector('img')) return;
                const image = document.createElement('img');
                image.alt = '';
                image.loading = 'lazy';
                image.decoding = 'async';
                image.referrerPolicy = 'no-referrer';
                image.addEventListener('load', () => { placeholder.hidden = true; });
                image.addEventListener('error', () => {
                    image.remove();
                    placeholder.hidden = false;
                    placeholder.textContent = 'Нет фото';
                    media.title = 'Фото недоступно';
                    imageCache.set(key, '');
                });
                image.src = url;
                media.append(image);
            });
        }
        async function loadImages(visible) {
            const missing = new Map();
            for (const product of visible) {
                const key = imageKey(product);
                if (imageCache.has(key) || pendingImages.has(key)) continue;
                if (!product.article || !product.project) { imageCache.set(key, ''); continue; }
                missing.set(key, { article: product.article, project: product.project });
            }
            renderImages();
            if (!missing.size) return;
            missing.forEach((_, key) => pendingImages.add(key));
            try {
                const response = await fetch('/api/admin/google-weeks/' + encodeURIComponent(marketplace) + '/product-images', {
                    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ items: Array.from(missing.values()) }),
                });
                if (!response.ok) throw new Error('images');
                const data = await response.json();
                if (!Array.isArray(data)) throw new Error('images');
                for (const item of data) {
                    const key = imageKey(item);
                    if (missing.has(key)) imageCache.set(key, imageUrl(item.image_url));
                }
            } catch (_) { }
            finally {
                missing.forEach((_, key) => {
                    pendingImages.delete(key);
                    if (!imageCache.has(key)) imageCache.set(key, '');
                });
                renderImages();
            }
        }
        function details(product) {
            const values = product.checked === null ? '' : `<p>Ячеек обновлено: ${product.written}; без изменений: ${product.unchanged}.</p>`;
            const problems = Array.from(product.problems.values(), problem => {
                const entries = Array.from(problem.entries.values(), entry => {
                    const location = [e(entry.week), entry.cell ? cellLink(entry.cell) : ''].filter(Boolean).join(' · ');
                    const days = entry.days.size ? 'Нет данных за: ' + e(Array.from(entry.days).sort().join(', ')) : '';
                    return location || days ? '<p>' + [location, days].filter(Boolean).join('<br>') + '</p>' : '';
                }).join('');
                return `<details><summary>${e(reason(problem.reason))}</summary>${entries}</details>`;
            }).join('');
            return values + problems;
        }
        function render() {
            const search = query('[data-products-search]').value.trim().toLocaleLowerCase('ru');
            const filter = query('[data-products-filter]').value;
            const filtered = rows.filter(product => {
                const matches = filter === 'all' || (filter === 'incomplete'
                    ? product.problems.has('missing_data') || product.problems.has('future') : product.status === filter);
                return matches && `${product.article} ${product.project}`.toLocaleLowerCase('ru').includes(search);
            });
            const lastPage = Math.max(0, Math.ceil(filtered.length / pageSize) - 1);
            page = Math.min(page, lastPage);
            const start = page * pageSize;
            const visible = filtered.slice(start, start + pageSize);
            query('[data-products-rows]').innerHTML = visible.map(product => `<tr><td><div class="week-product-identity"><span class="week-product-photo" data-product-photo="${e(imageKey(product))}" aria-hidden="true"><span>Фото</span></span><div class="week-product-copy"><strong>${e(product.article || 'Без артикула')}</strong><br>${e(product.project || 'Без проекта')}<br><small>${cellLink('A' + product.row, 'Строка ' + product.row)}</small></div></div></td><td>${e(statuses[product.status])}</td><td>${details(product)}</td></tr>`).join('');
            query('[data-products-empty]').hidden = Boolean(filtered.length);
            query('[data-products-count]').textContent = filtered.length ? `${start + 1}–${Math.min(start + pageSize, filtered.length)} из ${filtered.length}` : '0 товаров';
            query('[data-products-prev]').disabled = page === 0;
            query('[data-products-next]').disabled = page === lastPage;
            loadImages(visible);
        }
        query('[data-products-search]').addEventListener('input', () => { page = 0; render(); });
        query('[data-products-filter]').addEventListener('change', () => { page = 0; render(); });
        query('[data-products-filter]').value = initialFilter;
        query('[data-products-prev]').addEventListener('click', () => { page--; render(); });
        query('[data-products-next]').addEventListener('click', () => { page++; render(); });
        query('[data-products-close]').addEventListener('click', () => dialog.close());
        dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
        dialog.addEventListener('close', () => dialog.remove(), { once: true });
        document.body.append(dialog);
        render();
        dialog.showModal();
    }

    window.CheckStockWeekReport = { summaryHtml, show };
})();
