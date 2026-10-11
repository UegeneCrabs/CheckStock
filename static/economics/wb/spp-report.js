(() => {
    const root = document.getElementById('spp-report');
    if (!root) return;
    const config = JSON.parse(document.getElementById('spp-config').textContent);
    const byId = (id) => document.getElementById(id);
    const form = byId('spp-filters');
    const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
    const money = new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 });
    let currentPage = 1;
    let requestNumber = 0;
    let sortBy = 'turnover';
    let sortOrder = 'desc';

    byId('spp-date-from').value = config.dateFrom;
    byId('spp-date-to').value = config.dateTo;
    byId('spp-manager-label').hidden = !config.showManager;

    function setOptions(select, values, labels) {
        const previous = select.value;
        select.replaceChildren(new Option('Все', ''));
        values.forEach((value, index) => select.add(new Option(labels ? labels[index] : value, value)));
        select.value = previous;
    }
    setOptions(byId('spp-store'), config.stores.map((store) => store.slug), config.stores.map((store) => store.name));

    function cell(row, value) {
        const td = document.createElement('td');
        td.textContent = value;
        row.append(td);
        return td;
    }

    function render(data) {
        sortBy = data.sort_by;
        sortOrder = data.sort_order;
        root.querySelectorAll('[data-spp-sort]').forEach((button) => {
            const active = button.dataset.sppSort === sortBy;
            button.closest('th').setAttribute('aria-sort', active ? (sortOrder === 'desc' ? 'descending' : 'ascending') : 'none');
        });
        const options = data.options;
        setOptions(byId('spp-brand'), options.brands);
        setOptions(byId('spp-manager'), options.managers);
        setOptions(byId('spp-subject'), options.subjects);
        setOptions(byId('spp-article'), options.articles);

        const summary = data.summary;
        const comparable = summary.comparable;
        const badPercent = comparable ? Math.round(summary.problem / comparable * 100) : null;
        const goodPercent = summary.health_percent;
        byId('spp-problem-percent').textContent = badPercent === null ? '—' : `${badPercent}%`;
        byId('spp-healthy-percent').textContent = goodPercent === null ? '—' : `${goodPercent}%`;
        byId('spp-problem-count').textContent = `${summary.problem} из ${comparable}`;
        byId('spp-healthy-count').textContent = `${summary.healthy} из ${comparable}`;
        byId('spp-bar-bad').style.width = `${badPercent || 0}%`;
        byId('spp-bar-good').style.width = `${goodPercent || 0}%`;
        byId('spp-overall').textContent = goodPercent === null ? 'Нет данных' : `${goodPercent}%`;
        byId('spp-missing').textContent = summary.no_data ? `Без сравнения: ${summary.no_data} товаров` : '';
        byId('spp-period-note').textContent = `Сравнение: ${data.period.previous_from} — ${data.period.previous_to}. Торговый оборот за ${data.period.date_from} — ${data.period.date_to}.`;

        const tbody = byId('spp-rows');
        tbody.replaceChildren();
        data.rows.forEach((product) => {
            const row = document.createElement('tr');
            cell(row, product.article);
            cell(row, product.name);
            cell(row, product.turnover === null ? '—' : money.format(product.turnover));
            cell(row, product.current_spp === null ? '—' : `${number.format(product.current_spp)}%`);
            cell(row, product.previous_spp === null ? '—' : `${number.format(product.previous_spp)}%`);
            const status = cell(row, '');
            const badge = document.createElement('span');
            badge.className = `spp-status spp-status--${product.status}`;
            badge.textContent = { problem: 'Требует внимания', healthy: 'Без проблем', no_data: 'Нет данных' }[product.status];
            status.append(badge);
            tbody.append(row);
        });
        byId('spp-message').textContent = data.total ? '' : 'По выбранным фильтрам товаров нет';
        byId('spp-page-label').textContent = `Страница ${data.page} из ${Math.max(1, Math.ceil(data.total / data.page_size))}`;
        byId('spp-prev').disabled = data.page <= 1;
        byId('spp-next').disabled = data.page * data.page_size >= data.total;
    }

    async function load() {
        const sequence = ++requestNumber;
        const params = new URLSearchParams(new FormData(form));
        params.set('page', String(currentPage));
        params.set('sort_by', sortBy);
        params.set('sort_order', sortOrder);
        byId('spp-message').textContent = 'Загрузка…';
        try {
            const response = await fetch(`/api/unit-economics-1c/reports/spp?${params}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить отчёт');
            if (sequence === requestNumber) render(data);
        } catch (error) {
            if (sequence === requestNumber) byId('spp-message').textContent = error.message;
        }
    }

    form.addEventListener('submit', (event) => { event.preventDefault(); currentPage = 1; load(); });
    root.querySelectorAll('[data-spp-sort]').forEach((button) => {
        button.addEventListener('click', () => {
            const field = button.dataset.sppSort;
            sortOrder = field === sortBy && sortOrder === 'desc' ? 'asc' : 'desc';
            sortBy = field;
            currentPage = 1;
            load();
        });
    });
    byId('spp-prev').addEventListener('click', () => { currentPage -= 1; load(); });
    byId('spp-next').addEventListener('click', () => { currentPage += 1; load(); });
    load();
})();
