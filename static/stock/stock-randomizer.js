(() => {
    const root = document.querySelector("[data-stock-randomizer]");
    if (!root) return;

    const fulfillment = root.querySelector("[data-randomizer-fulfillment]");
    const generateButton = root.querySelector("[data-randomizer-generate]");
    const buttonLabel = root.querySelector("[data-randomizer-button-label]");
    const notice = root.querySelector("[data-randomizer-notice]");
    const lastRun = root.querySelector("[data-randomizer-last-run]");
    const readOnly = document.body.dataset.accessLevel === "read";

    const escapeHtml = (value) => String(value ?? "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    const copyIdentifier = (kind, value, label) => window.CheckStockIdentifierCopy
        ? window.CheckStockIdentifierCopy.html(kind, value, label)
        : escapeHtml(label ?? value);

    const resultHtml = (item) => {
        if (!item.article) {
            return `<div class="randomizer-result is-empty" data-randomizer-result>
                <span class="randomizer-result-icon" aria-hidden="true">!</span>
                <p>${escapeHtml(item.message || "Подходящих артикулов не осталось")}</p>
            </div>`;
        }
        const barcode = item.barcode
            ? `<small>${copyIdentifier("Баркод", item.barcode, `Баркод ${item.barcode}`)}</small>` : "";
        return `<div class="randomizer-result is-ready" data-randomizer-result>
            <span>АРТИКУЛ ДЛЯ СВЕРКИ</span>
            <strong>${copyIdentifier("Артикул", item.article, item.article)}</strong>
            <p>${escapeHtml(item.name || "Без названия")}</p>
            ${barcode}
            <div class="randomizer-stock-pair">
                <span><small>Учёт ФФ</small><b>${Number(item.ff_quantity || 0).toLocaleString("ru-RU")} шт.</b></span>
                <span><small>WB FBS</small><b>${Number(item.fbs_quantity || 0).toLocaleString("ru-RU")} шт.</b></span>
            </div>
        </div>`;
    };

    const updateCard = (item) => {
        const card = root.querySelector(`[data-randomizer-card][data-store="${CSS.escape(item.store_slug)}"]`);
        if (!card) return;
        const previous = card.querySelector("[data-randomizer-result]");
        if (previous) previous.outerHTML = resultHtml(item);
        const used = card.querySelector("[data-randomizer-used]");
        const remaining = card.querySelector("[data-randomizer-remaining]");
        if (used) used.textContent = Number(item.used_count || 0).toLocaleString("ru-RU");
        if (remaining) remaining.textContent = Number(item.remaining_count || 0).toLocaleString("ru-RU");
        card.classList.remove("is-generated");
        requestAnimationFrame(() => card.classList.add("is-generated"));
    };

    const setNotice = (message, tone = "success") => {
        notice.textContent = message;
        notice.dataset.tone = tone;
        notice.hidden = !message;
    };

    const setLoading = (loading) => {
        generateButton.disabled = loading || readOnly || !fulfillment.value;
        generateButton.classList.toggle("is-loading", loading);
        buttonLabel.textContent = loading ? "Подбираем артикулы…" : "Сгенерировать";
    };

    const requestGeneration = async () => {
        setLoading(true);
        setNotice("");
        try {
            const response = await fetch("/stock/randomizer/generate", {
                method: "POST",
                headers: {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Requested-With": "fetch",
                },
                body: JSON.stringify({ fulfillment: fulfillment.value }),
            });
            const body = await response.json().catch(() => ({}));
            if (!response.ok || body.ok === false) {
                throw new Error(body.error || body.detail || `Ошибка ${response.status}`);
            }
            body.items.forEach(updateCard);
            const generatedAt = new Date(body.generated_at);
            lastRun.textContent = Number.isNaN(generatedAt.getTime())
                ? "Только что"
                : generatedAt.toLocaleString("ru-RU", {
                    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
                });
            const total = body.items.length;
            const empty = total - body.generated_count;
            setNotice(
                empty
                    ? `Готово: выбрано ${body.generated_count} из ${total}. Для ${empty} магазинов новых подходящих артикулов нет.`
                    : `Готово: сформировано ${body.generated_count} позиций для сверки.`,
                empty ? "warning" : "success",
            );
        } catch (error) {
            setNotice(error.message || "Не удалось сгенерировать артикулы", "error");
        } finally {
            setLoading(false);
        }
    };

    fulfillment.addEventListener("change", () => {
        const url = new URL(window.location.href);
        url.searchParams.set("ff", fulfillment.value);
        window.location.assign(url.toString());
    });
    generateButton.addEventListener("click", requestGeneration);

    if (readOnly) {
        generateButton.disabled = true;
        setNotice("Раздел доступен только для просмотра. Генерация отключена.", "warning");
    }
})();
