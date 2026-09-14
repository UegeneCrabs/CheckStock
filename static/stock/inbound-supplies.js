(() => {
    'use strict';
    const root = document.querySelector('[data-inbound-page]');
    if (!root) return;
    const el = name => root.querySelector(`[data-inbound-${name}]`);
    const scopes = JSON.parse(root.dataset.scopes || '[]');
    const marketplaceOptions = Array.from(el('marketplace').options).map(option => ({ value: option.value, text: option.text }));
    const labels = { planned: 'Запланировано', transit: 'В пути / транзит', acceptance: 'На приёмке', placement: 'Принято, размещается', discrepancy: 'Расхождения / спор', completed: 'Завершено', cancelled: 'Отменено / отказ', unknown: 'Требует проверки' };
    const statuses = { never: 'Ещё не загружалось', running: 'Обновляется', ok: 'Обновлено', partial: 'Есть замечания', error: 'Ошибка обновления', not_configured: 'Не подключено' };
    const stageOrder = ['planned', 'transit', 'acceptance', 'placement', 'discrepancy', 'unknown', 'completed', 'cancelled'];
    const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
    const number = value => value == null ? '—' : Number(value).toLocaleString('ru-RU');
    const date = value => {
        if (!value) return '—';
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
    };
    let targets = [];
    let stage = '';
    let visibleCount = 40;
    let requestSequence = 0;
    let loading = false;
    let refreshing = false;
    let autoTimer;
    let openCards = new Set();
    const notice = (text, error = false) => { el('notice').textContent = text; el('notice').classList.toggle('is-error', error); };
    const query = () => new URLSearchParams({ store: el('store').value, mp: el('marketplace').value });
    const cardKey = (target, supply) => `${target.store_slug}|${target.marketplace}|${supply.key}`;
    const allSupplies = () => targets.flatMap(target => target.supplies.map(supply => ({ target, supply })));
    const matches = (supply, term) => !term || [supply.number, supply.supply_id, supply.warehouse, supply.campaign_name,
        ...supply.items.flatMap(item => [item.article, item.barcode, item.name, item.vendor_code, item.sku])].some(value => String(value || '').toLocaleLowerCase('ru').includes(term));

    function connectionCards() {
        const trouble = targets.filter(target => target.status === 'error' || target.status === 'partial' || (target.stale && target.last_success));
        const running = targets.filter(target => target.status === 'running').length;
        el('connection-summary').textContent = `Подключения: ${targets.length}${running ? ` · обновляется ${running}` : ''}${trouble.length ? ` · требуют внимания ${trouble.length}` : ''}`;
        el('targets').innerHTML = targets.map(target => `<div class="inbound-connection ${target.status === 'ok' && !target.stale ? 'is-ok' : trouble.includes(target) ? 'is-error' : ''}">
            <strong>${escape(target.store_name)} · ${escape(target.marketplace_name)}</strong>
            <small>${escape(statuses[target.status] || target.status)}${target.stale && target.last_success ? ' · данные устарели' : ''}</small>
            <small>Успешная загрузка: ${date(target.last_success)} МСК</small>
            ${target.error ? `<p>${escape(target.error)}</p>` : ''}</div>`).join('');
    }

    function stageCards(rows) {
        const categories = [['', 'Все незавершённые'], ['planned', labels.planned], ['transit', labels.transit],
            ['acceptance', labels.acceptance], ['placement', labels.placement], ['discrepancy', 'Расхождения / проверка']];
        el('stages').innerHTML = categories.map(([value, label]) => {
            const selected = rows.filter(({ supply }) => !['completed', 'cancelled'].includes(supply.stage) && (
                !value || (value === 'discrepancy' ? ['discrepancy', 'unknown'].includes(supply.stage) || supply.unavailable : supply.stage === value && !supply.unavailable)
            ));
            const trustworthy = selected.filter(({ supply, target }) => !supply.unavailable && !target.stale && !['error', 'not_configured'].includes(target.status));
            const hasStale = trustworthy.length !== selected.length;
            const units = trustworthy.reduce((sum, { supply }) => sum + supply.quantity, 0);
            return `<button class="inbound-stage ${stage === value ? 'is-active' : ''}" type="button" data-stage="${value}" aria-pressed="${stage === value}">
                <span>${label}</span><strong>${number(selected.length)}</strong><small>${number(units)} шт. по заявкам${hasStale ? ' · есть старые данные' : ''}</small></button>`;
        }).join('');
    }

    function itemRemaining(supply, item) {
        if (supply.unavailable || ['unknown', 'discrepancy'].includes(supply.stage)) return null;
        if (['completed', 'cancelled'].includes(supply.stage)) return 0;
        if (supply.stage === 'planned') return item.quantity;
        if (supply.stage === 'transit') return Math.max(item.quantity - (item.ready_quantity || 0), 0);
        if (supply.stage === 'placement') return item.ready_quantity == null ? null : Math.max(item.quantity - item.ready_quantity, 0);
        if (item.accepted_quantity == null) return null;
        return Math.max(item.quantity - item.accepted_quantity, 0);
    }

    function cardBody(target, supply) {
        const wb = target.marketplace === 'WB';
        const warning = supply.warning || (target.stale || ['error', 'not_configured'].includes(target.status) ? 'Показаны последние полученные данные. Проверьте состояние подключения.' : '');
        return `<div class="inbound-card-body">
            ${warning ? `<p class="inbound-card-warning">${escape(warning)}</p>` : ''}
            ${supply.note ? `<p class="inbound-card-note">${escape(supply.note)}</p>` : ''}
            <p class="inbound-card-note">Поставка ${escape(supply.supply_id)}${supply.campaign_name ? ` · ${escape(supply.campaign_name)}` : ''} · Проверено: ${date(supply.checked_at)} МСК. «—» означает, что количество не подтверждено площадкой.</p>
            <div class="inbound-table-scroll"><table class="inbound-table"><thead><tr>
                <th>Артикул</th><th>Баркод</th><th>Название</th><th class="inbound-num">В заявке</th><th class="inbound-num">Принято</th>
                ${wb ? '<th class="inbound-num">Готово к продаже по поставке</th>' : ''}<th class="inbound-num">Не завершено</th>
                <th class="inbound-num">Недостача</th><th class="inbound-num">Излишек</th><th class="inbound-num">Брак</th>
            </tr></thead><tbody>${supply.items.map(item => `<tr>
                <td>${escape(item.article || item.sku || '—')}${item.vendor_code ? `<small>${escape(item.vendor_code)}</small>` : ''}</td>
                <td>${escape(item.barcode || '—')}</td><td>${escape(item.name || '—')}</td>
                <td class="inbound-num">${number(item.quantity)}</td><td class="inbound-num">${number(item.accepted_quantity)}</td>
                ${wb ? `<td class="inbound-num">${number(item.ready_quantity)}</td>` : ''}<td class="inbound-num">${number(itemRemaining(supply, item))}</td>
                <td class="inbound-num">${number(item.shortage_quantity)}</td><td class="inbound-num">${number(item.surplus_quantity)}</td><td class="inbound-num">${number(item.defect_quantity)}</td>
            </tr>`).join('') || `<tr><td colspan="${wb ? 10 : 9}">В составе заявки пока нет товаров.</td></tr>`}</tbody></table></div>
        </div>`;
    }

    function card({ target, supply }) {
        const key = cardKey(target, supply);
        const isOpen = openCards.has(key);
        const stale = supply.unavailable || target.stale || ['error', 'not_configured'].includes(target.status);
        return `<details class="inbound-card" data-card-key="${escape(key)}"${isOpen ? ' open' : ''}>
            <summary><div class="inbound-card-title">${escape(target.store_name)} · ${escape(target.marketplace_name)}
                <small>№ ${escape(supply.number || supply.supply_id)}${supply.order_id && supply.supply_id !== supply.order_id ? ` · поставка ${escape(supply.supply_id)}` : ''}</small></div>
                <div class="inbound-card-route">${escape(supply.warehouse || 'Склад не указан')}${supply.transit_warehouse ? `<small>Через: ${escape(supply.transit_warehouse)}</small>` : ''}
                    <small>План: ${date(supply.planned_at)} МСК</small></div>
                <div><span class="inbound-badge inbound-badge--${supply.unavailable ? 'unknown' : supply.stage}">${escape(supply.unavailable ? 'Требует проверки' : labels[supply.stage])}</span>
                    <small>${escape(supply.status_label)}${stale ? ' · старые данные' : ''}${supply.warning ? ' · есть замечание' : ''}</small></div>
                <div class="inbound-card-numbers"><strong>${number(supply.quantity)}</strong> шт. в заявке
                    <small>Принято: ${number(supply.accepted_quantity)} · не завершено: ${number(supply.remaining_quantity)}</small></div>
                <span class="inbound-chevron" aria-hidden="true">›</span></summary>${isOpen ? cardBody(target, supply) : ''}</details>`;
    }

    function render() {
        const term = el('search').value.trim().toLocaleLowerCase('ru');
        const rows = allSupplies().filter(({ supply }) => matches(supply, term));
        stageCards(rows);
        const visible = rows.filter(({ supply }) => {
            if (!el('history').checked && ['completed', 'cancelled'].includes(supply.stage)) return false;
            if (stage === 'discrepancy') return ['discrepancy', 'unknown'].includes(supply.stage) || supply.unavailable;
            return !stage || (supply.stage === stage && !supply.unavailable);
        }).sort((a, b) => stageOrder.indexOf(a.supply.stage) - stageOrder.indexOf(b.supply.stage)
            || (b.supply.planned_at || b.supply.updated_at).localeCompare(a.supply.planned_at || a.supply.updated_at));
        el('count').textContent = `Поставок: ${number(visible.length)}`;
        const loaded = targets.some(target => target.last_success);
        el('list').innerHTML = visible.slice(0, visibleCount).map(card).join('') || `<div class="inbound-empty">${loaded ? 'По выбранным условиям поставок нет.' : 'Данные поставок ещё не получены.'}<br>${loaded ? 'Измените фильтры или включите завершённые поставки.' : 'Обновите поставки или дождитесь автоматической загрузки. Состояние каждого магазина показано выше.'}</div>`;
        el('more').hidden = visible.length <= visibleCount;
    }

    function setMarketplaceOptions() {
        const selected = el('marketplace').value;
        const allowed = new Set(scopes.filter(([store]) => !el('store').value || store === el('store').value).map(([, mp]) => mp));
        el('marketplace').replaceChildren(...marketplaceOptions.filter(option => !option.value || allowed.has(option.value)).map(option => new Option(option.text, option.value)));
        el('marketplace').value = allowed.has(selected) ? selected : '';
    }

    async function load() {
        const sequence = ++requestSequence;
        loading = true;
        clearTimeout(autoTimer);
        try {
            const response = await fetch(`/stock/inbound/data?${query()}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
            const data = await response.json();
            if (sequence !== requestSequence) return;
            if (!response.ok || !data.ok) throw new Error(data.detail || data.error || 'Не удалось загрузить поставки');
            targets = data.targets;
            connectionCards();
            render();
            const running = targets.filter(target => target.status === 'running').length;
            const issues = targets.filter(target => target.status === 'error' || target.status === 'partial' || (target.stale && target.last_success)).length;
            notice(running ? `Обновление выполняется для ${running} подключений. Данные появятся автоматически.` : issues
                ? `У ${issues} подключений есть замечания. Подробности — в состоянии подключений.` : 'Данные обновляются автоматически. Даты показаны по московскому времени.', Boolean(issues));
        } catch (error) {
            if (sequence === requestSequence) notice(error.message || 'Не удалось связаться с сервером', true);
        } finally {
            if (sequence === requestSequence) {
                loading = false;
                autoTimer = setTimeout(() => { if (!document.hidden) load(); }, targets.some(target => target.status === 'running') ? 5000 : 30000);
            }
        }
    }

    el('refresh').addEventListener('click', async () => {
        if (refreshing) return;
        refreshing = true;
        el('refresh').disabled = true;
        notice('Запускаю обновление выбранных магазинов…');
        try {
            const response = await fetch('/stock/inbound/sync', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ store: el('store').value, marketplace: el('marketplace').value }) });
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.detail || data.error || 'Не удалось запустить обновление');
            await load();
            if (!data.started) notice('Обновление уже выполняется или запускалось менее минуты назад.');
        } catch (error) { notice(error.message, true); }
        finally { refreshing = false; el('refresh').disabled = false; }
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
    el('store').addEventListener('change', () => { setMarketplaceOptions(); changeScope(); });
    el('marketplace').addEventListener('change', changeScope);
    el('search').addEventListener('input', () => { visibleCount = 40; render(); });
    el('history').addEventListener('change', () => { stage = ''; visibleCount = 40; render(); });
    el('stages').addEventListener('click', event => {
        const button = event.target.closest('[data-stage]');
        if (!button) return;
        stage = button.dataset.stage;
        el('history').checked = false;
        visibleCount = 40;
        render();
    });
    el('more').addEventListener('click', () => { visibleCount += 40; render(); });
    el('list').addEventListener('toggle', event => {
        const details = event.target;
        if (!details.matches('[data-card-key]')) return;
        if (details.open) {
            openCards.add(details.dataset.cardKey);
            if (!details.querySelector('.inbound-card-body')) {
                const row = allSupplies().find(({ target, supply }) => cardKey(target, supply) === details.dataset.cardKey);
                if (row) details.insertAdjacentHTML('beforeend', cardBody(row.target, row.supply));
            }
        } else openCards.delete(details.dataset.cardKey);
    }, true);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !loading) load(); });
    setMarketplaceOptions();
    load();
})();
