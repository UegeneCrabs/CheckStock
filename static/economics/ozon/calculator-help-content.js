window.OzonCalculatorHelpContent = (() => {
    'use strict';
    const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 6 });
    const days = new Intl.DateTimeFormat('ru-RU', { timeZone: 'Europe/Moscow' });
    const moments = new Intl.DateTimeFormat('ru-RU', {
        timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'short',
    });
    const n = value => value == null || value === '' ? '—' : numbers.format(Number(value));
    const rub = value => n(value) + ' ₽';
    const pct = value => n(value) + '%';
    const lines = (...values) => values.filter(Boolean).join('\n');
    const priceNames = { base_price: 'Цена поставщика', seller_price: 'Цена с учётом акции', black_price: 'Цена с СПП', green_price: 'Цена с Кошельком' };
    const priceKeys = Object.keys(priceNames);
    const discountKeys = ['action_discount_percent', 'spp_percent', 'wallet_percent'];
    const discountNames = ['Скидка акции', 'СПП', 'Скидка Кошелька'];
    const missing = 'Не рассчитано: не хватает исходных данных или база не позволяет выполнить расчёт.';

    function date(value, time = false) {
        if (!value) return 'не передана';
        const parsed = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? value + 'T12:00:00+03:00' : value);
        return Number.isNaN(parsed.getTime()) ? 'не передана' : (time ? moments : days).format(parsed) + (time ? ' МСК' : '');
    }

    function stamp(label, value) { return label + ': ' + date(value, true) + '.'; }
    function period(start, end) { return 'Период: ' + date(start) + '–' + date(end) + '.'; }
    function state(source = {}) {
        source = source || {};
        return lines(source.stale && 'Данные устарели.', source.error && 'Последнее обновление завершилось ошибкой.',
            source.state === 'conflict' && 'Источники расходятся; значение не выбрано.',
            source.state === 'partial' && 'Данные периода неполные.',
            source.state === 'missing' && 'Источник ещё не загружен.',
            source.state === 'error' && 'Ошибка загрузки источника.');
    }

    function priceSource(product, key) {
        const source = product.price?.[key] || {};
        const fields = { base_price: 'price.price', seller_price: 'price.marketing_seller_price', black_price: 'website_prices.black_price', green_price: 'website_prices.green_price' };
        return lines(priceNames[key] + ': Ozon Seller API, ' + (source.source || 'метод не передан') + ', поле ' + fields[key] + '.',
            source.scheme && 'Схема: ' + source.scheme + '.', source.action_ids?.length && 'Акции: ' + source.action_ids.join(', ') + '.', state(source));
    }

    function priceDates(product, keys) {
        return keys.map(key => stamp(priceNames[key], product.price?.[key]?.received_at)).join('\n');
    }

    function sheet(product, column) {
        const data = product.details || {};
        return 'Google Sheets, колонка «' + column + '», лист «' + (data.source_sheet_title || 'не передан') + '», строка ' + (data.source_row ?? 'не передана') + '.';
    }

    function buyoutSource(product) {
        const b = product.calculator?.buyout || product.cabinet?.buyout || {};
        return lines('Заказы Ozon FBO + FBS и клиентские возвраты /v1/returns/list. Отбор по исходной дате заказа; товары в пути исключены.',
            b.default_applied && 'Применён выкуп по умолчанию из настроек кабинета. Фактический: ' + pct(b.actual_percent) + '.', state(b));
    }

    function buyoutDates(product) {
        const b = product.calculator?.buyout || product.cabinet?.buyout || {};
        return lines(period(b.period_from, b.period_to), '30 завершённых дней без сегодня.', stamp('Выкуп обновлён', b.observed_at));
    }

    function advertisingDates(product, prefix) {
        const c = product.calculator || {};
        return lines('Расходы и заказы за ' + date(c.day) + '.',
            (!prefix || prefix === 'click') && stamp('Расходы за клики', c.click_updated_at),
            (!prefix || prefix === 'order') && stamp('Расходы за заказы', c.order_updated_at),
            'Дата обновления прочих видов рекламы отдельно не передаётся.', stamp('База заказов', c.updated_at), buyoutDates(product));
    }

    function advertisingWeekDates(product) {
        const week = product.calculator?.advertising_week;
        return lines(period(week?.period.start, week?.period.end), '7 завершённых дней без сегодня.',
            stamp('Самый ранний снимок рекламы за период', week?.advertising_updated_at),
            stamp('Самое раннее обновление заказов периода', week?.orders_updated_at),
            'Процент выкупа:', buyoutDates(product));
    }

    function finish(data, value, status, manual = false) {
        if (manual) data.source = lines('Задано вручную в текущем сценарии.', 'Исходный источник:', data.source);
        if (status) data.calculation = status;
        else if (value == null) data.calculation = lines(data.calculation, missing);
        return data;
    }

    function calculator(session, key, title) {
        const v = session.values, p = session.product;
        const prefix = key.split('_')[0];
        const modified = key === 'advertising_amount' ? Boolean(v.advertising_manual) : session.modified.has(key) || session.modified.has(prefix + '_percent') || session.modified.has(prefix + '_amount') || ['click', 'order'].includes(prefix) && (session.modified.has(prefix + '_drr') || session.modified.has(prefix + '_spend'));
        const d = { title, formula: '', calculation: '', source: '', dates: '', note: '' };
        const value = key === 'margin' || key === 'roi' ? v.result?.[key] : v[key];
        if (['acquiring', 'team'].includes(prefix)) {
            const amountBasis = v[prefix + '_basis'] === 'amount';
            const acquiring = prefix === 'acquiring';
            const base = acquiring ? v.wallet_price : v.seller_price;
            const priceKey = acquiring ? 'green_price' : 'seller_price';
            const priceLabel = acquiring ? 'цена с Кошельком' : 'цена с учётом акции';
            d.formula = amountBasis ? 'Ставка = сумма / ' + priceLabel + ' × 100.' : 'Сумма = ' + priceLabel + ' × ставка / 100.';
            d.calculation = amountBasis ? rub(v[prefix + '_amount']) + ' / ' + rub(base) + ' × 100 = ' + pct(v[prefix + '_percent'])
                : rub(base) + ' × ' + n(v[prefix + '_percent']) + ' / 100 = ' + rub(v[prefix + '_amount']);
            d.source = lines(acquiring ? 'Ставка: настройки кабинета «' + (p.store_name || p.store_slug || 'не указан') + '».' : sheet(p, 'ДРР %'), priceSource(p, priceKey));
            d.dates = lines(acquiring ? stamp('Настройки сохранены', session.expensesUpdatedAt) : stamp('Загрузка Sheets', p.details?.source_synced_at), priceDates(p, [priceKey]));
        } else if (['vat', 'usn'].includes(prefix)) {
            const amountBasis = v[prefix + '_basis'] === 'amount';
            if (prefix === 'vat') {
                d.formula = amountBasis ? 'Ставка НДС = сумма НДС / (цена с СПП − сумма НДС) × 100.' : 'НДС = цена с СПП × ставка НДС / (100 + ставка НДС).';
                d.calculation = amountBasis ? rub(v.vat_amount) + ' / (' + rub(v.buyer_price) + ' − ' + rub(v.vat_amount) + ') × 100 = ' + pct(v.vat_percent)
                    : rub(v.buyer_price) + ' × ' + n(v.vat_percent) + ' / (100 + ' + n(v.vat_percent) + ') = ' + rub(v.vat_amount);
            } else {
                d.formula = amountBasis ? 'Ставка УСН = сумма УСН / (цена с СПП − НДС) × 100.' : 'УСН = (цена с СПП − НДС) × ставка УСН / 100.';
                d.calculation = amountBasis ? rub(v.usn_amount) + ' / (' + rub(v.buyer_price) + ' − ' + rub(v.vat_amount) + ') × 100 = ' + pct(v.usn_percent)
                    : '(' + rub(v.buyer_price) + ' − ' + rub(v.vat_amount) + ') × ' + n(v.usn_percent) + ' / 100 = ' + rub(v.usn_amount);
                d.note = v.vat_basis === 'percent' ? 'В базе УСН сервер использует НДС без промежуточного округления; здесь сумма НДС показана до копеек.' : '';
            }
            if (!amountBasis && Number(v[prefix + '_percent']) === 0 && v[prefix + '_percent'] != null) d.calculation = 'Ставка 0% → налог 0 ₽, даже если цена неизвестна.';
            d.source = lines('Ставки: настройки кабинета «' + (p.store_name || p.store_slug || 'не указан') + '».', priceSource(p, 'black_price'));
            d.dates = lines(stamp('Настройки сохранены', session.expensesUpdatedAt), priceDates(p, ['black_price']));
        } else if (['purchase', 'fulfillment'].includes(key)) {
            d.formula = 'Готовый расход на одну штуку; дополнительного расчёта нет.';
            d.calculation = rub(value) + '/шт.';
            d.source = sheet(p, key === 'purchase' ? 'Себес, руб' : 'Проч.затр, руб');
            d.dates = stamp('Загрузка Sheets', p.details?.source_synced_at);
        } else if (key === 'buyout_percent') {
            const b = p.calculator?.buyout || p.cabinet?.buyout || {};
            d.formula = '(Получено − клиентские возвраты) / (получено + отменено) × 100.';
            d.calculation = modified ? 'Выкуп задан вручную: ' + pct(value) : b.default_applied ? 'Подстановка из настроек: ' + pct(value) + '; фактический выкуп: ' + pct(b.actual_percent)
                : '(' + n(b.received) + ' − ' + n(b.returned) + ') / (' + n(b.received) + ' + ' + n(b.cancelled) + ') × 100 = ' + pct(value);
            d.source = buyoutSource(p);
            d.dates = buyoutDates(p);
        } else if (key === 'advertising_amount') {
            const week = v.advertising_week;
            d.formula = 'Рекламные расходы за 7 дней / (заказанные штуки за те же 7 дней × выкуп / 100).';
            d.calculation = v.advertising_manual ? 'Задано на одну штуку: ' + rub(value)
                : rub(week?.spend) + ' / (' + n(week?.orders_count) + ' × ' + n(v.buyout_percent) + ' / 100) = ' + rub(value);
            d.source = lines('Ozon Performance API: клики, заказы и известные расходы прочих видов рекламы, распределённые на этот товар. Заказы FBO + FBS до вычитания отмен, по исходной дате заказа в Москве.', buyoutSource(p));
            d.dates = advertisingWeekDates(p);
            d.note = lines('Расходы и заказы берутся за последние 7 завершённых дней независимо от периода таблицы. Сегодняшние расходы и ДРР в соседних полях не меняют недельную сумму. Процент выкупа — текущее значение калькулятора.',
                !week?.complete && 'Полная недельная база недоступна; подстановка сегодняшних или частичных расходов не выполняется.',
                week?.missing_dates?.length && 'Нет полных данных за: ' + week.missing_dates.map(day => date(day)).join(', ') + '.',
                week?.partial_dates?.length && 'Неполные или устаревшие данные за: ' + week.partial_dates.map(day => date(day)).join(', ') + '.',
                week?.other_incomplete && 'В прочей рекламе учтена известная часть; неизвестные категории не считаются подтверждённым нулём.',
                week?.complete && 'Подтверждённый нулевой расход за неделю даёт 0 ₽ даже при нулевом количестве ожидаемых выкупов.');
        } else if (key === 'storage_amount') {
            const source = p.storage || {}, s = source.snapshot;
            d.formula = 'Расходы хранения / (продажи FBO − возвраты FBO).';
            d.calculation = modified ? 'Задано на одну штуку: ' + rub(value) : rub(s?.expenses) + ' / (' + n(s?.sold_units) + ' − ' + n(s?.returned_units) + ') = ' + rub(value);
            d.source = lines('Ozon: отчёт размещения /v1/report/placement/by-products/create; продажи и возвраты из /v1/finance/accrual/by-day.', state(source), state(s), s?.state === 'insufficient' && 'Нет положительного количества выкупленных единиц.');
            d.dates = lines(period(s?.period.start, s?.period.end), '30 завершённых дней без сегодня.', stamp('Снимок хранения', s?.observed_at));
        } else if (key === 'margin' || key === 'roi') {
            const costs = ['purchase', 'fulfillment', 'commission_amount', 'logistics_amount', 'storage_amount', 'advertising_amount', 'acquiring_amount', 'team_amount', 'vat_amount', 'usn_amount'];
            d.formula = key === 'roi' ? 'ROI = чистая прибыль на штуку / закупочная стоимость × 100.'
                : 'Цена с учётом акции − закупка − ФФ − комиссия Ozon − логистика − хранение − реклама − эквайринг − комиссия компании − НДС − УСН.';
            d.calculation = key === 'roi' ? rub(v.result?.margin) + ' / ' + rub(v.purchase) + ' × 100 = ' + pct(value)
                : [v.seller_price, ...costs.map(field => v[field])].map(rub).join(' − ') + ' = ' + rub(value);
            d.source = 'Серверный расчёт из текущих полей калькулятора. Ручные значения имеют приоритет. Источник каждого расхода показан в подсказке его поля.';
            d.dates = lines(priceDates(p, ['seller_price', 'black_price', 'green_price']), stamp('Загрузка Sheets', p.details?.source_synced_at), stamp('Настройки сохранены', session.expensesUpdatedAt), advertisingWeekDates(p),
                period(p.logistics?.snapshot?.period.start, p.logistics?.snapshot?.period.end), stamp('Логистика', p.logistics?.snapshot?.observed_at), stamp('Хранение', p.storage?.snapshot?.observed_at));
            d.note = lines((v.result?.missing || []).length ? 'Не хватает: ' + v.result.missing.map(field => window.OzonEconomicsUI.marginLabels[field] || field).join(', ') + '.' : '',
                !session.changed && p.current_economics?.calculation?.issues?.filter(field => !['orders_partial', 'advertising_partial'].includes(field)).map(field => window.OzonEconomicsUI.marginLabels[field] || field).join('. '));
        } else {
            const spend = '(' + rub(v.click_spend) + ' + ' + rub(v.order_spend) + ' + ' + rub(v.other_spend) + ')';
            if (key === 'total_drr') {
                d.formula = '(Расходы за клики + за заказы + прочая реклама) / (ТО после отмен × выкуп (%) / 100) × 100%.';
                d.calculation = spend + ' / (' + rub(v.turnover) + ' × ' + n(v.buyout_percent) + ' / 100) × 100 = ' + pct(value);
            } else if (key === 'other_spend') {
                d.formula = 'Сумма известных расходов прочих видов рекламы, распределённых на товар.';
                d.calculation = v.other_breakdown ? (v.other_breakdown.filter(item => item.amount != null).map(item => rub(item.amount)).join(' + ') || '0 ₽') + ' = ' + rub(value) : 'Общая сумма без разбивки: ' + rub(value);
            } else {
                const rateBasis = v[prefix + '_basis'] === 'percent';
                d.formula = rateBasis ? 'Расходы = ДРР (%) / 100 × ТО после отмен × выкуп (%) / 100.' : 'ДРР (%) = расходы / (ТО после отмен × выкуп (%) / 100) × 100.';
                d.calculation = rateBasis ? n(v[prefix + '_drr']) + ' / 100 × ' + rub(v.turnover) + ' × ' + n(v.buyout_percent) + ' / 100 = ' + rub(v[prefix + '_spend'])
                    : rub(v[prefix + '_spend']) + ' / (' + rub(v.turnover) + ' × ' + n(v.buyout_percent) + ' / 100) × 100 = ' + pct(v[prefix + '_drr']);
            }
            d.source = lines('Ozon Performance API: клики (CPC), заказы (CPO выбранных товаров, всех товаров и Комбо), прочие виды рекламы. Только расходы, распределённые на этот товар.', buyoutSource(p));
            d.dates = advertisingDates(p, ['click', 'order'].includes(prefix) ? prefix : null);
            d.note = lines(d.note, 'Неизвестные расходы не равны нулю. Прочая реклама включает известную часть; её неполнота не блокирует итог.',
                (v.other_breakdown || []).some(item => item.stale || item.state !== 'ready' || item.amount == null) && 'В составе прочей рекламы есть неполные или устаревшие данные.');
        }
        if (session.changed) d.note = lines(d.note, 'Показан текущий сценарий. Даты относятся к исходным данным; правки не сохраняются в Ozon.');
        if (session.settingsError) d.note = lines(d.note, session.settingsError);
        const status = session.error || (session.invalid.size ? 'Проверьте введённые значения.' : session.pending || session.pricePending || !session.initialized ? 'Пересчитываем…' : '');
        return finish(d, value, status, modified || key === 'advertising_amount' && v.advertising_manual);
    }

    function prices(product, session, key, title) {
        const v = session.values, category = session.category || {};
        const d = { title, formula: '', calculation: '', source: '', dates: '', note: '' };
        let keys = [key], manual = false;
        if (priceKeys.includes(key)) {
            const index = priceKeys.indexOf(key), anchor = priceKeys.indexOf(session.helpPriceAnchor);
            manual = session.helpPriceManual === key;
            if (manual || !session.helpPriceAnchor || !session.helpPriceManual && index <= anchor) {
                d.formula = manual ? 'Опорная цена задана вручную. Остальные цены пересчитываются с сохранением скидок.'
                    : session.helpPriceAnchor ? 'Цена сохранена при изменении скидки ниже по цепочке.' : 'Готовая цена из API Ozon.';
                d.calculation = rub(v[key]);
            } else {
                const backwards = anchor > index;
                const parent = priceKeys[backwards ? index + 1 : index - 1];
                const discount = backwards ? index : index - 1;
                keys = [parent, key];
                d.formula = priceNames[parent] + (backwards ? ' / ' : ' × ') + '(1 − ' + discountNames[discount] + ' / 100).';
                d.calculation = rub(v[parent]) + (backwards ? ' / ' : ' × ') + '(1 − ' + n(v[discountKeys[discount]]) + ' / 100) = ' + rub(v[key]);
                if (backwards && Number(v[discountKeys[discount]]) === 100) d.calculation = 'При скидке 100% обратное деление невозможно; сервер сохраняет предыдущую базу: ' + rub(v[key]);
            }
        } else if (key === 'spp_percent' || key === 'wallet_percent') {
            keys = key === 'spp_percent' ? ['seller_price', 'black_price'] : ['black_price', 'green_price'];
            manual = session.helpDiscounts?.has(key);
            d.formula = '(1 − ' + priceNames[keys[1]] + ' / ' + priceNames[keys[0]] + ') × 100.';
            d.calculation = manual ? 'Скидка задана вручную: ' + pct(v[key]) : '(1 − ' + rub(v[keys[1]]) + ' / ' + rub(v[keys[0]]) + ') × 100 = ' + pct(v[key]);
        } else {
            keys = ['seller_price'];
            manual = v.commission_mode === 'manual';
            const amountBasis = manual && session.helpCommissionBasis === 'commission_amount';
            d.formula = amountBasis ? 'Ставка = комиссия в рублях / цена с учётом акции × 100.' : 'Комиссия = цена с учётом акции × ставка FBO / 100.';
            d.calculation = amountBasis ? rub(v.commission_amount) + ' / ' + rub(v.seller_price) + ' × 100 = ' + pct(v.commission_percent)
                : rub(v.seller_price) + ' × ' + n(v.commission_percent) + ' / 100 = ' + rub(v.commission_amount);
            d.source = lines('Исходная ставка: Ozon /v5/product/info/prices, commissions.sales_percent_fbo.',
                category.selected && 'Категория справочника: ' + category.text + '.',
                category.catalog && 'Справочник: ' + category.catalog.source_url,
                'Автоматический режим: до 100 ₽ — 17%, свыше 100 до 300 ₽ — 23%, с исключениями категории; выше 300 ₽ — обычная ставка товара или выбранной категории.', state(product.commission));
            d.dates = lines(stamp('Ставка из API', product.commission?.received_at), category.catalog && 'Тариф действует с ' + date(category.catalog.effective_from) + '; проверен ' + date(category.catalog.verified_on) + '.');
        }
        d.source = lines(d.source, keys.map(field => priceSource(product, field)).join('\n'));
        d.dates = lines(d.dates, priceDates(product, keys));
        d.note = lines('Вычисления выполняет сервер. Пояснение показывает до 6 знаков; денежный результат округляется до копеек.',
            session.modified && 'Текущий сценарий открытой карточки. Даты относятся к исходным данным.', category.error,
            category.categoryError && 'Категория товара не загружена.');
        return finish(d, v[key], session.helpError || (session.invalid.size ? 'Проверьте введённые значения.' : session.helpPending || !session.initialized ? 'Пересчитываем…' : ''), manual);
    }

    function category(session) {
        const c = session.category || {};
        return { title: 'Категория / тип Ozon', formula: 'Категория и ценовой диапазон определяют тариф комиссии FBO.',
            calculation: c.text || 'Категория не выбрана.',
            source: lines(c.touched ? 'Категория выбрана вручную для сценария.' : 'Категория товара: Ozon /v3/product/info/list и /v1/description-category/tree.', c.catalog?.source_url, c.error, c.categoryError && 'Не удалось загрузить категорию карточки.'),
            dates: lines(stamp('Категория получена', c.identity?.received_at), 'Тариф действует с ' + date(c.catalog?.effective_from) + '; проверен ' + date(c.catalog?.verified_on) + '.'),
            note: 'Выбор не изменяет категорию товара в Ozon.' };
    }

    function logistics(session, key, title) {
        const s = session.snapshot, c = session.changed;
        const manual = Object.hasOwn(c, key);
        const inputs = session.result?.inputs || s?.inputs || {};
        const get = field => Object.hasOwn(c, field) ? c[field] : field === 'markup' && !s ? 0 : inputs[field];
        const value = key === 'actual' ? s?.actual_per_unit : key === 'modeled' ? s?.modeled_per_unit
            : key === 'total' ? manual ? c.total : Object.keys(c).length ? session.result?.total : s?.actual_per_unit
            : key === 'volume_l' ? Number(s?.volume_l) > 0 ? s.volume_l : null : get(key);
        const d = { title, formula: '', calculation: '',
            source: lines('Ozon FBO: /v1/finance/accrual/postings, отправления /v3/posting/fbo/list и возвраты /v1/returns/list.', state(session.source), state(s)),
            dates: lines(period(s?.period.start, s?.period.end), '30 завершённых дней без сегодня.', stamp('Снимок логистики', s?.observed_at)), note: '' };
        if (key === 'volume_l') {
            d.formula = 'Объём упаковки = ширина × высота × глубина (мм) / 1 000 000.';
            d.calculation = n(value) + ' л. Исходные размеры отдельно в калькулятор не передаются.';
            d.source = 'Ozon /v4/product/info/attributes, габариты упаковки.';
            d.note = 'Справочный объём из дневного снимка. Логистика рассчитана по начислениям Ozon; объём здесь не редактируется.';
        } else if (key === 'buyout_percent') {
            d.formula = '(Получено − возвращено) / единицы с расходами на доставку × 100.';
            d.calculation = '(' + n(s?.received_units) + ' − ' + n(s?.returned_units) + ') / ' + n(s?.paid_units) + ' × 100 = ' + pct(value);
            d.note = 'Отдельный выкуп среди платных доставок FBO. Незавершённые платные доставки входят в знаменатель.';
        } else if (key === 'actual' || key === 'total' && !Object.keys(c).length) {
            d.formula = 'Все учтённые расходы доставки / (получено − возвращено).';
            d.calculation = rub(s?.total_expenses) + ' / (' + n(s?.received_units) + ' − ' + n(s?.returned_units) + ') = ' + rub(value);
            d.note = 'Учитываются начисления внутри окна по отправлениям этого окна. Поздние начисления и более старые отправления могут не входить в снимок.';
        } else if (key === 'total' || key === 'modeled') {
            const v = key === 'modeled' ? s?.inputs || {} : { ...inputs, ...c };
            d.formula = '(Туда + наценка) / доля выкупа + (обратно + обработка) × (1 − доля выкупа) / доля выкупа + до места выдачи + прочие. Доля выкупа = выкуп / 100.';
            d.calculation = '(' + rub(v.forward) + ' + ' + rub(v.markup) + ') / (' + n(v.buyout_percent) + ' / 100) + (' + rub(v.reverse) + ' + ' + rub(v.handling) + ') × (1 − ' + n(v.buyout_percent) + ' / 100) / (' + n(v.buyout_percent) + ' / 100) + ' + rub(v.last_mile) + ' + ' + rub(v.extra) + ' = ' + rub(value);
            if (Number(v.buyout_percent) === 100) d.calculation = rub(v.forward) + ' + ' + rub(v.markup) + ' + ' + rub(v.last_mile) + ' + ' + rub(v.extra) + ' = ' + rub(value) + '. При выкупе 100% обратные расходы не требуются.';
            d.note = 'Оценка моделирует обратную перевозку для каждой неоставленной единицы; она может отличаться от фактических начислений.';
        } else if (key === 'markup') {
            d.formula = 'Дополнительная наценка на доставку для сценария. Исходно 0 ₽.';
            d.calculation = rub(value);
        } else {
            const counts = { forward: 'forward_units', reverse: 'reverse_units', handling: 'reverse_units', last_mile: 'retained_units', extra: 'retained_units' };
            const labels = { forward: 'оплаченные единицы туда', reverse: 'оплаченные единицы обратно', handling: 'обратные перевозки', last_mile: 'оставленные покупателями единицы', extra: 'оставленные покупателями единицы' };
            d.formula = 'Сумма начислений этой составляющей / ' + labels[key] + '.';
            const charges = s?.charges.filter(item => item.part === key) || [];
            d.calculation = '(' + (charges.length ? charges.map(item => rub(item.amount)).join(' + ') : s ? '0 ₽' : '—') + ') / ' + n(s?.[counts[key]]) + ' = ' + rub(value);
            if (key === 'handling') d.note = 'Обработка распределяется на обратные перевозки; фиксированные 15 ₽ автоматически не добавляются.';
        }
        if (manual) d.calculation = 'Задано вручную: ' + (key === 'buyout_percent' ? pct(value) : rub(value));
        d.note = lines(d.note, key !== 'volume_l' && session.helpMessage, 'В пояснении до 6 знаков; поле калькулятора показывает до 2 знаков.');
        const reference = ['actual', 'modeled', 'volume_l'].includes(key);
        return finish(d, value,
            !reference && session.helpMessage && !session.result && Object.keys(c).length ? session.helpMessage : '', manual);
    }

    return { calculator, prices, category, logistics };
})();
