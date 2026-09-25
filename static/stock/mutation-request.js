(function () {
    'use strict';

    var active = new Map();

    function storageKeyFor(url) {
        return 'stock-request-v1:' + document.getElementById('store-layout').dataset.mutationUser + ':' + url;
    }

    function hex(bytes) {
        return Array.from(bytes, function (byte) {
            return byte.toString(16).padStart(2, '0');
        }).join('');
    }

    async function digest(value) {
        var bytes = typeof value === 'string' ? new TextEncoder().encode(value) : new Uint8Array(value);
        if (crypto.subtle) return hex(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)));
        // The app also supports HTTP on a local network, where SubtleCrypto is unavailable.
        // This is only a pending-form fingerprint; the server independently hashes the actual request.
        var constants = [
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
            0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
            0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
            0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
            0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
            0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
            0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
        ];
        var state = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
        var padded = new Uint8Array(Math.ceil((bytes.length + 9) / 64) * 64);
        padded.set(bytes);
        padded[bytes.length] = 128;
        var view = new DataView(padded.buffer);
        view.setUint32(padded.length - 8, Math.floor(bytes.length / 0x20000000));
        view.setUint32(padded.length - 4, bytes.length * 8);
        var words = new Uint32Array(64);
        var rotate = function (word, bits) { return (word >>> bits) | (word << (32 - bits)); };
        for (var offset = 0; offset < padded.length; offset += 64) {
            for (var i = 0; i < 16; i++) words[i] = view.getUint32(offset + i * 4);
            for (i = 16; i < 64; i++) {
                var x = words[i - 15], y = words[i - 2];
                words[i] = words[i - 16] + (rotate(x, 7) ^ rotate(x, 18) ^ (x >>> 3)) +
                    words[i - 7] + (rotate(y, 17) ^ rotate(y, 19) ^ (y >>> 10));
            }
            var h = state.slice();
            for (i = 0; i < 64; i++) {
                var first = (h[7] + (rotate(h[4], 6) ^ rotate(h[4], 11) ^ rotate(h[4], 25)) +
                    ((h[4] & h[5]) ^ (~h[4] & h[6])) + constants[i] + words[i]) >>> 0;
                var second = ((rotate(h[0], 2) ^ rotate(h[0], 13) ^ rotate(h[0], 22)) +
                    ((h[0] & h[1]) ^ (h[0] & h[2]) ^ (h[1] & h[2]))) >>> 0;
                h = [(first + second) >>> 0, h[0], h[1], h[2], (h[3] + first) >>> 0, h[4], h[5], h[6]];
            }
            for (i = 0; i < 8; i++) state[i] = (state[i] + h[i]) >>> 0;
        }
        return state.map(function (word) { return word.toString(16).padStart(8, '0'); }).join('');
    }

    function requestKey() {
        if (crypto.randomUUID) return crypto.randomUUID();
        var bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        return hex(bytes);
    }

    async function mutationFetch(url, options) {
        options = options || {};
        var body = options.body;
        if (options.method !== 'POST' || (body instanceof FormData && body.get('preview'))) {
            return fetch(url, options);
        }
        // Separate pending operations by account and endpoint. Only hashes and the request key
        // survive a reload; file contents and business data are not stored in browser storage.
        var storageKey = storageKeyFor(url);
        var parts = [];
        if (body instanceof FormData) {
            for (var entry of body.entries()) {
                if (entry[0] === 'confirmation_token') continue;
                var value = entry[1];
                parts.push([entry[0], value instanceof Blob
                    ? [value.name, await digest(await value.arrayBuffer())] : value]);
            }
            parts.sort(function (a, b) { return a[0].localeCompare(b[0]); });
        } else {
            parts = body;
        }
        var signature = await digest(JSON.stringify(parts));
        var pending = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
        if (pending && pending.signature !== signature) {
            throw new Error('Результат предыдущей операции неизвестен. Сначала повторите её с прежними данными.');
        }
        if (!pending) {
            pending = {
                key: requestKey(),
                signature: signature,
                confirmationToken: body instanceof FormData ? body.get('confirmation_token') : null,
            };
            // If storage is unavailable, fail before sending: a reload must not lose the key.
            sessionStorage.setItem(storageKey, JSON.stringify(pending));
        }
        if (active.has(storageKey)) return (await active.get(storageKey)).clone();
        if (body instanceof FormData && pending.confirmationToken) {
            body.set('confirmation_token', pending.confirmationToken);
        }
        var headers = new Headers(options.headers || {});
        headers.set('Idempotency-Key', pending.key);
        var promise = (async function () {
            var response = await fetch(url, Object.assign({}, options, { headers: headers }));
            var data = await response.clone().json();
            // Unknown results keep the original identity. A successful next deliberate submission
            // starts a new operation, including an identical Excel file / Google sheet.
            if ((response.ok && data.ok === true) ||
                (response.status === 400 && data.ok === false) || response.status === 422 ||
                data.code === 'confirmation_changed') {
                sessionStorage.removeItem(storageKey);
            }
            return response;
        })();
        active.set(storageKey, promise);
        try {
            return (await promise).clone();
        } finally {
            active.delete(storageKey);
        }
    }

    window.CheckStockMutation = {
        fetch: mutationFetch,
        hasPending: function (url) {
            try { return Boolean(sessionStorage.getItem(storageKeyFor(url))); }
            catch (_) { return false; } // Sending will fail safely before fetch if storage remains unavailable.
        },
    };
})();
