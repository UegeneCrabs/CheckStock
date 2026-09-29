/* Presentation-only aliases. Never rename response fields or infer equivalent formulas. */
(() => {
  const calculator = [
    ['inputs.retail', 'inputs.seller_price', 'Цена продавца до скидки площадки', '₽'],
    ['inputs.client', 'inputs.buyer_price', 'Цена покупателя с учётом скидки площадки', '₽'],
    ['inputs.purchase', 'inputs.purchase_price', 'Закупочная стоимость единицы товара', '₽'],
    ['inputs.fulfillment', 'inputs.fulfillment_cost', 'Расходы фулфилмента на единицу товара', '₽'],
    ['inputs.commission', 'inputs.commission_percent', 'Ставка комиссии маркетплейса', '%'],
    ['inputs.acquiringPercent', 'inputs.acquiring_percent', 'Ставка эквайринга / перевода платежа', '%', 'Состав услуги и база расчёта определяются правилами площадки. У Яндекса приём платежа указан отдельно.'],
    ['inputs.team', 'inputs.company_commission_percent', 'Ставка комиссии компании', '%'],
    ['inputs.vat', 'inputs.vat_percent', 'Ставка НДС', '%'],
    ['inputs.usn', 'inputs.usn_percent', 'Ставка УСН', '%'],
    ['inputs.buyoutPercent', 'inputs.buyout_percent', 'Доля выкупа', '%'],
    ['results.margin', 'results.margin', 'Расчётная прибыль на единицу товара', '₽'],
    ['results.roi', 'results.roi', 'ROI калькулятора', '%'],
  ];
  const aliases = {
    'profit-calculator': calculator,
    prices: [
      ['retail_price', 'seller_price', 'Цена продавца до скидки площадки', '₽'],
      ['customer_price_with_spp', 'buyer_price', 'Цена покупателя с учётом скидки площадки', '₽'],
    ],
    'target-prices': [
      ['target_retail_price', 'target_retail_price', 'Целевая цена продавца до скидки площадки', '₽'],
      ['target_spp_price', 'target_spp_price', 'Целевая цена покупателя с учётом скидки площадки', '₽'],
    ],
  };

  function rows(method, maps) {
    const definitions = aliases[method] || [];
    const used = [new Set(), new Set()];
    const result = [];
    const add = (paths, definition) => {
      const fields = paths.map((path, i) => maps[i].get(path));
      fields.forEach((field, i) => { if (field) used[i].add(paths[i]); });
      result.push({ paths, fields, label: definition?.[2], unit: definition?.[3], note: definition?.[4] });
    };
    maps.forEach((map, side) => map.forEach((field, path) => {
      if (used[side].has(path)) return;
      const definition = definitions.find(pair => 'rows[].' + pair[side] === path);
      if (definition) add(definition.slice(0, 2).map(key => 'rows[].' + key), definition);
      else add([path, path]);
    }));
    return result;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { rows };
  else globalThis.CheckStockCatalogComparison = { rows };
})();
