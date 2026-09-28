(() => {
  const list = document.getElementById('catalog-list');
  const detail = document.getElementById('catalog-detail');
  const message = document.getElementById('catalog-message');
  const search = document.getElementById('catalog-search');
  let methods = [], selected;
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  const typeName = value => value.split('|').map(t => ({str:'Текст', number:'Число', integer:'Целое число', bool:'Да / нет', boolean:'Да / нет', date:'Дата YYYY-MM-DD', datetime:'Дата и время ISO 8601', array:'Список', object:'Объект', null:'нет данных'}[t] || t)).join(' / ');
  function table(headers, rows) {
    const wrap = node('div'); wrap.className = 'agent-table-wrap';
    const t = node('table'), head = node('thead'), tr = node('tr');
    headers.forEach(h => tr.append(node('th', h))); head.append(tr); t.append(head);
    const body = node('tbody');
    rows.forEach(values => { const row = node('tr'); values.forEach(value => row.append(node('td', value))); body.append(row); });
    t.append(body); wrap.append(t); return wrap;
  }
  function renderList() {
    list.replaceChildren();
    const term = search.value.toLocaleLowerCase();
    const visible = methods.filter(m => (m.title+' '+m.description+' '+m.name+' '+[...m.fields,...Object.values(m.marketplace_guides || {}).flatMap(g=>g.fields)].map(f=>f.path+' '+f.description).join(' ')).toLocaleLowerCase().includes(term));
    visible.forEach(m => {
      const button = node('button', m.title); button.type = 'button';
      button.setAttribute('aria-pressed', String(selected === m.name));
      button.onclick = () => renderMethod(m); list.append(button);
    });
    if (!visible.length) list.append(node('p', 'Методы не найдены.'));
  }
  function renderMethod(m) {
    selected = m.name; renderList(); detail.replaceChildren();
    detail.append(node('h2', m.title), node('code', 'GET '+m.path));
    const platforms=['WB','YANDEX MARKET'];
    const supported=mp=>Boolean(m.marketplace_support?.[mp]);
    const guide=mp=>m.marketplace_guides?.[mp] || m;
    const maps=platforms.map(mp=>new Map((supported(mp)?guide(mp).fields:[]).map(f=>[f.path,f])));
    const comparisonRows=CheckStockCatalogComparison.rows(m.name,maps);
      const description=row=> {
        if(row.label) {
          const types=[...new Set(row.fields.filter(Boolean).map(f=>typeName(f.type)))];
          const notes=[...new Set(row.fields.filter(Boolean).map(f=>f.notes).filter(n=>n && !['—','Яндекс Маркет'].includes(n)))];
          return [row.label,types.join(' / ')+(row.unit?' · '+row.unit:''),...notes,row.note].filter(Boolean).join('\n');
        }
        const descriptions=row.fields.map(f=> {
          if(!f) return '';
          const note=f.notes && !['—','Яндекс Маркет'].includes(f.notes)?f.notes:'';
          return [f.description,typeName(f.type)+(f.unit && f.unit!=='—'?' · '+f.unit:''),note].filter(Boolean).join('\n');
        });
        if(!descriptions[0] || !descriptions[1] || descriptions[0]===descriptions[1]) return descriptions[0] || descriptions[1];
        return 'WB: '+descriptions[0]+'\n\nЯндекс Маркет: '+descriptions[1];
      };
      const fieldPaths=row=>row.paths[0]===row.paths[1]?row.paths[0]:row.fields.map((f,i)=>f?(i===0?'WB: ':'Яндекс: ')+row.paths[i]:null).filter(Boolean).join('\n\n');
      const comparison=table(['Поле в JSON','Описание поля','WB','Яндекс Маркет'],comparisonRows.map(row=>[fieldPaths(row),description(row),...row.fields.map(f=>f?'Есть':'Нет')]));
      comparison.classList.add('catalog-comparison');
      comparison.querySelectorAll('tbody tr').forEach(row=> {
        [2,3].forEach(index=> {
          const cell=row.cells[index], badge=node('span',cell.textContent);
          badge.className=cell.textContent==='Есть'?'catalog-availability catalog-availability--yes':'catalog-availability catalog-availability--no';
          cell.replaceChildren(badge);
        });
      });
      detail.append(comparison);
    if(m.envelope_fields.length) {
      const block=node('details'), title=node('summary','Общие поля ответа: период, полнота и следующая порция');
      block.append(title,table(['Поле в JSON','Назначение','Тип'],m.envelope_fields.map(f=>[f.path,f.description,typeName(f.type)])));detail.append(block);
    }
    const params=node('details');params.append(node('summary','Параметры запроса — справочник'));
    if(!m.parameters.length) params.append(node('p','Параметры не требуются.'));
    else params.append(table(['Параметр','Обязательный','Формат / значения','Условия'],m.parameters.map(p=> {
      const s=p.schema || {}, variants=s.anyOf || [], v=variants.find(x=>x.type !== 'null') || s;
      const hints={store:'Код доступного магазина. Для отчётов обычно можно пропустить при точном article; в сводке — все доступные магазины.', marketplace:'Код площадки из доступных областей метода.', date_from:'Начало периода включительно; при указании периода нужны обе даты.',date_to:'Конец периода включительно; обычно период не более 90 дней.', article:'Точный артикул товара.', limit:'Строк в одном ответе (до 100).', offset:'Смещение от начала, с нуля. Для продолжения передайте next_offset.',order:'asc — по возрастанию, desc — по убыванию.'};
      const format=(v.enum || []).join(', ') || typeName(v.format || v.type || 'str');
      return [p.name,p.required?'Да':'Нет',format,(hints[p.name] || p.description || '—')+(s.default !== undefined && s.default !== null ? ' По умолчанию: '+s.default+'.' : '')];
    })));detail.append(params);
  }
  search.oninput=renderList;
  fetch('/api/ai-agents/catalog',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
    .then(async r=>{if(!r.ok) throw new Error();return r.json();})
    .then(data=>{methods=data.methods;message.textContent='Доступно методов: '+methods.length;if(methods.length) renderMethod(methods[0]);else message.textContent='Нет доступных методов. Обратитесь к администратору за доступом к магазинам и разделам.';})
    .catch(()=>{message.textContent='Не удалось загрузить каталог. Проверьте вход на сайт и обновите страницу.';});
})();
