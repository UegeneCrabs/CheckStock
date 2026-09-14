(() => {
    'use strict';
    const root = document.querySelector('[data-inbound-page]');
    if (!root) return;
    const el = (name) => root.querySelector(`[data-inbound-${name}]`);
    const scopes = JSON.parse(root.dataset.scopes || '[]');
    const marketplaceOptions = Array.from(el('marketplace').options).map((option) => ({
        value: option.value,
        text: option.text,
    }));
    const labels = {
        planned: 'Запланировано',
        transit: 'В пути / транзит',
        acceptance: 'На приёмке',
        placement: 'Принято, размещается',
        discrepancy: 'Расхождения / спор',
        completed: 'Завершено',
        cancelled: 'Отменено / отказ',
        unknown: 'Требует проверки',
    };
    const statuses = {
        never: 'Ещё не загружалось',
        running: 'Обновляется',
        ok: 'Обновлено',
        partial: 'Есть замечания',
        error: 'Ошибка обновления',
        not_configured: 'Не подключено',
    };
    const stageOrder = [
        'planned',
        'transit',
        'acceptance',
        'placement',
        'discrepancy',
        'unknown',
        'completed',
        'cancelled',
    ];
    const escape = window.CheckStockUI.escapeHtml;
    const number = (value) => (value == null ? '—' : Number(value).toLocaleString('ru-RU'));
    const date = (value) => {
        if (!value) return '—';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime())
            ? '—'
            : parsed.toLocaleString('ru-RU', {
                  day: '2-digit',
                  month: '2-digit',
                  year: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                  timeZone: 'Europe/Moscow',
              });
    };
    let targets = [];
    let stage = '';
    let visibleCount = 40;
    let requestSequence = 0;
    let loading = false;
    let refreshing = false;
    let autoTimer;
    let openCards = new Set();
    const notice = (text, error = false) => {
        el('notice').textContent = text;
        el('notice').classList.toggle('is-error', error);
    };
    const query = () => new URLSearchParams({ store: el('store').value, mp: el('marketplace').value });
    const cardKey = (target, supply) => `${target.store_slug}|${target.marketplace}|${supply.key}`;
    const allSupplies = () =>
        targets.flatMap((target) => target.supplies.map((supply) => ({ target, supply })));
    const matches = (supply, term) =>
        !term ||
        [
            supply.number,
            supply.supply_id,
            supply.warehouse,
            supply.campaign_name,
            ...supply.items.flatMap((item) => [
                item.article,
                item.barcode,
                item.name,
                item.vendor_code,
                item.sku,
            ]),
        ].some((value) =>
            String(value || '')
                .toLocaleLowerCase('ru')
                .includes(term),
        );

    function connectionCards() {
        const trouble = targets.filter(
            (target) =>
                target.status === 'error' ||
                target.status === 'partial' ||
                (target.stale && target.last_success),
        );
        const running = targets.filter((target) => target.status === 'running').length;
        el('connection-summary').textContent =
            `Подключения: ${targets.length}${running ? ` · обновляется ${running}` : ''}${trouble.length ? ` · требуют внимания ${trouble.length}` : ''}`;
        el('targets').innerHTML = targets
            .map((target) =>
                window.CheckStockUI.render('stock/inbound-supplies/connection-cards-2', {
                    content:
                        target.status === 'ok' && !target.stale
                            ? 'is-ok'
                            : trouble.includes(target)
                              ? 'is-error'
                              : '',
                    store_name: target.store_name,
                    marketplace_name: target.marketplace_name,
                    content_2: statuses[target.status] || target.status,
                    content_3: target.stale && target.last_success ? ' · данные устарели' : '',
                    last_success: date(target.last_success),
                    content_4: target.error
                        ? window.CheckStockUI.render('stock/inbound-supplies/connection-cards', {
                              error: target.error,
                          })
                        : '',
                }),
            )
            .join('');
    }

    function stageCards(rows) {
        const categories = [
            ['', 'Все незавершённые'],
            ['planned', labels.planned],
            ['transit', labels.transit],
            ['acceptance', labels.acceptance],
            ['placement', labels.placement],
            ['discrepancy', 'Расхождения / проверка'],
        ];
        el('stages').innerHTML = categories
            .map(([value, label]) => {
                const selected = rows.filter(
                    ({ supply }) =>
                        !['completed', 'cancelled'].includes(supply.stage) &&
                        (!value ||
                            (value === 'discrepancy'
                                ? ['discrepancy', 'unknown'].includes(supply.stage) || supply.unavailable
                                : supply.stage === value && !supply.unavailable)),
                );
                const trustworthy = selected.filter(
                    ({ supply, target }) =>
                        !supply.unavailable &&
                        !target.stale &&
                        !['error', 'not_configured'].includes(target.status),
                );
                const hasStale = trustworthy.length !== selected.length;
                const units = trustworthy.reduce((sum, { supply }) => sum + supply.quantity, 0);
                return window.CheckStockUI.render('stock/inbound-supplies/stage-cards', {
                    content: stage === value ? 'is-active' : '',
                    value: value,
                    content_2: stage === value,
                    label: label,
                    length: number(selected.length),
                    units: number(units),
                    content_3: hasStale ? ' · есть старые данные' : '',
                });
            })
            .join('');
    }

    function itemRemaining(supply, item) {
        if (supply.unavailable || ['unknown', 'discrepancy'].includes(supply.stage)) return null;
        if (['completed', 'cancelled'].includes(supply.stage)) return 0;
        if (supply.stage === 'planned') return item.quantity;
        if (supply.stage === 'transit') return Math.max(item.quantity - (item.ready_quantity || 0), 0);
        if (supply.stage === 'placement')
            return item.ready_quantity == null ? null : Math.max(item.quantity - item.ready_quantity, 0);
        if (item.accepted_quantity == null) return null;
        return Math.max(item.quantity - item.accepted_quantity, 0);
    }

    function cardBody(target, supply) {
        const wb = target.marketplace === 'WB';
        const warning =
            supply.warning ||
            (target.stale || ['error', 'not_configured'].includes(target.status)
                ? 'Показаны последние полученные данные. Проверьте состояние подключения.'
                : '');
        return window.CheckStockUI.render('stock/inbound-supplies/card-body-8', {
            content: warning
                ? window.CheckStockUI.render('stock/inbound-supplies/card-body', { warning: warning })
                : '',
            content_2: supply.note
                ? window.CheckStockUI.render('stock/inbound-supplies/card-body-2', {
                      note: supply.note,
                  })
                : '',
            supply_id: supply.supply_id,
            content_3: supply.campaign_name ? ` · ${escape(supply.campaign_name)}` : '',
            checked_at: date(supply.checked_at),
            content_4: wb ? window.CheckStockUI.render('stock/inbound-supplies/card-body-3') : '',
            content_5:
                supply.items
                    .map((item) =>
                        window.CheckStockUI.render('stock/inbound-supplies/card-body-6', {
                            content: item.article || item.sku || '—',
                            content_2: item.vendor_code
                                ? window.CheckStockUI.render('stock/inbound-supplies/card-body-4', {
                                      vendor_code: item.vendor_code,
                                  })
                                : '',
                            content_3: item.barcode || '—',
                            content_4: item.name || '—',
                            quantity: number(item.quantity),
                            accepted_quantity: number(item.accepted_quantity),
                            content_5: wb
                                ? window.CheckStockUI.render('stock/inbound-supplies/card-body-5', {
                                      ready_quantity: number(item.ready_quantity),
                                  })
                                : '',
                            content_6: number(itemRemaining(supply, item)),
                            shortage_quantity: number(item.shortage_quantity),
                            surplus_quantity: number(item.surplus_quantity),
                            defect_quantity: number(item.defect_quantity),
                        }),
                    )
                    .join('') ||
                window.CheckStockUI.render('stock/inbound-supplies/card-body-7', { content: wb ? 10 : 9 }),
        });
    }

    function card({ target, supply }) {
        const key = cardKey(target, supply);
        const isOpen = openCards.has(key);
        const stale =
            supply.unavailable || target.stale || ['error', 'not_configured'].includes(target.status);
        return window.CheckStockUI.render('stock/inbound-supplies/card-2', {
            key: key,
            content: isOpen ? ' open' : '',
            store_name: target.store_name,
            marketplace_name: target.marketplace_name,
            content_2: supply.number || supply.supply_id,
            content_3:
                supply.order_id && supply.supply_id !== supply.order_id
                    ? ` · поставка ${escape(supply.supply_id)}`
                    : '',
            content_4: supply.warehouse || 'Склад не указан',
            content_5: supply.transit_warehouse
                ? window.CheckStockUI.render('stock/inbound-supplies/card', {
                      transit_warehouse: supply.transit_warehouse,
                  })
                : '',
            planned_at: date(supply.planned_at),
            content_6: supply.unavailable ? 'unknown' : supply.stage,
            content_7: supply.unavailable ? 'Требует проверки' : labels[supply.stage],
            status_label: supply.status_label,
            content_8: stale ? ' · старые данные' : '',
            content_9: supply.warning ? ' · есть замечание' : '',
            quantity: number(supply.quantity),
            accepted_quantity: number(supply.accepted_quantity),
            remaining_quantity: number(supply.remaining_quantity),
            content_10: isOpen ? cardBody(target, supply) : '',
        });
    }

    function render() {
        const term = el('search').value.trim().toLocaleLowerCase('ru');
        const rows = allSupplies().filter(({ supply }) => matches(supply, term));
        stageCards(rows);
        const visible = rows
            .filter(({ supply }) => {
                if (!el('history').checked && ['completed', 'cancelled'].includes(supply.stage)) return false;
                if (stage === 'discrepancy')
                    return ['discrepancy', 'unknown'].includes(supply.stage) || supply.unavailable;
                return !stage || (supply.stage === stage && !supply.unavailable);
            })
            .sort(
                (a, b) =>
                    stageOrder.indexOf(a.supply.stage) - stageOrder.indexOf(b.supply.stage) ||
                    (b.supply.planned_at || b.supply.updated_at).localeCompare(
                        a.supply.planned_at || a.supply.updated_at,
                    ),
            );
        el('count').textContent = `Поставок: ${number(visible.length)}`;
        const loaded = targets.some((target) => target.last_success);
        el('list').innerHTML =
            visible.slice(0, visibleCount).map(card).join('') ||
            window.CheckStockUI.render('stock/inbound-supplies/render', {
                content: loaded ? 'По выбранным условиям поставок нет.' : 'Данные поставок ещё не получены.',
                content_2: loaded
                    ? 'Измените фильтры или включите завершённые поставки.'
                    : 'Обновите поставки или дождитесь автоматической загрузки. Состояние каждого магазина показано выше.',
            });
        el('more').hidden = visible.length <= visibleCount;
    }

    function setMarketplaceOptions() {
        const selected = el('marketplace').value;
        const allowed = new Set(
            scopes.filter(([store]) => !el('store').value || store === el('store').value).map(([, mp]) => mp),
        );
        el('marketplace').replaceChildren(
            ...marketplaceOptions
                .filter((option) => !option.value || allowed.has(option.value))
                .map((option) => new Option(option.text, option.value)),
        );
        el('marketplace').value = allowed.has(selected) ? selected : '';
    }

    async function load() {
        const sequence = ++requestSequence;
        loading = true;
        clearTimeout(autoTimer);
        try {
            const response = await fetch(`/stock/inbound/data?${query()}`, {
                headers: { Accept: 'application/json' },
                cache: 'no-store',
            });
            const data = await response.json();
            if (sequence !== requestSequence) return;
            if (!response.ok || !data.ok)
                throw new Error(data.detail || data.error || 'Не удалось загрузить поставки');
            targets = data.targets;
            connectionCards();
            render();
            const running = targets.filter((target) => target.status === 'running').length;
            const issues = targets.filter(
                (target) =>
                    target.status === 'error' ||
                    target.status === 'partial' ||
                    (target.stale && target.last_success),
            ).length;
            notice(
                running
                    ? `Обновление выполняется для ${running} подключений. Данные появятся автоматически.`
                    : issues
                      ? `У ${issues} подключений есть замечания. Подробности — в состоянии подключений.`
                      : 'Данные обновляются автоматически. Даты показаны по московскому времени.',
                Boolean(issues),
            );
        } catch (error) {
            if (sequence === requestSequence)
                notice(error.message || 'Не удалось связаться с сервером', true);
        } finally {
            if (sequence === requestSequence) {
                loading = false;
                autoTimer = setTimeout(
                    () => {
                        if (!document.hidden) load();
                    },
                    targets.some((target) => target.status === 'running') ? 5000 : 30000,
                );
            }
        }
    }

    el('refresh').addEventListener('click', async () => {
        if (refreshing) return;
        refreshing = true;
        el('refresh').disabled = true;
        notice('Запускаю обновление выбранных магазинов…');
        try {
            const response = await fetch('/stock/inbound/sync', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ store: el('store').value, marketplace: el('marketplace').value }),
            });
            const data = await response.json();
            if (!response.ok || !data.ok)
                throw new Error(data.detail || data.error || 'Не удалось запустить обновление');
            await load();
            if (!data.started) notice('Обновление уже выполняется или запускалось менее минуты назад.');
        } catch (error) {
            notice(error.message, true);
        } finally {
            refreshing = false;
            el('refresh').disabled = false;
        }
    });
    function changeScope() {
        visibleCount = 40;
        openCards = new Set();
        targets = [];
        render();
        notice('Загрузка выбранных магазинов…');
        history.replaceState(null, '', `/stock/inbound?${query()}`);
        load();
    }
    el('store').addEventListener('change', () => {
        setMarketplaceOptions();
        changeScope();
    });
    el('marketplace').addEventListener('change', changeScope);
    el('search').addEventListener('input', () => {
        visibleCount = 40;
        render();
    });
    el('history').addEventListener('change', () => {
        stage = '';
        visibleCount = 40;
        render();
    });
    el('stages').addEventListener('click', (event) => {
        const button = event.target.closest('[data-stage]');
        if (!button) return;
        stage = button.dataset.stage;
        el('history').checked = false;
        visibleCount = 40;
        render();
    });
    el('more').addEventListener('click', () => {
        visibleCount += 40;
        render();
    });
    el('list').addEventListener(
        'toggle',
        (event) => {
            const details = event.target;
            if (!details.matches('[data-card-key]')) return;
            if (details.open) {
                openCards.add(details.dataset.cardKey);
                if (!details.querySelector('.inbound-card-body')) {
                    const row = allSupplies().find(
                        ({ target, supply }) => cardKey(target, supply) === details.dataset.cardKey,
                    );
                    if (row) details.insertAdjacentHTML('beforeend', cardBody(row.target, row.supply));
                }
            } else openCards.delete(details.dataset.cardKey);
        },
        true,
    );
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && !loading) load();
    });
    setMarketplaceOptions();
    load();
})();
