(() => {
  const list = document.getElementById('catalog-list');
  const detail = document.getElementById('catalog-detail');
  const message = document.getElementById('catalog-message');
  const search = document.getElementById('catalog-search');
  const controls = Array.from(document.querySelectorAll('#catalog-marketplaces button'));
  const platforms = ['WB', 'YANDEX MARKET', 'OZON'];
  const labels = ['Wildberries', 'Яндекс Маркет', 'Ozon'];
  let methods = [], selected, marketplace = 'all';
  const supported = (method, mp) => Boolean(method.marketplace_support?.[mp]);
  const guide = (method, mp) => method.marketplace_guides?.[mp] || method;
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  const typeName = value => value.split('|').map(t => ({str:'Текст', string:'Текст', number:'Число', integer:'Целое число', bool:'Да / нет', boolean:'Да / нет', date:'Дата YYYY-MM-DD', datetime:'Дата и время ISO 8601', array:'Список', object:'Объект', null:'нет данных'}[t] || t)).join(' / ');
  function table(headers, rows) {
    const wrap = node('div'); wrap.className = 'agent-table-wrap';
    const t = node('table'), head = node('thead'), tr = node('tr');
    headers.forEach(h => tr.append(node('th', h))); head.append(tr); t.append(head);
    const body = node('tbody');
    rows.forEach(values => { const row = node('tr'); values.forEach(value => row.append(node('td', value))); body.append(row); });
    t.append(body); wrap.append(t); return wrap;
  }
  const fieldDescription = field => [field.description,typeName(field.type)+(field.unit && field.unit!=='—'?' · '+field.unit:''),field.notes && !['—','Яндекс Маркет'].includes(field.notes)?field.notes:''].filter(Boolean).join('\n');
  function eligible(method) {
    return marketplace === 'all' || (supported(method, marketplace) && method.scopes.some(scope => scope.marketplace === marketplace));
  }
  function visibleMethods() {
    const term = search.value.trim().toLocaleLowerCase();
    return methods.filter(method => {
      if (!eligible(method)) return false;
      const guides = marketplace === 'all' ? platforms.filter(mp => supported(method, mp)).map(mp => guide(method, mp)) : [guide(method, marketplace)];
      const content = guides.flatMap(item => [item.title || method.title,item.description,...item.fields.map(field => field.path+' '+field.description+' '+(field.notes || ''))]);
      return [method.name,...content].join(' ').toLocaleLowerCase().includes(term);
    });
  }
  function renderList() {
    list.replaceChildren();
    const visible = visibleMethods();
    visible.forEach(m => {
      const doc = marketplace === 'all' ? m : guide(m, marketplace);
      const button = node('button'); button.type = 'button';
      button.append(node('span', doc.title || m.title));
      if (marketplace === 'all') {
        const badges = node('span'); badges.className = 'catalog-marketplace-labels';
        platforms.forEach((mp,index) => { if (supported(m,mp)) badges.append(node('span',labels[index])); });
        button.append(badges);
      }
      button.setAttribute('aria-pressed', String(selected === m.name));
      button.onclick = () => renderMethod(m); list.append(button);
    });
    if (!visible.length) list.append(node('p', 'Методы не найдены.'));
  }
  function renderMethod(m) {
    selected = m.name; renderList(); detail.replaceChildren();
    const doc = marketplace === 'all' ? m : guide(m, marketplace);
    detail.append(node('h2', doc.title || m.title), node('code', 'GET '+m.path), node('p', doc.description));
    if (marketplace !== 'all') {
      const fields = table(['Поле в JSON','Описание поля'],doc.fields.map(field=>[field.path,fieldDescription(field)]));
      fields.classList.add('catalog-fields');
      detail.append(fields);
    } else {
      const maps=platforms.map(mp=>new Map((supported(m,mp)?guide(m,mp).fields:[]).map(f=>[f.path,f])));
      const comparisonRows=CheckStockCatalogComparison.rows(m.name,maps);
      const description=row=> {
        if(row.label) {
          const types=[...new Set(row.fields.filter(Boolean).map(f=>typeName(f.type)))];
          const notes=[...new Set(row.fields.filter(Boolean).map(f=>f.notes).filter(n=>n && !['—','Яндекс Маркет'].includes(n)))];
          return [row.label,types.join(' / ')+(row.unit?' · '+row.unit:''),...notes,row.note].filter(Boolean).join('\n');
        }
        const descriptions=row.fields.map(f=>f ? fieldDescription(f) : '');
        const unique=[...new Set(descriptions.filter(Boolean))];
        return unique.length===1 ? unique[0] : descriptions.map((text,i)=>text ? labels[i]+': '+text : '').filter(Boolean).join('\n\n');
      };
      const fieldPaths=row=> {
        const unique=[...new Set(row.paths.filter((path,index)=>path && row.fields[index]))];
        return unique.length===1 ? unique[0] : row.fields.map((f,i)=>f?labels[i]+': '+row.paths[i]:null).filter(Boolean).join('\n\n');
      };
      const comparison=table(['Поле в JSON','Описание поля',...labels],comparisonRows.map(row=>[fieldPaths(row),description(row),...row.fields.map(f=>f?'Есть':'Нет')]));
      comparison.classList.add('catalog-comparison');
      comparison.querySelectorAll('tbody tr').forEach(row=> {
        [2,3,4].forEach(index=> {
          const cell=row.cells[index], badge=node('span',cell.textContent);
          badge.className=cell.textContent==='Есть'?'catalog-availability catalog-availability--yes':'catalog-availability catalog-availability--no';
          cell.replaceChildren(badge);
        });
      });
      detail.append(comparison);
    }
    if(m.envelope_fields.length) {
      const block=node('details'), title=node('summary','Общие поля ответа: период, полнота и следующая порция');
      block.append(title,table(['Поле в JSON','Назначение','Тип'],m.envelope_fields.map(f=>[f.path,f.description,typeName(f.type)])));detail.append(block);
    }
    const params=node('details');params.append(node('summary','Параметры запроса — справочник'));
    if(!m.parameters.length) params.append(node('p','Параметры не требуются.'));
    else params.append(table(['Параметр','Обязательный','Формат / значения','Условия'],m.parameters.map(p=> {
      const s=p.schema || {}, variants=s.anyOf || [], v=variants.find(x=>x.type !== 'null') || s;
      const hints={store:'Код доступного магазина. Для отчётов обычно можно пропустить при точном article; в сводке — все доступные магазины.', marketplace:'Код площадки из доступных областей метода.', date_from:'Начало периода включительно; при указании периода нужны обе даты.',date_to:'Конец периода включительно; обычно период не более 90 дней.', article:'Точный артикул товара.', limit:'Строк в одном ответе (до 100).', offset:'Смещение от начала, с нуля. Для продолжения передайте next_offset.',order:'asc — по возрастанию, desc — по убыванию.'};
      const format=p.name==='marketplace' && marketplace!=='all' ? marketplace : (v.enum || []).join(', ') || typeName(v.format || v.type || 'str');
      const fallback=s.default !== undefined && s.default !== null ? ' По умолчанию API: '+s.default+'.' : '';
      const hint=p.name==='marketplace' && marketplace!=='all' ? 'Для этой площадки передайте marketplace='+marketplace+'. Выбор раздела меняет только документацию.' : hints[p.name] || p.description || '—';
      return [p.name,p.required?'Да':'Нет',format,hint+fallback];
    })));detail.append(params);
  }
  function refresh(initial=false) {
    const visible=visibleMethods(), count=methods.filter(eligible).length;
    message.textContent=search.value.trim() ? 'Найдено методов: '+visible.length+' из '+count : 'Доступно методов: '+count;
    if (initial && visible.length) selected=visible[0].name;
    const current=visible.find(method=>method.name===selected);
    if (current) renderMethod(current);
    else {
      selected=undefined;
      detail.replaceChildren(node('p',visible.length ? 'Выберите метод из списка для выбранной площадки.' : 'Нет методов по выбранной площадке и условиям поиска.'));
      renderList();
    }
  }
  controls.forEach(control=> {
    control.disabled=true;
    control.onclick=()=> {
      marketplace=control.dataset.marketplace;
      controls.forEach(item=>item.setAttribute('aria-pressed',String(item===control)));
      refresh();
    };
  });
  search.disabled=true;
  search.oninput=()=>refresh();
  fetch('/api/ai-agents/catalog',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}})
    .then(async r=>{if(!r.ok) throw new Error();return r.json();})
    .then(data=>{methods=data.methods;controls.forEach(control=>{control.disabled=false;});search.disabled=false;refresh(true);if(!methods.length) message.textContent='Нет доступных методов. Обратитесь к администратору за доступом к магазинам и разделам.';})
    .catch(()=>{message.textContent='Не удалось загрузить каталог. Проверьте вход на сайт и обновите страницу.';});
})();
