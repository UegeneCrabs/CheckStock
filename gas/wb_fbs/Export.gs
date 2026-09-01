/**
 * Колонки отчёта, сборка строк и выгрузка FBS-заказов в общий лист.
 */

const FULL_FBS_COLUMNS = [
  { key: 'shopName', header: 'Магазин' },

  { key: 'barcode', header: 'Баркод' },
  { key: 'article', header: 'Артикул' },
  { key: 'name', header: 'Название' },

  { key: 'nmId', header: 'Артикул WB (nmId)' },
  { key: 'vendorCode', header: 'Артикул продавца (карточка)' },
  { key: 'brand', header: 'Бренд' },
  { key: 'subjectName', header: 'Предмет' },
  { key: 'length', header: 'Длина, см' },
  { key: 'width', header: 'Ширина, см' },
  { key: 'height', header: 'Высота, см' },
  { key: 'weightBrutto', header: 'Вес брутто, кг' },
  { key: 'tags', header: 'Теги' },

  { key: 'orderId', header: 'ID заказа' },
  { key: 'orderUid', header: 'UID заказа' },
  { key: 'rid', header: 'RID' },
  { key: 'chrtId', header: 'ID размера (chrtId)' },
  { key: 'colorCode', header: 'Код цвета' },
  { key: 'createdAt', header: 'Дата создания' },
  { key: 'price', header: 'Цена' },
  { key: 'convertedPrice', header: 'Цена (конв.)' },
  { key: 'currencyCode', header: 'Код валюты' },
  { key: 'convertedCurrencyCode', header: 'Код валюты (конв.)' },
  { key: 'scanPrice', header: 'Цена сканирования' },
  { key: 'cargoType', header: 'Тип груза' },
  { key: 'deliveryType', header: 'Тип доставки' },
  { key: 'warehouseId', header: 'ID склада' },
  { key: 'officeId', header: 'ID офиса' },
  { key: 'offices', header: 'Офис/склад' },
  { key: 'supplyId', header: 'ID поставки' },
  { key: 'comment', header: 'Комментарий' },
  { key: 'isZeroOrder', header: 'Нулевой заказ' },
  { key: 'isB2b', header: 'B2B заказ' },
  { key: 'fullAddress', header: 'Адрес доставки' },
  { key: 'longitude', header: 'Долгота' },
  { key: 'latitude', header: 'Широта' },

  { key: 'supplierStatus', header: 'Статус продавца' },
  { key: 'wbStatus', header: 'Статус WB' },

  // Колонка теперь принадлежит GAS и очищается вместе с отчётом.
  { key: 'quantity', header: 'Заказано' }
];

function buildFullFbsRows(shopName, orders, statusMap, cardMap) {
  return orders.map(order => {
    const status = statusMap[order.id] || {};
    const card = cardMap[order.nmId] || {};
    const address = order.address || {};
    const options = order.options || {};
    const nmId = order.nmId === undefined || order.nmId === null
      ? ''
      : String(order.nmId);

    const row = {
      shopName: shopName,

      barcode: (order.skus || []).join(', '),
      article: nmId,
      name: card.title || '',

      nmId: nmId,
      vendorCode: card.vendorCode || '',
      brand: card.brand || '',
      subjectName: card.subjectName || '',
      length: card.length || '',
      width: card.width || '',
      height: card.height || '',
      weightBrutto: card.weightBrutto || '',
      tags: card.tags || '',

      orderId: order.id || '',
      orderUid: order.orderUid || '',
      rid: order.rid || '',
      chrtId: order.chrtId || '',
      colorCode: order.colorCode || '',
      createdAt: order.createdAt || '',
      price: order.price || '',
      convertedPrice: order.convertedPrice || '',
      currencyCode: order.currencyCode || '',
      convertedCurrencyCode: order.convertedCurrencyCode || '',
      scanPrice: order.scanPrice || '',
      cargoType: order.cargoType || '',
      deliveryType: order.deliveryType || '',
      warehouseId: order.warehouseId || '',
      officeId: order.officeId || '',
      offices: (order.offices || []).join(', '),
      supplyId: order.supplyId || '',
      comment: order.comment || '',
      isZeroOrder: order.isZeroOrder === true ? 'да' : (order.isZeroOrder === false ? 'нет' : ''),
      isB2b: options.isB2b === true ? 'да' : (options.isB2b === false ? 'нет' : ''),
      fullAddress: address.fullAddress || '',
      longitude: address.longitude || '',
      latitude: address.latitude || '',

      supplierStatus: status.supplierStatus || '',
      wbStatus: status.wbStatus || '',
      quantity: 1
    };

    return FULL_FBS_COLUMNS.map(column => row[column.key]);
  });
}

function exportShopOrders(shopName, startDate, endDate) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(FBS_CONFIG.SHEETS.ORDERS);

  if (!sheet) {
    throw new Error(
      `Лист "${FBS_CONFIG.SHEETS.ORDERS}" не найден — закройте и снова откройте диалог выгрузки`
    );
  }

  const tokens = getShopTokens(ss, shopName);

  showStatus(
    `[${shopName}] 📅 Период: ` +
    `${Utilities.formatDate(startDate, FBS_CONFIG.TIMEZONE, 'yyyy-MM-dd')} — ` +
    `${Utilities.formatDate(endDate, FBS_CONFIG.TIMEZONE, 'yyyy-MM-dd')}`
  );

  showStatus(`[${shopName}] 🌐 Получение заказов из marketplace-api...`);
  const orders = fetchAllFbsOrders(tokens.marketplace, startDate, endDate);

  if (!orders.length) {
    showStatus(`[${shopName}] ⚠️ Заказов за период не найдено`);
    return 0;
  }

  showStatus(`[${shopName}] ✅ Получено заказов: ${orders.length}`);

  const orderIds = orders
    .map(order => order.id)
    .filter(id => id !== undefined && id !== null && id !== '');

  showStatus(`[${shopName}] 🔄 Получение статусов заказов...`);
  const statusMap = fetchFbsOrderStatuses(tokens.marketplace, orderIds);

  showStatus(`[${shopName}] 🏷️ Получение карточек товаров из каталога...`);
  const cardMap = fetchAllProductCards(tokens.content);

  showStatus(`[${shopName}] 💾 Дозапись строк в лист "${FBS_CONFIG.SHEETS.ORDERS}"...`);
  const rows = buildFullFbsRows(shopName, orders, statusMap, cardMap);
  appendRowsToSheet(sheet, rows, FBS_CONFIG.OUTPUT.START_COLUMN);

  showStatus(`[${shopName}] 🎉 Готово! Добавлено строк: ${rows.length}`, 8);
  return rows.length;
}
