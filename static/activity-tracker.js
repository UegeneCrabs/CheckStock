(function () {
    'use strict';

    var body = document.body;
    if (!body || !body.dataset.section) return;

    var IDLE_AFTER_MS = 10 * 60 * 1000;
    var HEARTBEAT_MS = 60 * 1000;
    var lastInteractionAt = Date.now();
    var idleReported = false;

    function send(active, pageView) {
        fetch('/api/activity/heartbeat', {
            method: 'POST',
            credentials: 'same-origin',
            keepalive: true,
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'fetch'
            },
            body: JSON.stringify({
                path: window.location.pathname,
                active: active,
                page_view: Boolean(pageView)
            })
        }).catch(function () {
            // Статистика не должна мешать работе сайта при проблемах с сетью.
        });
    }

    function markInteraction() {
        var wasIdle = idleReported || Date.now() - lastInteractionAt >= IDLE_AFTER_MS;
        lastInteractionAt = Date.now();
        idleReported = false;
        if (wasIdle && document.visibilityState === 'visible') send(true, false);
    }

    ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach(function (eventName) {
        window.addEventListener(eventName, markInteraction, { passive: true });
    });
    window.addEventListener('focus', markInteraction);

    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState !== 'visible') return;
        if (Date.now() - lastInteractionAt >= IDLE_AFTER_MS) {
            if (!idleReported) send(false, false);
            idleReported = true;
        } else {
            send(true, false);
        }
    });

    send(true, true);
    window.setInterval(function () {
        if (document.visibilityState !== 'visible') return;
        var active = Date.now() - lastInteractionAt < IDLE_AFTER_MS;
        if (active) {
            idleReported = false;
            send(true, false);
        } else if (!idleReported) {
            idleReported = true;
            send(false, false);
        }
    }, HEARTBEAT_MS);
})();
