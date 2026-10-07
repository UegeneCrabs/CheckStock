(function (scope) {
    'use strict';
    const identities = [
        {index: 0, key: 'project', label: 'Проект', width: 100},
        {index: 1, key: 'category', label: 'Категория', width: 138},
        {index: 2, key: 'barcode', label: 'Баркод', width: 165},
        {index: 3, key: 'article', label: 'Артикул WB', width: 142},
        {index: 4, key: 'name', label: 'Название', width: 335},
    ];
    const combined = {index: 1000, key: 'product', label: 'Товар', width: 375};
    function columns(dates, exact = false, showDays = true) {
        const result = exact ? [...identities] : [combined, identities[0], identities[1]];
        result.push({index: 5, key: 'orders', label: 'Итого заказы', width: 105, numeric: true});
        result.push({index: 6, key: 'cancels', label: 'Итого отмены', width: 105, numeric: true});
        if (showDays) dates.forEach((date, day) => result.push({index: 7 + day, key: date, label: date, width: 102, day, numeric: true}));
        return result;
    }
    function value(row, column) {
        if (column.day != null) return row.days[column.day];
        if (column.key === 'product') return [row.name, row.article, row.barcode].join(' · ');
        return row[column.key] ?? (column.numeric ? null : '');
    }
    const filterValue = (row, column) => String(value(row, column) ?? '');
    function externalRows(rows, state) {
        const query = state.query.trim().toLocaleLowerCase('ru');
        return rows.filter(row => (!query || [row.name, row.article, row.barcode, ...row.article_aliases, row.project, row.category].join(' ').toLocaleLowerCase('ru').includes(query))
            && (!state.project || row.project === state.project)
            && (!state.category || (state.category === '__empty__' ? !row.category : row.category === state.category))
            && (state.segment === 'all' || state.segment === 'orders' && row.orders > 0 || state.segment === 'zero' && row.orders === 0 && row.complete));
    }
    function filteredRows(rows, state, fields) {
        const result = externalRows(rows, state).filter(row => Object.entries(state.filters).every(([idx, values]) => {
            const column = fields.find(c => c.index === Number(idx));
            return !column || values.has(filterValue(row, column));
        }));
        const column = fields.find(c => c.index === state.sort);
        if (column) result.sort((a, b) => {
            const x = value(a, column), y = value(b, column);
            if (x == null || x === '') return y == null || y === '' ? 0 : 1;
            if (y == null || y === '') return -1;
            return (column.numeric ? x - y : String(x).localeCompare(String(y), 'ru', {numeric: true})) * state.direction;
        });
        return result;
    }
    function total(rows, column) {
        const values = rows.map(row => value(row, column)).filter(value => typeof value === 'number');
        return {value: values.length ? values.reduce((a, b) => a + b, 0) : null,
            partial: values.length < rows.length || column.day == null && rows.some(row => !row.complete)};
    }
    function csvCell(value) {
        let text = String(value ?? '');
        if (typeof value !== 'number' && /^[\s]*[=+@-]/.test(text)) text = "'" + text;
        return '"' + text.replace(/"/g, '""') + '"';
    }
    const api = {columns, combined, value, filterValue, externalRows, filteredRows, total, csvCell};
    scope.CheckStockSalesApi = api;
    if (typeof module !== 'undefined') module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
