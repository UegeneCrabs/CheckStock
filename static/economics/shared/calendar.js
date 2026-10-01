(function () {
    'use strict';
    const config = JSON.parse(document.getElementById('uec-config').textContent);
    const el = name => document.getElementById('uec-' + name);
    const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});
    const display = value => value == null ? 'Нет данных' : typeof value === 'number' ? number.format(value) : value === 'usn' ? 'УСН' : value === 'osno' ? 'ОСНО' : String(value);
    const dateTime = value => value ? new Date(value).toLocaleString('ru-RU', {timeZone:'Europe/Moscow'}) + ' МСК' : 'Время не сохранено';
    let page = 1, data = null, current = null, sequence = 0, cellSequence = 0, timer;
    const computed = [
        {key:'margin', label:'Маржинальность', unit:'₽/шт. · без рекламы'},
        {key:'roi', label:'ROI', unit:'% · без рекламы'},
        {key:'day_profit', label:'Прибыль за день', unit:'₽ · после рекламы'}
    ];
    async function api(path, options) {
        const response = await fetch(config.endpoint + path, {headers:{'Content-Type':'application/json','Accept':'application/json'}, ...options});
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : body.error || 'Не удалось выполнить запрос.');
        return body;
    }
    function params() { return new URLSearchParams({store:el('store').value, day:el('day').value}); }
    async function load() {
        const request = ++sequence;
        el('status').textContent = 'Загрузка…';
        el('rows').replaceChildren();
        data = null;
        const q = params(); q.set('q', el('search').value); q.set('page', page); q.set('page_size', el('size').value);
        try {
            const result = await api('?' + q);
            if (request !== sequence) return;
            data = result;
            el('day').min = data.first_day || config.today;
            el('status').textContent = data.preliminary ? 'Сегодня · предварительные значения' : 'Сохранённые значения за день';
            el('note').textContent = (data.first_day ? 'История с ' + data.first_day + '. ' : 'Дневных снимков пока нет. ') + 'Нажмите на ячейку: источник, время и история. Красным отмечено отсутствие; 0 — сохранённый ноль. Полнота не означает проверку человеком.';
            el('summary').textContent = 'Полный расчёт маржинальности: ' + data.complete + ' из ' + data.total + ' товаров. Прибыль за ' + data.day + ': ' + (data.day_profit == null ? 'Недостаточно данных' : display(data.day_profit) + ' ₽' + (data.profit_complete ? '' : ' · Неполный расчёт')) + '.';
            el('summary').classList.toggle('uec-error', !data.profit_complete);
            el('incomplete').hidden = data.profit_complete || !data.profit_missing.length;
            el('incomplete').querySelector('ul').innerHTML = data.profit_missing.map(p=>'<li>'+escape(p.article)+': '+escape(p.parameters.map(k=>(data.fields.find(f=>f.key===k)||{}).label || (k==='snapshot'?'нет дневного снимка':k)).join(', '))+(p.unavailable?' · не вошёл в итог: недостаточно данных':'')+'</li>').join('');
            const cols = [...computed.slice(0,2), ...data.fields, computed[2]];
            el('head').innerHTML = '<tr><th>Товар<small>Фото · название · артикул · баркод</small></th>' + cols.map(f => '<th>' + escape(f.label) + '<small>' + escape(f.unit) + '</small></th>').join('') + '</tr>';
            el('rows').innerHTML = data.rows.map((row, index) => {
                const p = row.product;
                const photo = /^https?:\/\//i.test(p.image_url || '') ? '<img loading="lazy" src="' + escape(p.image_url) + '" alt="" />' : '<span class="uec-no-photo" aria-label="Нет фотографии"></span>';
                const codes = (p.barcodes && p.barcodes.length ? p.barcodes : [p.barcode]).filter(Boolean);
                const product = '<div class="uec-product">' + photo + '<div><strong title="' + escape(p.name) + '">' + escape(p.name || p.article) + '</strong><button class="uec-copy" data-copy="' + escape(p.article) + '" title="Скопировать артикул">' + escape(p.article) + ' ⧉</button>' + codes.map(c => '<button class="uec-copy" data-copy="' + escape(c) + '" title="Скопировать баркод">' + escape(c) + ' ⧉</button>').join('') + '</div></div>';
                return '<tr><td>' + product + '</td>' + cols.map(f => {
                    const isComputed = computed.some(c => c.key === f.key);
                    const v = (isComputed ? row.result : row.values)[f.key];
                    const corrected = row.corrected.includes(f.key);
                    const partial = isComputed && (f.key === 'day_profit' ? (row.result.daily_missing || []).length : (row.result.missing || []).length);
                    const text = v == null && isComputed ? 'Недостаточно данных' : display(v);
                    return '<td><button class="uec-cell' + (v == null || partial ? ' is-missing' : '') + (corrected ? ' is-corrected' : '') + (v < 0 ? ' uec-negative' : '') + '" data-row="' + index + '" data-field="' + f.key + '" aria-label="' + escape(f.label + ': ' + text) + '">' + escape(text) + (partial && v != null ? '<small class="uec-partial">Неполный расчёт</small>' : '') + (corrected ? '<small>Корректировка</small>' : '') + '</button></td>';
                }).join('') + '</tr>';
            }).join('');
            if (!data.rows.length) el('rows').innerHTML = '<tr><td colspan="' + (cols.length+1) + '" style="padding:28px">Товары не найдены в пределах ваших прав и фильтра.</td></tr>';
            el('page-summary').textContent = 'Страница ' + page + ' · товаров ' + data.total;
            el('prev').disabled = page <= 1;
            el('next').disabled = page * Number(el('size').value) >= data.total;
        } catch (error) { if (request === sequence) el('status').textContent = error.message; }
    }
    async function openCell(index, field) {
        if (!data) return;
        const row = data.rows[index], meta = [...data.fields,...computed].find(f=>f.key===field);
        const request = ++cellSequence;
        current = null;
        el('cell-title').textContent = meta.label + (meta.unit ? ', ' + meta.unit : '');
        el('cell-product').textContent = row.product.name + ' · ' + row.product.article + ' · ' + data.day;
        el('cell-content').textContent = 'Загрузка истории…';
        el('error').textContent = ''; el('edit').hidden = true; el('preview').hidden = true; el('raw').hidden = true;
        el('reason').value = ''; el('raw').open = false;
        el('cell').showModal();
        if (!row.snapshot) { el('cell-content').textContent = 'Нет сохранённого снимка за этот день. Исторические параметры неизвестны.'; return; }
        const q = params(); q.set('article', row.product.article);
        try {
            const saved = await api('/cell?' + q);
            if (request !== cellSequence) return;
            const isInput = data.fields.some(f=>f.key===field);
            current = {saved, field, article:row.product.article, day:data.day, store:el('store').value};
            const origin = saved.source.origins[field] || (isInput ? 'Сохранённый дневной снимок' : 'Расчёт из входных параметров выбранного дня');
            const actual = (isInput ? saved.values : saved.result)[field];
            el('cell-content').innerHTML = '<dl><dt>Действующее значение</dt><dd><strong>' + escape(display(actual)) + '</strong></dd><dt>Источник</dt><dd>' + escape(origin) + '</dd><dt>Снимок сохранён</dt><dd>' + escape(dateTime(saved.source.captured_at)) + '</dd><dt>Получено от источника</dt><dd>' + escape(dateTime((saved.source_times||{})[field])) + '</dd>' + (isInput ? '<dt>Первоначальное значение</dt><dd>' + escape(display(saved.original.values[field])) + '</dd><dt>Последнее значение источника</dt><dd>' + escape(display(saved.source.values[field])) + '</dd>' : '') + '<dt>Версия расчёта</dt><dd>' + escape(saved.version || 'Исходная') + '</dd></dl>';
            if (saved.source.basis === 'legacy') el('cell-content').innerHTML += '<p class="uec-note">Ранее сохранённый снимок. Дата сохранения указана выше; исходный сбор за эту дату не подтверждается автоматически.</p>';
            if (actual == null && ['retail_price','customer_price'].includes(field) && saved.values.price_missing_reason) el('cell-content').innerHTML += '<p class="uec-error">' + escape(saved.values.price_missing_reason) + '</p>';
            const missing = field === 'day_profit' ? saved.result.daily_missing || [] : saved.result.missing || [];
            if (actual == null || !isInput && missing.length) {
                el('cell-content').innerHTML += '<p class="uec-error">' + escape(field === 'roi' && saved.values.purchase_price === 0 ? 'ROI не определён при нулевой закупке.' : missing.length ? (actual == null ? 'Недостаточно данных. ' : 'Неполный расчёт. ') + 'Не учтены / неизвестны за ' + data.day + ': ' + [...new Set(missing)].map(k => (data.fields.find(f=>f.key===k)||{}).label || k).join(', ') : 'Источник не сохранил значение.') + '</p>';
            }
            const events = (saved.events || []).filter(e => !isInput || e.kind === 'source' || e.field === field);
            el('cell-content').innerHTML += '<h3>История значения</h3><ol class="uec-history"><li>Первый снимок: ' + escape(dateTime(saved.original.captured_at)) + '</li>' + events.map(e => '<li>' + escape(dateTime(e.at)) + ' · ' + escape(e.actor) + ' · ' + escape(e.kind === 'source' ? 'Получены данные источника' : e.kind === 'undo' ? 'Отмена корректировки' : 'Корректировка') + (isInput ? ': ' + escape(display(e.kind === 'source' ? (e.before || {})[field] : e.before)) + ' → ' + escape(display(e.kind === 'source' ? (e.after || {})[field] : e.after)) : '') + (e.reason ? '<br>' + escape(e.reason) : '') + '</li>').join('') + '</ol>';
            el('edit').hidden = !(isInput && meta.editable && data.can_edit);
            if (isInput && !meta.editable) el('cell-content').innerHTML += '<p class="uec-note">Справочный или вычисляемый параметр. Для пересчёта измените исходные входы.</p>';
            el('effective-day').textContent = data.day;
            el('value').value = saved.values[field] == null ? '' : String(saved.values[field]);
            el('value').placeholder = field === 'tax_system' ? 'usn или osno' : '0';
            el('undo').hidden = !(field in saved.overrides);
            el('raw').hidden = false;
            el('raw').querySelector('pre').textContent = JSON.stringify({original:saved.original, latest:saved.source}, null, 2);
        } catch (error) { if (request === cellSequence) el('cell-content').textContent = error.message; }
    }
    function draft(undo) {
        const text = el('value').value.trim().replace(',', '.');
        if (!undo && current.field !== 'tax_system' && (!text || !Number.isFinite(Number(text)))) throw new Error('Введите число; пустое значение не является нулём.');
        if (el('reason').value.trim().length < 3) throw new Error('Укажите причину (не менее 3 символов).');
        return {store:current.store, day:current.day, article:current.article, field:current.field, token:current.saved.token, value:undo ? null : current.field === 'tax_system' ? text : Number(text), reason:el('reason').value.trim(), undo};
    }
    async function preview(undo) {
        el('error').textContent = ''; el('preview').hidden = true;
        try {
            const context = current, body = draft(undo);
            const result = await api('/preview', {method:'POST',body:JSON.stringify(body)});
            if (current !== context) return;
            el('preview').hidden = false;
            const lines = [{label:'Значение',before:result.before,after:result.after}, ...computed.map(f=>({label:f.label + ', ' + f.unit,before:result.before_result[f.key],after:result.after_result[f.key]}))];
            el('preview').innerHTML = '<strong>' + (undo ? 'Подтвердите отмену корректировки' : 'Подтвердите корректировку') + ' · ' + escape(body.day) + '</strong><p>' + escape(body.reason) + '</p><table><thead><tr><th>Показатель</th><th>Было</th><th>Станет</th></tr></thead><tbody>' + lines.map(r=>'<tr><td>'+escape(r.label)+'</td><td>'+escape(display(r.before))+'</td><td>'+escape(display(r.after))+'</td></tr>').join('') + '</tbody></table><p class="uec-error">' + escape([...(result.after_result.messages||[]),...(result.after_result.daily_messages||[])].join(' ')) + '</p><button class="ue1c-primary-button" id="uec-apply">Применить к выбранной дате</button>';
            el('apply').onclick = async () => {
                el('apply').disabled = true;
                try {
                    if (JSON.stringify(draft(undo)) !== JSON.stringify(body)) throw new Error('Параметры изменились. Проверьте влияние ещё раз.');
                    await api('/correction', {method:'POST',body:JSON.stringify({...body, preview_token:result.preview_token})});
                    el('cell').close(); current = null; await load();
                } catch (error) { el('error').textContent = error.message; el('preview').hidden = true; }
            };
            el('preview').scrollIntoView({block:'nearest'});
        } catch (error) { el('error').textContent = error.message; }
    }
    el('edit').onsubmit = event => {event.preventDefault(); preview(false);};
    el('undo').onclick = () => preview(true);
    el('value').oninput = el('reason').oninput = () => {el('preview').hidden = true;};
    el('close').onclick = () => el('cell').close();
    el('cell').addEventListener('close', () => {++cellSequence; current=null;});
    el('rows').onclick = async event => {
        const copy = event.target.closest('[data-copy]');
        if (copy) { try {await navigator.clipboard.writeText(copy.dataset.copy); el('status').textContent='Скопировано: '+copy.dataset.copy;} catch (_) {el('status').textContent='Не удалось скопировать.';} return; }
        const cell = event.target.closest('[data-field]'); if (cell) openCell(Number(cell.dataset.row), cell.dataset.field);
    };
    el('store').innerHTML = config.stores.map(s=>'<option value="'+escape(s.slug)+'">'+escape(s.name)+'</option>').join('');
    el('day').value = config.today; el('day').max = config.today;
    el('store').onchange = () => {page=1; el('day').value=config.today; load();};
    el('day').onchange = () => {if(el('day').value && el('day').reportValidity()) {page=1; load();}};
    el('today').onclick = () => {el('day').value=config.today; page=1; load();};
    el('search').oninput = () => {clearTimeout(timer); timer=setTimeout(()=>{page=1;load();},250);};
    el('size').onchange = () => {page=1;load();};
    el('prev').onclick = () => {if(page>1){page--;load();}};
    el('next').onclick = () => {page++;load();};
    if(config.stores.length) load(); else el('status').textContent='Нет доступных магазинов.';
})();
