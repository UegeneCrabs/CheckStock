/**
 * Запросы к Wildberries API для выгрузки FBS-заказов.
 */

/**
 * Разбивает включительный период на непересекающиеся окна максимум
 * по windowDays суток. Границы передаются в WB как Unix timestamp.
 *
 * Например, 01.08 00:00:00 — 31.08 23:59:59 при windowDays=30:
 *   01.08 00:00:00 — 30.08 23:59:59
 *   31.08 00:00:00 — 31.08 23:59:59
 */
function splitFbsDateRangeIntoWindows(startDate, endDate, windowDays) {
  const startTs = Math.floor(startDate.getTime() / 1000);
  const endTs = Math.floor(endDate.getTime() / 1000);
  const windowSeconds = windowDays * 24 * 60 * 60;

  if (!Number.isFinite(startTs) || !Number.isFinite(endTs)) {
    throw new Error('Некорректные границы периода FBS');
  }
  if (!Number.isInteger(windowDays) || windowDays <= 0) {
    throw new Error('Размер окна FBS должен быть положительным целым числом дней');
  }
  if (startTs > endTs) {
    return [];
  }

  const windows = [];
  let from = startTs;

  while (from <= endTs) {
    const to = Math.min(from + windowSeconds - 1, endTs);
    windows.push({ from: from, to: to });
    from = to + 1;
  }

  return windows;
}

/** Постранично забирает заказы для одного окна (не более 30 дней). */
function fetchFbsOrdersWindow(token, dateFromTs, dateToTs) {
  const baseUrl = FBS_CONFIG.URLS.FBS_ORDERS;
  const limit = FBS_CONFIG.LIMITS.ORDERS_PAGE_LIMIT;
  const allOrders = [];
  const seenCursors = Object.create(null);

  let next = '0';

  while (true) {
    const cursorKey = String(next);
    if (seenCursors[cursorKey]) {
      throw new Error(`WB вернул повторяющийся курсор пагинации: ${next}`);
    }
    seenCursors[cursorKey] = true;

    const url =
      `${baseUrl}?limit=${limit}&next=${next}` +
      `&dateFrom=${dateFromTs}&dateTo=${dateToTs}`;
    const options = { method: 'get', headers: { Authorization: token } };

    const json = requestWB(url, options, 'FBS orders window');
    const orders = Array.isArray(json.orders) ? json.orders : [];

    allOrders.push.apply(allOrders, orders);

    log(
      `📦 Окно ${new Date(dateFromTs * 1000).toISOString()}–` +
      `${new Date(dateToTs * 1000).toISOString()}: ` +
      `получено ${orders.length}, всего ${allOrders.length}`,
      'INFO'
    );

    const responseNext = json.next === undefined || json.next === null || json.next === ''
      ? '0'
      : String(json.next).trim();

    if (!orders.length || responseNext === '0') {
      break;
    }
    if (!/^\d+$/.test(responseNext)) {
      throw new Error(`WB вернул некорректный курсор пагинации: ${json.next}`);
    }

    next = responseNext;
    Utilities.sleep(FBS_CONFIG.LIMITS.PAGE_DELAY_MS);
  }

  return allOrders;
}

/**
 * Забирает все FBS-заказы за период и удаляет возможные повторы между
 * страницами/окнами по ID сборочного задания.
 */
function fetchAllFbsOrders(token, startDate, endDate) {
  const windows = splitFbsDateRangeIntoWindows(
    startDate,
    endDate,
    FBS_CONFIG.LIMITS.WINDOW_DAYS
  );
  const ordersById = Object.create(null);
  const ordersWithoutId = [];

  windows.forEach((window, index) => {
    log(`🗓️ Окно ${index + 1}/${windows.length}`, 'INFO');
    const orders = fetchFbsOrdersWindow(token, window.from, window.to);

    orders.forEach(order => {
      if (order.id === undefined || order.id === null || order.id === '') {
        ordersWithoutId.push(order);
        return;
      }
      ordersById[String(order.id)] = order;
    });
  });

  return Object.keys(ordersById)
    .map(id => ordersById[id])
    .concat(ordersWithoutId);
}

/** Возвращает карту orderId -> { supplierStatus, wbStatus }. */
function fetchFbsOrderStatuses(token, orderIds) {
  const url = FBS_CONFIG.URLS.FBS_ORDERS_STATUS;
  const chunkSize = FBS_CONFIG.LIMITS.STATUS_CHUNK_SIZE;
  const statusMap = {};
  const uniqueOrderIds = Array.from(
    new Set(orderIds.filter(id => id !== undefined && id !== null && id !== ''))
  );

  for (let i = 0; i < uniqueOrderIds.length; i += chunkSize) {
    const chunk = uniqueOrderIds.slice(i, i + chunkSize);
    const options = {
      method: 'post',
      headers: { Authorization: token, 'Content-Type': 'application/json' },
      payload: JSON.stringify({ orders: chunk })
    };

    const json = requestWB(url, options, 'FBS orders status');
    const orders = Array.isArray(json.orders) ? json.orders : [];

    orders.forEach(order => {
      statusMap[order.id] = {
        supplierStatus: order.supplierStatus || '',
        wbStatus: order.wbStatus || ''
      };
    });

    log(
      `🏷️ Статусы получены: ${orders.length} (чанк ${Math.floor(i / chunkSize) + 1})`,
      'INFO'
    );

    if (i + chunkSize < uniqueOrderIds.length) {
      Utilities.sleep(FBS_CONFIG.LIMITS.PAGE_DELAY_MS);
    }
  }

  return statusMap;
}

/** Загружает весь каталог и возвращает карту nmId -> данные карточки. */
function fetchAllProductCards(token) {
  const url = FBS_CONFIG.URLS.CONTENT_CARDS_LIST;
  const limit = FBS_CONFIG.LIMITS.CONTENT_PAGE_LIMIT;
  const cardMap = {};

  let cursor = { limit: limit };
  let total = limit;

  while (total >= limit) {
    const payload = {
      settings: {
        sort: { ascending: false },
        filter: { withPhoto: -1 },
        cursor: cursor
      }
    };
    const options = {
      method: 'post',
      headers: { Authorization: token, 'Content-Type': 'application/json' },
      payload: JSON.stringify(payload)
    };

    const json = requestWB(url, options, 'Content cards list');
    const cards = Array.isArray(json.cards) ? json.cards : [];

    cards.forEach(card => {
      const dimensions = card.dimensions || {};
      cardMap[card.nmID] = {
        vendorCode: card.vendorCode || '',
        title: card.title || '',
        description: card.description || '',
        brand: card.brand || '',
        subjectName: card.subjectName || '',
        length: dimensions.length || '',
        width: dimensions.width || '',
        height: dimensions.height || '',
        weightBrutto: dimensions.weightBrutto || '',
        tags: (card.tags || []).map(tag => tag.name).join(', ')
      };
    });

    total = cards.length;
    log(`🏷️ Загружено карточек товаров: ${Object.keys(cardMap).length}`, 'INFO');

    if (!json.cursor || total < limit) {
      break;
    }

    cursor = {
      updatedAt: json.cursor.updatedAt,
      nmID: json.cursor.nmID,
      limit: limit
    };

    Utilities.sleep(FBS_CONFIG.LIMITS.PAGE_DELAY_MS);
  }

  return cardMap;
}
