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
            && (state.segment === 'all' || state.segment === 'negative' && row.currentRoi != null && row.currentRoi < 0 || state.segment === 'new' && row.code.toUpperCase() === 'NEW')
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
            const key = JSON.stringify([storeKey(row), String(row.article || row.id || '').split(' / ')[0].trim()]);
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
        const result = {partial: {}};
        ['plan', 'fact', 'forecast', 'difference'].forEach(key => {
            result[key] = sum(grouped.map(group => group[key]));
            result.partial[key] = grouped.some(group => key === 'plan' ? !group.completePlan : group[key] == null);
        });
        const paired = grouped.filter(group => group.completePlan && group.fact != null && group.difference != null);
        result.deviation = ratio(sum(paired.map(group => group.difference)), sum(paired.map(group => group.plan)));
        result.partial.deviation = paired.length < grouped.length;
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
    function ratioSummary(rows, column) {
        const weights = {
            currentRoi: ['currentProfit', 'currentPurchase'], roi: ['profit', 'purchase'],
            drr: ['spend', 'boughtAmount'], yesterdayDrr: ['yesterdaySpend', 'yesterdayAmount'],
            totalCtr: ['clicks', 'impressions'],
        }[column.key];
        if (column.metric !== 'ctr' && !weights) return null;
        const known = amount => typeof amount === 'number' && Number.isFinite(amount);
        const pairs = [];
        let missingCards = 0, conflictingCards = 0;
        cardGroups(rows).forEach(products => {
            const parts = products.map(row => ({
                numerator: column.metric === 'ctr' ? row.clicks?.[column.idx] : row.weights?.[weights[0]],
                denominator: column.metric === 'ctr' ? row.impressions?.[column.idx] : row.weights?.[weights[1]],
            }));
            const numerators = new Set(parts.map(part => part.numerator).filter(known));
            const denominators = new Set(parts.map(part => part.denominator).filter(known));
            if (numerators.size > 1 || denominators.size > 1) {
                conflictingCards++;
                return;
            }
            const pair = parts.find(part => known(part.numerator) && known(part.denominator));
            if (pair) pairs.push(pair);
            else missingCards++;
        });
        const numerator = sum(pairs.map(pair => pair.numerator));
        const denominator = sum(pairs.map(pair => pair.denominator));
        const zeroDrr = ['drr', 'yesterdayDrr'].includes(column.key) && numerator === 0 && denominator === 0;
        return {value: zeroDrr ? 0 : ratio(numerator, denominator),
            partial: missingCards > 0 || conflictingCards > 0,
            includedCards: pairs.length, missingCards, conflictingCards};
    }
    function summaryDetails(rows, column) {
        return ratioSummary(rows, column) || {value: summary(rows, column), partial: false};
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
    const api = {fields, combined, groups, numeric, value, filterValue, filter, tableRows, sum, summary, summaryDetails, csvCell, displayColumns, storeTurnover, turnoverTotals, orderProgress, columnId, color};
    scope.CheckStockAnalyzer = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
