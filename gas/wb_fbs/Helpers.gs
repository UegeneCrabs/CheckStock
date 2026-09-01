/**
 * Общие вспомогательные функции GAS-выгрузки WB.
 */

function log(message, level) {
  Logger.log(`[${level || 'INFO'}] ${message}`);
}

function showStatus(message, seconds) {
  try {
    SpreadsheetApp.getActiveSpreadsheet().toast(message, 'Выгрузка WB', seconds || 6);
  } catch (error) {
    // Toast недоступен вне контекста активной таблицы — это не ошибка выгрузки.
  }
  log(message, 'INFO');
}

function notifyError(context, error) {
  try {
    MailApp.sendEmail({
      to: FBS_CONFIG.ERROR_EMAIL,
      subject: '❌ Ошибка выгрузки FBS-заказов WB',
      body:
        `Контекст: ${context}\n` +
        `Время: ${new Date().toISOString()}\n\n` +
        `Ошибка: ${error.message}\n\n` +
        `Stack:\n${error.stack}`
    });
  } catch (mailError) {
    log('❌ Не удалось отправить email об ошибке: ' + mailError.message, 'ERROR');
  }
}

function getHttpStatusInfo(code) {
  const known = FBS_CONFIG.HTTP_STATUSES[code];
  if (known) {
    return known;
  }

  if (code >= 500) {
    return {
      retry: true,
      level: 'WARN',
      description: `Неизвестная ошибка сервера WB (HTTP ${code})`
    };
  }

  return {
    retry: false,
    level: 'ERROR',
    description: `Неизвестный код ответа WB (HTTP ${code})`
  };
}

function parseWbErrorBody(bodyText) {
  if (!bodyText) {
    return '(пустой ответ)';
  }

  try {
    const json = JSON.parse(bodyText);
    const parts = [];

    if (json.title) parts.push(json.title);
    if (json.detail) parts.push(json.detail);
    if (json.message) parts.push(json.message);
    if (json.errorText) parts.push(json.errorText);
    if (Array.isArray(json.errors) && json.errors.length) parts.push(json.errors.join('; '));
    if (json.code) parts.push(`code: ${json.code}`);

    return parts.length ? parts.join(' — ') : bodyText;
  } catch (error) {
    return bodyText;
  }
}

/**
 * JSON.parse преобразует целые числа больше Number.MAX_SAFE_INTEGER в
 * неточные Number. Курсор next WB уже может содержать 19 цифр, поэтому
 * перед разбором ответа заключаем только значение поля next в кавычки.
 */
function parseWbSuccessBody(bodyText) {
  if (!bodyText) {
    return {};
  }

  const safeBody = bodyText.replace(
    /("next"\s*:\s*)(-?\d{16,})(?=\s*[,}])/g,
    '$1"$2"'
  );
  return JSON.parse(safeBody);
}

function requestWB(url, options, context) {
  const retryCount = FBS_CONFIG.LIMITS.RETRY_COUNT;
  const baseDelay = FBS_CONFIG.LIMITS.RETRY_BASE_DELAY_MS;
  const fetchOptions = Object.assign({}, options, { muteHttpExceptions: true });

  for (let attempt = 1; attempt <= retryCount; attempt++) {
    let response;

    try {
      response = UrlFetchApp.fetch(url, fetchOptions);
    } catch (networkError) {
      if (attempt === retryCount) {
        throw new Error(
          `[${context}] Сетевая ошибка после ${retryCount} попыток: ${networkError.message}`
        );
      }

      const wait = baseDelay * attempt;
      log(
        `⏳ [${context}] Сетевая ошибка (${networkError.message}), ` +
        `повтор через ${wait} мс (попытка ${attempt}/${retryCount})`,
        'WARN'
      );
      Utilities.sleep(wait);
      continue;
    }

    const code = response.getResponseCode();

    if (code === 200) {
      const bodyText = response.getContentText();
      log(`✅ [${context}] HTTP 200 (попытка ${attempt})`, 'DEBUG');
      return parseWbSuccessBody(bodyText);
    }

    const statusInfo = getHttpStatusInfo(code);
    const errorDetail = parseWbErrorBody(response.getContentText());

    if (statusInfo.retry) {
      if (attempt === retryCount) {
        throw new Error(
          `[${context}] HTTP ${code} (${statusInfo.description}) ` +
          `после ${retryCount} попыток: ${errorDetail}`
        );
      }

      const wait = baseDelay * attempt;
      log(
        `⏳ [${context}] HTTP ${code} — ${statusInfo.description}, ` +
        `повтор через ${wait} мс (попытка ${attempt}/${retryCount}): ${errorDetail}`,
        statusInfo.level
      );
      Utilities.sleep(wait);
      continue;
    }

    log(
      `❌ [${context}] HTTP ${code} — ${statusInfo.description}: ${errorDetail}`,
      statusInfo.level
    );
    throw new Error(`[${context}] HTTP ${code} (${statusInfo.description}): ${errorDetail}`);
  }

  throw new Error(
    `[${context}] Превышено число попыток (${retryCount}) после повторяемых ошибок`
  );
}

function writeRowsToSheetGeneric(sheet, columns, rows, cfg) {
  const startColumn = cfg.START_COLUMN;
  const numColumns = columns.length;

  sheet
    .getRange(
      cfg.DATA_START_ROW,
      startColumn,
      sheet.getMaxRows() - cfg.DATA_START_ROW + 1,
      numColumns
    )
    .clearContent();

  sheet
    .getRange(cfg.HEADER_ROW, startColumn, 1, numColumns)
    .setValues([columns.map(column => column.header)]);

  if (rows.length) {
    sheet
      .getRange(cfg.DATA_START_ROW, startColumn, rows.length, numColumns)
      .setValues(rows);
  }

  SpreadsheetApp.flush();
  Logger.log('После очистки LastRow = ' + sheet.getLastRow());
}

function appendRowsToSheet(sheet, rows, startColumn) {
  if (!rows.length) {
    return;
  }

  const maxRows = sheet.getMaxRows();
  const values = sheet
    .getRange(1, startColumn, maxRows, 1)
    .getDisplayValues();

  let startRow = 2;
  for (let i = 1; i < values.length; i++) {
    if (values[i][0] === '') {
      startRow = i + 1;
      break;
    }
  }

  Logger.log('Запись начинается со строки: ' + startRow);

  sheet
    .getRange(startRow, startColumn, rows.length, rows[0].length)
    .setValues(rows);

  SpreadsheetApp.flush();
}
