window.CheckStockProductComments = (() => {
    'use strict';
    const legacyPrefixes = { WB: '', OZON: 'ozon:', 'YANDEX MARKET': 'yandex:' };
    const batchSize = 100;
    const productKey = product => JSON.stringify([product.store_slug, String(product.article)]);
    const normalize = text => String(text || '').replace(/\r\n?/g, '\n').trim();

    function readLegacy(legacyKey) {
        try {
            const value = JSON.parse(localStorage.getItem(legacyKey) || '{}');
            return value && typeof value === 'object' ? value : {};
        } catch {
            return {};
        }
    }

    function legacyEntry(prefix, id, raw) {
        if (!id.startsWith(prefix)) return null;
        const separator = id.indexOf(':', prefix.length);
        if (separator <= prefix.length) return null;
        const store_slug = id.slice(prefix.length, separator);
        const article = id.slice(separator + 1);
        const text = normalize(typeof raw === 'string' ? raw : [raw?.first, raw?.second].filter(Boolean).join('\n'));
        if (!article || !text) return null;
        return { id, raw, comment: { store_slug, article, text } };
    }

    function create(marketplace, legacyKey) {
        const endpoint = '/api/unit-economics-1c/product-comments/' + encodeURIComponent(marketplace);
        const draft = new Map();
        const pending = new Map();
        let saved = new Map();
        let ready = false;
        let migrated = false;
        let revision = 0;

        async function request(suffix, method, body, signal) {
            const response = await window.fetch(endpoint + suffix, {
                method, signal,
                headers: {
                    Accept: 'application/json', 'X-Requested-With': 'fetch',
                    ...(body ? { 'Content-Type': 'application/json' } : {}),
                },
                body: body ? JSON.stringify(body) : undefined,
            });
            if (!response.ok || response.redirected) throw new Error('Product comments request failed');
            return response.json();
        }

        async function importLegacy(signal) {
            if (migrated) return;
            const prefix = legacyPrefixes[marketplace];
            const entries = Object.entries(readLegacy(legacyKey))
                .map(([id, raw]) => legacyEntry(prefix, id, raw)).filter(Boolean);
            for (let start = 0; start < entries.length; start += batchSize) {
                const batch = entries.slice(start, start + batchSize);
                const result = await request('/import', 'POST', { comments: batch.map(item => item.comment) }, signal);
                const accepted = new Set(result.accepted.map(productKey));
                const remaining = readLegacy(legacyKey);
                batch.forEach(({ id, raw, comment }) => {
                    if (accepted.has(productKey(comment)) && JSON.stringify(remaining[id]) === JSON.stringify(raw))
                        delete remaining[id];
                });
                try {
                    if (Object.keys(remaining).length) localStorage.setItem(legacyKey, JSON.stringify(remaining));
                    else localStorage.removeItem(legacyKey);
                } catch {}
            }
            migrated = true;
        }

        async function load(signal) {
            let migrationFailed = false;
            try {
                await importLegacy(signal);
            } catch (error) {
                if (signal.aborted) throw error;
                migrationFailed = true;
            }
            const before = revision;
            const result = await request('', 'GET', null, signal);
            if (!signal.aborted && !pending.size && before === revision) {
                saved = new Map(result.comments.map(comment => [productKey(comment), comment.text]));
                ready = true;
            }
            return migrationFailed;
        }

        function text(product) {
            const key = productKey(product);
            return draft.get(key) ?? saved.get(key) ?? '';
        }

        function edit(product, value) {
            draft.set(productKey(product), value);
        }

        async function save(product, value) {
            const key = productKey(product);
            const text = normalize(value);
            const previous = pending.get(key);
            if (!previous && text === (saved.get(key) ?? '')) {
                draft.delete(key);
                return null;
            }
            draft.set(key, text);
            if (previous?.text === text) return null;

            const task = { text };
            const settled = previous ? previous.result.catch(() => {}) : Promise.resolve();
            task.result = settled.then(async () => {
                const result = await request('', 'PUT', {
                    store_slug: product.store_slug, article: String(product.article), text,
                });
                saved.set(key, result.text);
                revision += 1;
                if (pending.get(key) === task && draft.get(key) === text) draft.delete(key);
                return result.text;
            }).finally(() => {
                if (pending.get(key) === task) pending.delete(key);
            });
            pending.set(key, task);
            return task.result;
        }

        return { load, text, edit, save, get ready() { return ready; } };
    }

    return { create };
})();
