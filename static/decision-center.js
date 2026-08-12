(() => {
    const root = document.querySelector("[data-decision-center]");
    if (!root) return;

    const $ = (selector) => root.querySelector(selector);
    const storeSelect = $("[data-dc-store]");
    const syncButton = $("[data-dc-sync]");
    const loading = $("[data-dc-loading]");
    const errorBox = $("[data-dc-error]");
    const queue = $("[data-dc-queue]");
    const moreButton = $("[data-dc-more]");
    const state = { data: null, domain: "Все", status: "active", limit: 12, loading: false };

    const escapeHtml = (value) => String(value ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    const number = (value, digits = 0) => new Intl.NumberFormat("ru-RU", {
        maximumFractionDigits: digits,
    }).format(Number(value || 0));
    const money = (value, compact = false) => new Intl.NumberFormat("ru-RU", {
        style: "currency", currency: "RUB", maximumFractionDigits: 0,
        notation: compact && Math.abs(Number(value || 0)) >= 100000 ? "compact" : "standard",
    }).format(Number(value || 0));
    const percent = (value, digits = 1) => `${number(Number(value || 0) * 100, digits)}%`;
    const formatDate = (value) => {
        if (!value) return "нет свежей аналитики";
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? "нет свежей аналитики" : parsed.toLocaleString("ru-RU", {
            day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
        });
    };

    const setLoading = (value) => {
        state.loading = value;
        loading.hidden = !value;
        syncButton.disabled = value;
        syncButton.classList.toggle("is-loading", value);
    };

    const showError = (message) => {
        errorBox.textContent = message;
        errorBox.hidden = !message;
    };

    const empty = (title, text) => `
        <div class="dc-empty"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(text)}</span></div>`;

    function renderStatus() {
        const sync = state.data.sync || [];
        const errors = sync.filter((item) => item.status === "error");
        const latest = state.data.meta.lastAnalyticsAt;
        const dot = $("[data-dc-live-dot]");
        dot.classList.toggle("is-ready", sync.length > 0 && !errors.length);
        dot.classList.toggle("is-warning", !sync.length || errors.length > 0);
        if (errors.length) {
            $("[data-dc-status]").textContent = `${errors.length} источн. требуют внимания · локальные данные доступны`;
        } else if (sync.length) {
            $("[data-dc-status]").textContent = `Wildberries подключён · аналитика ${formatDate(latest)}`;
        } else {
            $("[data-dc-status]").textContent = "Локальные заказы и остатки готовы · нажмите «Обновить WB» для полной воронки";
        }
    }

    function renderKpis() {
        const summary = state.data.summary;
        const cards = [
            ["Здоровье портфеля", number(summary.averageHealth), "из 100", "is-accent"],
            ["Потенциал прибыли", money(summary.potentialProfit, true), "по активным решениям", "is-positive"],
            ["Решений сейчас", number(summary.decisions), `${number(summary.critical)} критичных`, summary.critical ? "is-critical" : ""],
            ["Уже в работе", number(summary.inProgress), "сохранены в базе", "is-accent"],
            ["Товаров с риском", number(summary.productsAtRisk), "сток или здоровье", summary.productsAtRisk ? "is-critical" : ""],
            ["Sell-through", percent(summary.sellThrough, 0), "за последние 28 дней", ""],
        ];
        $("[data-dc-kpis]").innerHTML = cards.map(([label, value, note, tone]) => `
            <div class="dc-kpi ${tone}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></div>
        `).join("");
        $("[data-dc-potential-title]").textContent = summary.potentialProfit > 0
            ? `Можно забрать ${money(summary.potentialProfit, true)} дополнительной прибыли`
            : "Критичных потерь прибыли сейчас не обнаружено";
    }

    function renderFunnel() {
        $("[data-dc-funnel]").innerHTML = state.data.funnel.map((item, index) => `
            <div class="dc-funnel-step">
                <span>${escapeHtml(item.label)}</span>
                <strong>${number(item.value)}</strong>
                <small>${index === 0 ? "весь рассчитанный спрос" : `${percent(item.rate, 1)} от предыдущего этапа`}</small>
                <div class="dc-funnel-rate"><i style="width:${Math.max(3, Math.min(100, Number(item.rate || 0) * 100))}%"></i></div>
            </div>
        `).join("");
    }

    function renderFilters() {
        const domains = ["Все", ...new Set(state.data.opportunities.map((item) => item.domain))];
        $("[data-dc-domain-filters]").innerHTML = domains.map((domain) => `
            <button class="dc-filter-button ${state.domain === domain ? "is-active" : ""}" type="button" data-dc-domain="${escapeHtml(domain)}">${escapeHtml(domain)}</button>
        `).join("");
        const statuses = [["active", "Активные"], ["in_progress", "В работе"], ["completed", "Готово"], ["all", "Все"]];
        $("[data-dc-status-filters]").innerHTML = statuses.map(([value, label]) => `
            <button class="dc-filter-button ${state.status === value ? "is-active" : ""}" type="button" data-dc-status-filter="${value}">${label}</button>
        `).join("");
    }

    const statusLabel = (status) => ({ new: "Новое", in_progress: "В работе", completed: "Готово" }[status] || status);

    function decisionCard(item, rank) {
        const evidence = (item.evidence || []).map((fact) => `
            <span class="dc-evidence-item ${fact.tone !== "neutral" ? `is-${escapeHtml(fact.tone)}` : ""}">
                <span>${escapeHtml(fact.label)}</span><strong>${escapeHtml(fact.value)}</strong>
            </span>
        `).join("");
        const impact = item.expectedProfit > 0 ? `+${money(item.expectedProfit, true)}` : money(0);
        const statusClass = item.status === "in_progress" ? "is-progress" : item.status === "completed" ? "is-completed" : "";
        const actionButtons = item.status === "new"
            ? `<button class="dc-action-button is-primary" type="button" data-dc-set-status="in_progress">Взять в работу</button>`
            : item.status === "in_progress"
                ? `<button class="dc-action-button is-success" type="button" data-dc-set-status="completed">Отметить готовым</button><button class="dc-action-button" type="button" data-dc-set-status="new">Вернуть в новые</button>`
                : `<button class="dc-action-button" type="button" data-dc-set-status="in_progress">Открыть снова</button>`;
        return `
            <article class="dc-decision ${item.severity === "critical" ? "is-critical" : ""} ${item.status === "completed" ? "is-completed" : ""}" data-dc-decision="${escapeHtml(item.fingerprint)}">
                <div class="dc-decision-rank">${String(rank).padStart(2, "0")}</div>
                <div class="dc-decision-main">
                    <div class="dc-decision-meta">
                        <span class="dc-domain">${escapeHtml(item.domain)}</span>
                        <span class="dc-severity ${item.severity === "critical" ? "is-critical" : ""}">${item.severity === "critical" ? "Критично" : item.severity === "high" ? "Высокий" : "Средний"}</span>
                        <span class="dc-state-pill ${statusClass}">${escapeHtml(statusLabel(item.status))}</span>
                    </div>
                    <h4>${escapeHtml(item.title)}</h4>
                    <p class="dc-decision-product">${escapeHtml(item.product)} · арт. ${escapeHtml(item.article)} · ${escapeHtml(item.storeName)}</p>
                    <p class="dc-decision-summary">${escapeHtml(item.summary)}</p>
                    <div class="dc-evidence">${evidence}</div>
                </div>
                <div class="dc-decision-impact">
                    <span>потенциал прибыли</span><strong>${escapeHtml(impact)}</strong>
                    <span class="dc-score">${number(item.score)}/100</span>
                </div>
                <div class="dc-decision-details">
                    <button class="dc-details-toggle" type="button" data-dc-details aria-expanded="false">Показать план решения</button>
                    <div class="dc-details-body" hidden>
                        <div class="dc-detail dc-detail--wide"><span>Действие</span><p>${escapeHtml(item.action)}</p></div>
                        <div class="dc-detail"><span>Метрика</span><strong>${escapeHtml(item.primaryMetric)}</strong></div>
                        <div class="dc-detail"><span>Сейчас</span><strong>${escapeHtml(item.baseline)}</strong></div>
                        <div class="dc-detail"><span>Цель</span><strong>${escapeHtml(item.target)}</strong></div>
                        <div class="dc-detail"><span>Горизонт</span><strong>${number(item.horizonDays)} дн.</strong></div>
                        <div class="dc-detail dc-detail--wide"><span>Ограничение</span><p>${escapeHtml(item.guardrail)}</p></div>
                        <div class="dc-detail"><span>Уверенность</span><strong>${percent(item.confidence, 0)}</strong></div>
                    </div>
                    <div class="dc-decision-actions">${actionButtons}</div>
                </div>
            </article>
        `;
    }

    function filteredOpportunities() {
        return state.data.opportunities.filter((item) => {
            const domainOk = state.domain === "Все" || item.domain === state.domain;
            const statusOk = state.status === "all"
                || (state.status === "active" && item.status !== "completed")
                || item.status === state.status;
            return domainOk && statusOk;
        });
    }

    function renderQueue() {
        const filtered = filteredOpportunities();
        const visible = filtered.slice(0, state.limit);
        $("[data-dc-count]").textContent = String(filtered.length);
        queue.innerHTML = visible.length
            ? visible.map((item, index) => decisionCard(item, index + 1)).join("")
            : empty("Решений в этом фильтре нет", "Выберите другую зону или статус — данные при этом не теряются.");
        moreButton.hidden = filtered.length <= state.limit;
    }

    function renderReallocations() {
        const rows = state.data.reallocations || [];
        $("[data-dc-reallocations]").innerHTML = rows.length ? rows.map((item) => `
            <div class="dc-reallocation">
                <div class="dc-reallocation-flow">
                    <div class="dc-reallocation-product"><span>Снять · ДРР ${percent(item.fromDrr, 1)}</span><strong title="${escapeHtml(item.from)}">${escapeHtml(item.from)}</strong></div>
                    <span class="dc-reallocation-arrow">→</span>
                    <div class="dc-reallocation-product"><span>Добавить · CVR ${percent(item.toConversion, 1)}</span><strong title="${escapeHtml(item.to)}">${escapeHtml(item.to)}</strong></div>
                </div>
                <div class="dc-reallocation-foot"><span>${escapeHtml(item.fromStore)} → ${escapeHtml(item.toStore)}</span><strong>${money(item.dailyBudget)}/день</strong></div>
            </div>
        `).join("") : empty("Перенос бюджета не требуется", "Нужны товары одновременно с рекламным перерасходом и подтверждённой сильной конверсией.");
    }

    function portfolioAction(item) {
        if (item.stockDays < 10 && item.weeklyOrders > .5) return "Защитить сток";
        if (item.stockDays > 90) return "Высвободить";
        if (item.drr > .3) return "Снизить ДРР";
        if (item.growth > 20) return "Масштабировать";
        if (item.health < 60) return "Починить";
        return "Наблюдать";
    }

    function renderPortfolio() {
        const rows = state.data.portfolio || [];
        $("[data-dc-portfolio]").innerHTML = rows.length ? rows.map((item) => {
            const initials = String(item.name || item.article).slice(0, 2).toUpperCase();
            const avatar = item.imageUrl
                ? `<img src="${escapeHtml(item.imageUrl)}" alt="" loading="lazy">`
                : escapeHtml(initials);
            const healthClass = item.health >= 72 ? "is-good" : item.health < 50 ? "is-risk" : "";
            const growthClass = item.growth > 0 ? "dc-positive" : item.growth < 0 ? "dc-negative" : "";
            return `<tr>
                <td><div class="dc-product-cell"><span class="dc-product-avatar">${avatar}</span><span class="dc-product-copy"><strong title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</strong><small>арт. ${escapeHtml(item.article)}</small></span></div></td>
                <td>${escapeHtml(item.storeName)}</td><td><strong>${escapeHtml(portfolioAction(item))}</strong></td>
                <td><span class="dc-health ${healthClass}"><i></i>${number(item.health)}</span></td>
                <td class="${growthClass}">${item.growth > 0 ? "+" : ""}${number(item.growth, 0)}%</td>
                <td>${percent(item.buyoutRate, 1)}</td><td>${number(item.stockDays, 0)} дн.</td><td>${percent(item.drr, 1)}</td>
            </tr>`;
        }).join("") : `<tr><td colspan="8">${empty("Товаров пока нет", "Сначала синхронизируйте каталог Wildberries.")}</td></tr>`;
    }

    function renderPlaybooks() {
        $("[data-dc-playbooks]").innerHTML = (state.data.playbooks || []).map((item) => `
            <div class="dc-playbook"><span class="dc-playbook-icon">${escapeHtml(item.icon)}</span><div class="dc-playbook-copy"><h4>${escapeHtml(item.title)}</h4><p><strong>${escapeHtml(item.trigger)}</strong><br>${escapeHtml(item.action)}</p><small>Смотреть: ${escapeHtml(item.metric)}</small></div></div>
        `).join("");
    }

    function renderAll() {
        renderStatus(); renderKpis(); renderFunnel(); renderFilters(); renderQueue();
        renderReallocations(); renderPortfolio(); renderPlaybooks();
    }

    async function request(url, options = {}) {
        const response = await fetch(url, {
            headers: { "Accept": "application/json", "X-Requested-With": "fetch", ...(options.headers || {}) },
            ...options,
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.ok === false) throw new Error(body.error || body.detail || `Ошибка ${response.status}`);
        return body;
    }

    async function load() {
        setLoading(true); showError("");
        try {
            const params = new URLSearchParams();
            if (storeSelect.value) params.set("store", storeSelect.value);
            state.data = await request(`/api/decision-center?${params.toString()}`);
            state.limit = 12;
            renderAll();
        } catch (error) {
            showError(error.message || "Не удалось загрузить Центр решений");
        } finally {
            setLoading(false);
        }
    }

    async function sync() {
        setLoading(true); showError("");
        $("[data-dc-sync-label]").textContent = "Обновляем…";
        try {
            await request("/api/decision-center/sync", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ store: storeSelect.value || "" }),
            });
            await load();
        } catch (error) {
            showError(error.message || "Wildberries не ответил");
        } finally {
            $("[data-dc-sync-label]").textContent = "Обновить WB";
            setLoading(false);
        }
    }

    async function setStatus(card, status) {
        const button = card.querySelector(`[data-dc-set-status="${status}"]`);
        if (button) button.disabled = true;
        try {
            await request("/api/decision-center/status", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ fingerprint: card.dataset.dcDecision, status }),
            });
            const item = state.data.opportunities.find((row) => row.fingerprint === card.dataset.dcDecision);
            if (item) item.status = status;
            state.data.summary.inProgress = state.data.opportunities.filter((row) => row.status === "in_progress").length;
            state.data.summary.completed = state.data.opportunities.filter((row) => row.status === "completed").length;
            state.data.summary.decisions = state.data.opportunities.filter((row) => row.status !== "completed").length;
            renderKpis(); renderQueue();
        } catch (error) {
            showError(error.message || "Не удалось сохранить статус");
            if (button) button.disabled = false;
        }
    }

    root.addEventListener("click", (event) => {
        const domain = event.target.closest("[data-dc-domain]");
        if (domain) { state.domain = domain.dataset.dcDomain; state.limit = 12; renderFilters(); renderQueue(); return; }
        const statusFilter = event.target.closest("[data-dc-status-filter]");
        if (statusFilter) { state.status = statusFilter.dataset.dcStatusFilter; state.limit = 12; renderFilters(); renderQueue(); return; }
        const details = event.target.closest("[data-dc-details]");
        if (details) {
            const body = details.nextElementSibling;
            const expanded = details.getAttribute("aria-expanded") === "true";
            details.setAttribute("aria-expanded", String(!expanded)); body.hidden = expanded;
            details.textContent = expanded ? "Показать план решения" : "Скрыть план";
            return;
        }
        const statusButton = event.target.closest("[data-dc-set-status]");
        if (statusButton) {
            const card = statusButton.closest("[data-dc-decision]");
            if (card) setStatus(card, statusButton.dataset.dcSetStatus);
        }
    });
    moreButton.addEventListener("click", () => { state.limit += 12; renderQueue(); });
    storeSelect.addEventListener("change", load);
    syncButton.addEventListener("click", sync);
    load();
})();
