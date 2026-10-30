(function (scope) {
    'use strict';
    const fields = [];
    function col(letter, label, key, group, width = 108, type = 'number', extra = {}) {
        fields.push({letter, label, key, group, width, type, index: fields.length, ...extra});
    }
    col('A', 'Ответственный', 'manager', 'team', 100, 'person');
    col('B', 'Проект', 'project', 'team', 75, 'project');
    col('C', 'BARCODE', 'barcode', 'product', 120, 'text');
    col('D', 'ARTICLE', 'article', 'product', 105, 'text');
    col('E', 'Название', 'name', 'product', 160, 'name');
    col('F', 'Фото', 'image', 'product', 55, 'photo');
    col('G', 'Код', 'code', 'product', 65, 'code');
    col('H', 'Сток, шт', 'stock', 'base', 75, 'stock');
    col('I', 'ДРР', 'drr', 'base', 55, 'drr');
    col('J', 'ROI сейчас', 'currentRoi', 'base', 65, 'roi');
    col('K', 'Средняя цена<br>пред. периода', 'previousPrice', 'base', 90, 'money');
    ['L', 'M', 'N', 'O'].forEach((letter, idx) => col(letter, 'Показы', 'impressions' + idx, 'traffic', 70, 'number', {idx, metric: 'impressions'}));
    ['P', 'Q', 'R', 'S'].forEach((letter, idx) => col(letter, 'CTR', 'ctr' + idx, 'traffic', 55, 'percent', {idx, metric: 'ctr'}));
    ['T', 'U', 'V', 'W'].forEach((letter, idx) => col(letter, 'Корзины', 'carts' + idx, 'traffic', 65, 'number', {idx, metric: 'carts'}));
    col('X', 'Расход РК<br>вчера', 'spend', 'performance', 85, 'money');
    col('Y', 'ДРР<br>вчера', 'yesterdayDrr', 'performance', 55, 'percent');
    col('Z', 'ДРР<br>накопительный итог', 'drr', 'performance', 85, 'drr');
    col('AA', 'РОИ<br>накопительный итог', 'roi', 'performance', 85, 'roi');
    col('AB', 'СТР<br>накопительный итог', 'totalCtr', 'performance', 85, 'percent');
    col('AC', 'Рейтинг', 'rating', 'performance', 65, 'rating');
    col('AD', 'Цели на новую неделю', 'goals', 'goals', 110, 'goal');
    ['AE', 'AF', 'AG', 'AH', 'AI', 'AJ', 'AK'].forEach((letter, idx) => col(letter, '', 'day' + idx, 'week', 65, 'day', {idx}));
    col('AL', 'СПП', 'spp', 'plan', 55, 'percent');
    col('AM', 'Цена WB<br>с кошельком', 'wallet', 'plan', 80, 'money');
    col('AN', 'Цена<br>поставщика', 'price', 'plan', 80, 'money');
    col('AO', 'Цель W,<br>шт', 'goal', 'plan', 65);
    col('AP', 'Коэф D', 'coeff', 'turnover', 55, 'decimal');
    col('AQ', 'Цель день,<br>шт', 'dayGoal', 'plan', 65, 'decimal');
    col('AR', '', 'spacer', 'turnover', 8, 'spacer');
    col('AT', 'ТО цель на день,<br>₽', 'plan', 'turnover', 110, 'money');
    col('', 'ТО сегодня,<br>₽', 'fact', 'turnover', 110, 'money');
    col('AS', 'Прогнозируемый ТО<br>на день, ₽', 'forecast', 'turnover', 120, 'money');
    col('AU', 'Разница,<br>₽', 'difference', 'turnover', 85, 'difference');
    col('AV', 'Отклонение,<br>%', 'deviation', 'turnover', 70, 'deviation');
    ['AW', 'AX', 'AY', 'AZ', 'BA', 'BB', 'BC'].forEach((letter, idx) => col(letter, '', 'note' + idx, 'notes', 165, 'note', {idx}));
    const combined = {index: fields.length, label: 'Товар', key: 'product', group: 'product', width: 160, type: 'product'};
    const groups = {product: 'Товар', team: 'Команда и проект', base: 'Текущие показатели', traffic: 'Рекламная воронка · 4 дня', performance: 'Эффективность', goals: 'Цели на неделю', week: 'Заказы · выбранная неделя', plan: 'Цены и план', turnover: 'Товарооборот', notes: 'Работа по неделе'};
    const columnId = column => column.group + ':' + column.key;
    function color(row, column) {
        let amount = value(row, column);
        if (amount == null) return 'empty';
        if (column.type === 'day') {
            if (!(row.dayGoal > 0)) return 'neutral';
            amount -= row.dayGoal;
        }
        return amount < 0 ? 'red' : amount > 0 ? 'green' : 'neutral';
    }
    const numeric = c => !['product', 'text', 'person', 'project', 'name', 'photo', 'code', 'goal', 'spacer', 'note'].includes(c.type);
    function value(row, column) {
        if (column.metric) return row[column.metric]?.[column.idx] ?? null;
        if (column.type === 'day') return row.week[column.idx];
        if (column.type === 'note') return (row.notes[column.idx] || []).map(note => note.note).join('\n');
        if (column.type === 'product') return [row.name, row.article, row.barcode].join(' · ');
        if (column.type === 'goal') return [row.goal ?? '—', row.dayGoal ?? '—'].join(' / ');
        if (column.type === 'photo') return row.image ? 'Есть фото' : 'Нет фото';
        return row[column.key] ?? null;
    }
    const filterValue = (row, column) => String(value(row, column) ?? '');
    function filter(rows, state) {
        const query = (state.query || '').toLocaleLowerCase('ru').trim();
        return rows.filter(row => (!query || [row.name, row.article, row.barcode, ...(row.barcodes || []), row.project, row.manager].join(' ').toLocaleLowerCase('ru').includes(query))
            && (!state.project || row.project === state.project)
            && (!state.manager || row.manager === state.manager)
            && (state.segment === 'all' || state.segment === 'negative' && row.currentRoi != null && row.currentRoi < 0 || state.segment === 'new' && (row.marketplace === 'OZON' ? row.is_new === true : row.code.toUpperCase() === 'NEW'))
            && (!state.color || state.color === 'all' || color(row, fields[state.colorColumn]) === state.color));
    }
    function tableRows(rows, state) {
        const columns = [...fields, combined];
        const result = filter(rows, state).filter(row => Object.entries(state.filters || {}).every(([idx, values]) => values.has(filterValue(row, columns[idx]))));
        if (state.sort != null) {
            const column = columns[state.sort];
            result.sort((a, b) => {
                const x = value(a, column), y = value(b, column);
                if (x == null) return y == null ? 0 : 1;
                if (y == null) return -1;
                return (numeric(column) ? x - y : String(x).localeCompare(String(y), 'ru', {numeric: true})) * state.direction;
            });
        }
        return result;
    }
    function sum(values) {
        const known = values.filter(v => typeof v === 'number' && Number.isFinite(v));
        return known.length ? known.reduce((a, b) => a + b, 0) : null;
    }
    function ratio(a, b) { return a != null && b > 0 ? a / b * 100 : null; }
    function displayColumns(visibleGroups, separate = false, productWidth = 160, hiddenColumns = new Set()) {
        const order = Object.keys(groups);
        const enabled = fields.filter(column => (column.group === 'product' || visibleGroups.has(column.group))
            && (column.key === 'name' || !hiddenColumns.has(columnId(column))));
        enabled.sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group)
            || (a.key === 'difference' && b.key === 'deviation' ? 1 : a.key === 'deviation' && b.key === 'difference' ? -1 : a.index - b.index));
        const primary = separate ? enabled.find(column => column.key === 'name') : {...combined, width: productWidth};
        const code = enabled.find(column => column.key === 'code');
        return [{...primary, frozenLeft: 0}, ...(code ? [{...code, frozenLeft: primary.width}] : []),
            ...enabled.filter(column => !['name', 'code'].includes(column.key)
                && (separate || !['barcode', 'article', 'image'].includes(column.key)))];
    }
    const storeKey = row => row.store_slug || row.project || '';
    function cardGroups(rows) {
        const articles = new Map();
        rows.forEach(row => {
            const article = String(row.article || row.id || '');
            const key = JSON.stringify([storeKey(row), row.metricIdentity === 'fullArticle' ? article : article.split(' / ')[0].trim()]);
            if (!articles.has(key)) articles.set(key, []);
            articles.get(key).push(row);
        });
        return [...articles.values()];
    }
    function turnoverTotals(rows) {
        const known = value => typeof value === 'number' && Number.isFinite(value);
        const sharedValue = (products, key) => {
            const values = new Set(products.map(row => row[key]).filter(known));
            return values.size === 1 ? values.values().next().value : null;
        };
        // Funnel turnover belongs to an nmID; targets belong to individual size rows.
        const grouped = cardGroups(rows).map(products => {
            const plan = sum(products.map(row => row.plan));
            const completePlan = products.every(row => known(row.plan));
            const fact = sharedValue(products, 'fact'), forecast = sharedValue(products, 'forecast');
            return {plan, completePlan, fact, forecast,
                difference: completePlan && forecast != null ? forecast - plan : null};
        });
        const result = {partial: {}, stale: {}};
        ['plan', 'fact', 'forecast', 'difference'].forEach(key => {
            result[key] = sum(grouped.map(group => group[key]));
            const quality = mergeQuality(rows.map(row => metricQuality(row, key)));
            result.partial[key] = quality.partial || grouped.some(group => key === 'plan' ? !group.completePlan : group[key] == null);
            result.stale[key] = quality.stale;
        });
        const paired = grouped.filter(group => group.completePlan && group.fact != null && group.difference != null);
        result.deviation = ratio(sum(paired.map(group => group.difference)), sum(paired.map(group => group.plan)));
        const quality = mergeQuality(rows.map(row => metricQuality(row, 'deviation')));
        result.partial.deviation = quality.partial || paired.length < grouped.length;
        result.stale.deviation = quality.stale;
        return result;
    }
    function storeTurnover(rows) {
        const stores = new Map();
        rows.forEach(row => {
            const key = storeKey(row);
            if (!stores.has(key)) stores.set(key, {store: key, project: row.project || key, rows: []});
            stores.get(key).rows.push(row);
        });
        return [...stores.values()].sort((a, b) => a.project.localeCompare(b.project, 'ru')).map(store => ({
            store: store.store, project: store.project, ...turnoverTotals(store.rows),
        }));
    }
    const orderMetricField = field => /^(?:day[0-6]|week|\d{4}-\d{2}-\d{2})$/.test(field);
    function metricQuality(row, field) {
        const parts = field.split('.'), key = parts.at(-1);
        const source = parts.length > 1 ? row[parts[0]] || {} : row;
        const explicit = source.quality?.[key] || row.quality?.[field];
        const coverage = source.coverage || {}, reasons = new Set(explicit?.reasons || []);
        let partial = explicit?.complete === false, stale = explicit?.stale === true;
        if (!explicit) {
            const details = coverage[key + 'Details'];
            const completeKey = {drr:'drrComplete', yesterdayDrr:'yesterdayDrrComplete', roi:'roiComplete', currentRoi:'currentRoiComplete'}[key];
            const countKey = {drr:'drr', fact:'orders', impressions:'advertising', ctr:'advertising', totalCtr:'advertising'}[key];
            if (details && typeof details.complete === 'boolean') partial ||= !details.complete;
            if (completeKey && typeof coverage[completeKey] === 'boolean') partial ||= !coverage[completeKey];
            else if (countKey && typeof coverage[countKey] === 'number' && typeof coverage.expected === 'number') partial ||= coverage[countKey] < coverage.expected;
            if (key === 'stock' || key === 'stockDays') partial ||= row.stockPartial === true;
            if (key === 'stockDays') partial ||= row.stockOrdersComplete === false;
            if (['orders','cancels'].includes(key) && typeof row.complete === 'boolean') partial ||= !row.complete;
            stale ||= coverage[key + 'Stale'] === true || coverage.stale === true && (countKey != null || ['roi','currentRoi','spend','previousPrice','orders','cancels'].includes(key));
        }
        const issues = explicit ? [] : (row.field_issues || []).filter(item => item.field === field || item.field === 'orders' && orderMetricField(key));
        issues.forEach(item => {
            if (item.reason) reasons.add(item.reason);
            partial ||= item.reason === 'partial' || item.complete === false;
            stale ||= ['stale','source_error','transport_error'].includes(item.reason) || item.stale === true;
        });
        if (partial && !reasons.size) reasons.add('partial');
        if (stale) reasons.add('stale');
        return {complete: !partial, partial, stale, reasons: [...reasons], expected_dates: explicit?.expected_dates || [], known_dates: explicit?.known_dates || []};
    }
    function mergeQuality(qualities) {
        return {partial: qualities.some(item => item.partial), stale: qualities.some(item => item.stale), reasons: [...new Set(qualities.flatMap(item => item.reasons || []))]};
    }
    function qualityLabel(quality) {
        return [quality.partial ? 'неполные данные' : '', quality.stale ? 'сохранённые данные' : ''].filter(Boolean).join(' · ');
    }
    function qualityHelp(quality) {
        return [...new Set([qualityLabel(quality), ...(quality.reasons || []).map(reasonMessage)].filter(Boolean))].join('. ');
    }
    function weightedSummary(rows, field, numeratorValue, denominatorValue) {
        const known = amount => typeof amount === 'number' && Number.isFinite(amount);
        const pairs = [], qualities = [];
        let missingCards = 0, conflictingCards = 0;
        cardGroups(rows).forEach(products => {
            qualities.push(...products.map(row => metricQuality(row, field)));
            const parts = products.map(row => ({numerator: numeratorValue(row), denominator: denominatorValue(row)}));
            const numerators = new Set(parts.map(part => part.numerator).filter(known));
            const denominators = new Set(parts.map(part => part.denominator).filter(known));
            if (numerators.size > 1 || denominators.size > 1) { conflictingCards++; return; }
            const pair = parts.find(part => known(part.numerator) && known(part.denominator));
            if (pair) pairs.push(pair);
            else missingCards++;
        });
        const numerator = sum(pairs.map(pair => pair.numerator));
        const denominator = sum(pairs.map(pair => pair.denominator));
        const zeroDrr = ['drr', 'yesterdayDrr'].includes(field.split('.').at(-1)) && numerator === 0 && denominator === 0;
        const quality = mergeQuality(qualities);
        if (missingCards) quality.reasons.push('missing_source');
        if (conflictingCards) quality.reasons.push('mapping_conflict');
        return {...quality, value: zeroDrr ? 0 : ratio(numerator, denominator),
            partial: quality.partial || missingCards > 0 || conflictingCards > 0,
            includedCards: pairs.length, missingCards, conflictingCards};
    }
    function ratioSummary(rows, column) {
        const weights = {
            currentRoi: ['currentProfit', 'currentPurchase'], roi: ['profit', 'purchase'],
            drr: ['spend', 'boughtAmount'], yesterdayDrr: ['yesterdaySpend', 'yesterdayAmount'],
            totalCtr: ['clicks', 'impressions'],
        }[column.key];
        if (column.metric !== 'ctr' && !weights) return null;
        return weightedSummary(rows, column.key,
            row => column.metric === 'ctr' ? row.clicks?.[column.idx] : row.weights?.[weights[0]],
            row => column.metric === 'ctr' ? row.impressions?.[column.idx] : row.weights?.[weights[1]]);
    }
    function summaryDetails(rows, column) {
        const result = ratioSummary(rows, column);
        if (result) return result;
        const amount = summary(rows, column);
        const quality = mergeQuality(rows.map(row => metricQuality(row, column.key)));
        if (amount != null && numeric(column) && column.metric !== 'carts' && rows.some(row => value(row, column) == null)) quality.partial = true;
        return {...quality, value: amount};
    }
    function summary(rows, column) {
        const result = ratioSummary(rows, column);
        if (result) return result.value;
        const weighted = (numerator, denominator) => {
            const pairs = rows.filter(row => row.weights[numerator] != null && row.weights[denominator] != null);
            return ratio(sum(pairs.map(row => row.weights[numerator])), sum(pairs.map(row => row.weights[denominator])));
        };
        if (['carts', 'impressions'].includes(column.metric) || column.type === 'day' || column.key === 'spend') {
            return sum(cardGroups(rows).map(products => {
                const counts = new Set(products.map(row => value(row, column))
                    .filter(count => typeof count === 'number' && Number.isFinite(count)));
                return counts.size === 1 ? [...counts][0] : null;
            }));
        }
        if (column.key === 'previousPrice') {
            const percent = weighted('previousAmount', 'previousCount');
            return percent == null ? null : percent / 100;
        }
        if (['fact', 'forecast', 'difference', 'deviation'].includes(column.key)) return turnoverTotals(rows)[column.key];
        if (!numeric(column) || ['price', 'wallet', 'spp', 'rating', 'coeff'].includes(column.key)) return null;
        return sum(rows.map(row => value(row, column)));
    }
    function orderProgress(orders, goal) {
        if (!Number.isFinite(orders) || orders < 0 || !Number.isFinite(goal) || goal <= 0) return null;
        return {
            state: orders > goal ? 'over' : orders < goal ? 'under' : 'met',
            split: Math.min(orders, goal) / Math.max(orders, goal) * 100,
            difference: Math.abs(orders - goal),
        };
    }
    function csvCell(value) {
        let text = String(value ?? '');
        if (/^[\s]*[=+@\-]/.test(text) && typeof value !== 'number') text = "'" + text;
        return '"' + text.replace(/"/g, '""') + '"';
    }
    function csvQuality(row, field, value) {
        const quality = metricQuality(row, field);
        const state = [value == null ? 'unknown' : '', quality.partial ? 'partial' : '', quality.stale ? 'stale' : ''].filter(Boolean).join('|') || 'full';
        return [state, [...new Set([...quality.reasons, ...(value == null && !quality.reasons.length ? ['missing_source'] : [])])].join('|')];
    }
    function qualityCsv(rows, columns, getValue, getLabel, getField = column => column.key, enabled = false) {
        const extra = enabled ? columns.filter(column => (column.numeric === true || ['number','money','decimal','percent','drr','roi','stock','rating','day','difference','deviation'].includes(column.type))
            && !getField(column).startsWith('carts') && rows.some(row => csvQuality(row, getField(column), getValue(row, column))[0] !== 'full')) : [];
        return [[...columns.map(getLabel), ...extra.flatMap(column => [getLabel(column) + ' · качество', getLabel(column) + ' · причины'])],
            ...rows.map(row => [...columns.map(column => getValue(row, column)), ...extra.flatMap(column => csvQuality(row, getField(column), getValue(row, column)))])];
    }
    function marketplaceConfig(config = {}) {
        const marketplace = config.marketplace || 'WB';
        const labels = {marketplace: marketplace === 'YANDEX MARKET' ? 'ЯМ' : marketplace === 'OZON' ? 'Ozon' : 'WB', wallet: marketplace === 'YANDEX MARKET' ? 'Цена с Пэй' : marketplace === 'OZON' ? 'Цена с Ozon Картой' : 'Цена WB с кошельком', article: marketplace === 'WB' ? 'Артикул WB' : 'Артикул продавца', fbo: marketplace === 'YANDEX MARKET' ? 'FBY' : 'FBO', ...config.labels};
        return {...config, marketplace, labels, qualityExport: config.qualityExport ?? marketplace !== 'WB', apiBase: config.apiBase || '/api/analytics', pageBase: config.pageBase || '/analytics', exportPrefix: config.exportPrefix || 'wb', metricIdentity: config.metricIdentity || 'wbCard'};
    }
    const reasonMessages = {missing_source: 'Нет подтверждённых данных источника', not_configured: 'Коэффициент D этого часа не задан в параметрах кабинета', subscription_required: 'Источник сообщил, что требуется подписка', partial: 'Известна только часть данных', stale: 'Сохранённые данные: последнее обновление не завершено', provisional: 'Предварительные данные текущего дня источника', observed: 'Сохранённое наблюдение; финализация источником не подтверждена', mapping_conflict: 'Источник не подтвердил однозначную привязку товара', quota_deferred: 'Обновление отложено по суточному бюджету запросов', transport_error: 'Источник временно недоступен', rate_limited: 'Источник ограничил частоту запросов', credentials_error: 'Источник отклонил учётные данные', access_denied: 'Источник отклонил доступ к методу', api_rejected: 'Источник отклонил запрос', run_deferred: 'Обновление будет продолжено с сохранённой точки', conflict: 'Данные изменились в другой вкладке. Сравните сохранённую версию перед повтором.', current_day_only: 'Запись можно изменять только за сегодняшний день по Москве', current_week_only: 'Решения можно изменять только за текущую неделю по Москве', invalid_period: 'Проверьте выбранный период', product_not_found: 'Товар не найден или недоступен', note_not_found: 'Запись не найдена или недоступна', inactive_user: 'Учётная запись неактивна'};
    Object.assign(reasonMessages, {source_missing: reasonMessages.missing_source, field_missing: reasonMessages.missing_source, hourly_d_missing: reasonMessages.not_configured, estimated_price: 'Расчётная цена', stale_price: 'Последняя сохранённая цена устарела', source_error: 'Последнее обновление источника завершилось ошибкой', source_timezone_unknown: 'Границы суток источника не подтверждены; дата отчёта может отличаться от московских суток', source_calendar_unconfirmed: 'Календарные границы источника не подтверждены'});
    Object.assign(reasonMessages, {malformed_source: 'Источник вернул данные в неожиданном формате; предыдущие значения сохранены', repeated_page: 'Источник повторил страницу или SKU; незавершённые данные не опубликованы', limiter_required: 'Ограничитель запросов недоступен; загрузка не выполнялась', write_failed: 'Не удалось сохранить данные. Повторите действие'});
    reasonMessages.invalid_forecast = 'Нужны 24 разных часа от 0 до 23 и неотрицательные конечные коэффициенты либо пустые значения';
    reasonMessages.source_date_unverified = 'Источник не подтвердил исходную дату наблюдения';
    const reasonMessage = code => reasonMessages[code] || code;
    function cartInterval(day) {
        const next = new Date(day + 'T12:00:00Z'); next.setUTCDate(next.getUTCDate() + 1);
        return `С 03:00 ${day} до 03:00 ${next.toISOString().slice(0,10)} по Москве (UTC-день ${day})`;
    }
    function metricHelp(row, field, config, unknown = false) {
        if (config.marketplace === 'WB') return '';
        const issues = (row.field_issues || []).filter(item => item.field === field || item.field === 'orders' && orderMetricField(field));
        if (field.startsWith('carts') && row.cartSources?.[Number(field.slice(5))]) issues.push(row.cartSources[Number(field.slice(5))]);
        const text = issues.map(item => [reasonMessage(item.reason), item.source_scope === 'report_date' && item.source_day ? 'Дата отчёта: ' + item.source_day : '', item.source_timezone ? 'Часовой пояс источника: ' + item.source_timezone : '', item.calendar_warning ? reasonMessage(item.calendar_warning) : '', item.provisional ? reasonMessages.provisional : '', item.complete === false ? reasonMessages.partial : '', item.stale ? reasonMessages.stale : '', (item.observed_at || item.received_at) ? 'Наблюдение: ' + new Date(item.observed_at || item.received_at).toLocaleString('ru-RU', {timeZone: 'Europe/Moscow'}) : '', item.next_retry_at ? 'Следующая попытка: ' + new Date(item.next_retry_at).toLocaleString('ru-RU', {timeZone: 'Europe/Moscow'}) : ''].filter(Boolean).join('. '));
        const reason = row.reasons?.[field] || (/^(price|wallet|spp)$/.test(field) ? row.reasons?.prices : null) || (field.startsWith('carts') ? row.reasons?.carts : null);
        if (reason) text.push(reasonMessage(reason));
        const quality = qualityHelp(metricQuality(row, field));
        if (quality) text.push(quality);
        if (unknown && !text.length) text.push(reasonMessages.missing_source);
        if (config.marketplace === 'OZON' && field.startsWith('carts')) {
            const day = row.carts_source_days?.[Number(field.slice(5))];
            if (day) text.unshift(cartInterval(day));
        }
        return text.join('. ');
    }
    function configure(config) {
        if (config.marketplace === 'WB') return;
        fields.find(column => column.key === 'wallet').label = config.labels.wallet;
        fields.find(column => column.key === 'price').label = 'Цена продавца';
    }
    function localize(root, config) {
        if (config.marketplace === 'WB' || !root) return;
        const text = value => {
            let result = value.replace(/Заказы из воронки WB|Заказы WB из воронки/g, config.marketplace === 'OZON' ? 'Заказы Ozon FBO + FBS' : 'Заказы по API ' + config.labels.marketplace);
            result = result.replace(/Цена WB(?:<br>| )с кошельком/g, config.labels.wallet).replace(/WILDBERRIES|Wildberries|WB/g, config.labels.marketplace).replace(/\bFBO\b/g,config.labels.fbo);
            return result;
        };
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) if (!['SCRIPT','STYLE'].includes(walker.currentNode.parentElement?.tagName)) nodes.push(walker.currentNode);
        nodes.forEach(node => { node.nodeValue = text(node.nodeValue); });
        root.querySelectorAll('[title],[aria-label]').forEach(node => ['title','aria-label'].forEach(attribute => {if(node.hasAttribute(attribute)) node.setAttribute(attribute,text(node.getAttribute(attribute)));}));
    }
    const api = {fields, combined, groups, numeric, value, filterValue, filter, tableRows, sum, summary, summaryDetails, weightedSummary, metricQuality, mergeQuality, qualityLabel, qualityHelp, csvQuality, qualityCsv, csvCell, displayColumns, storeTurnover, turnoverTotals, orderProgress, columnId, color, marketplaceConfig, configure, localize, metricHelp, reasonMessage, cartInterval};
    scope.CheckStockAnalyzer = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
