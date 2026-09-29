// Configure API_BASE_URL, API_TOKEN, API_STORE, API_REPORT and optional API_PARAMS
// in Project Settings > Script Properties. Do not put the key in worksheet cells.
function onOpen() {
  SpreadsheetApp.getUi().createMenu('Данные сайта')
    .addItem('Обновить отчёт', 'refreshCheckStock').addToUi();
}

function refreshCheckStock() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) throw new Error('Обновление уже выполняется.');
  try {
    const p = PropertiesService.getScriptProperties().getProperties();
    const base = (p.API_BASE_URL || '').replace(/\/+$/, '');
    if (!/^https:\/\/[a-z0-9.-]+(?::\d+)?$/i.test(base)) {
      throw new Error('Укажите API_BASE_URL: публичный HTTPS-адрес без пути.');
    }
    if (!p.API_TOKEN || !p.API_STORE) throw new Error('Заполните API_TOKEN и API_STORE.');
    const report = p.API_REPORT || 'stocks';
    if (!/^[a-z][a-z-]+$/.test(report)) throw new Error('Некорректный API_REPORT.');
    const filters = JSON.parse(p.API_PARAMS || '{}');
    if (!filters || Array.isArray(filters) || typeof filters !== 'object') {
      throw new Error('API_PARAMS должен быть JSON-объектом.');
    }
    const rows = [];
    const warnings = new Set();
    const started = Date.now();
    let offset = 0;
    while (true) {
      if (Date.now() - started > 240000 || offset > 100000) {
        throw new Error('Отчёт слишком большой. Сузьте период или фильтры. Лист не изменён.');
      }
      const params = Object.assign({marketplace: 'WB'}, filters,
        {store: p.API_STORE, limit: 100, offset: offset});
      const query = Object.keys(params).filter(k => params[k] !== null && params[k] !== '')
        .map(k => encodeURIComponent(k) + '=' + encodeURIComponent(params[k])).join('&');
      const response = UrlFetchApp.fetch(base + '/api/agent/v1/' + report + '?' + query, {
        method: 'get', headers: {Authorization: 'Bearer ' + p.API_TOKEN, Accept: 'application/json'},
        muteHttpExceptions: true, followRedirects: false,
      });
      if (response.getResponseCode() !== 200) {
        throw new Error('API: HTTP ' + response.getResponseCode() + '. Проверьте ключ, права и параметры.');
      }
      const data = JSON.parse(response.getContentText());
      if (!Array.isArray(data.rows)) throw new Error('Этот метод не возвращает табличный отчёт rows.');
      rows.push.apply(rows, data.rows);
      (data.warnings || []).forEach(w => warnings.add(String(w)));
      (data.sources || []).forEach(s => {
        if (s.stale || s.has_error) warnings.add('Источник устарел или содержит ошибку: ' + (s.source || 'неизвестно'));
      });
      if (data.next_offset === null || data.next_offset === undefined) break;
      if (!Number.isInteger(data.next_offset) || data.next_offset <= offset) {
        throw new Error('Некорректная пагинация. Лист не изменён.');
      }
      offset = data.next_offset;
    }
    const columns = Array.from(new Set(rows.reduce((all, row) => all.concat(Object.keys(row)), [])));
    function cell(value) {
      if (value === null || value === undefined) return '';
      const result = typeof value === 'object' ? JSON.stringify(value) : value;
      // Never execute formulas arriving in product names or external text.
      return typeof result === 'string' && /^[=+@-]/.test(result) ? "'" + result : result;
    }
    const values = rows.map(row => columns.map(key => cell(row[key])));
    const book = SpreadsheetApp.getActiveSpreadsheet();
    const name = 'API_' + report;
    const sheet = book.getSheetByName(name) || book.insertSheet(name);
    const height = Math.max(values.length + 4, 4);
    const width = Math.max(columns.length, 1);
    if (sheet.getMaxRows() < height) sheet.insertRowsAfter(sheet.getMaxRows(), height - sheet.getMaxRows());
    if (sheet.getMaxColumns() < width) sheet.insertColumnsAfter(sheet.getMaxColumns(), width - sheet.getMaxColumns());
    sheet.clearContents();
    sheet.getRange(1, 1).setValue('Обновлено: ' + new Date().toISOString() + '. Строк: ' + rows.length);
    sheet.getRange(2, 1).setValue(cell(Array.from(warnings).join('\n')));
    if (columns.length) {
      sheet.getRange(4, 1, 1, columns.length).setValues([columns]);
      if (values.length) sheet.getRange(5, 1, values.length, columns.length).setNumberFormat('@').setValues(values);
    }
    sheet.setFrozenRows(4);
  } finally {
    lock.releaseLock();
  }
}
