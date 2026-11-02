(() => {
  const headings = new Map([
    ['Общие правила', 'common'], ['Wildberries', 'WB'], ['Яндекс Маркет', 'YANDEX MARKET'], ['Ozon', 'OZON'],
  ]);
  function parse(value) {
    const sections = new Map();
    let current;
    for (const line of value.replace(/\r\n/g, '\n').split('\n')) {
      if (line.startsWith('## ')) {
        const key = headings.get(line.slice(3));
        if (!key || sections.has(key)) throw new Error('section');
        sections.set(key, []);
        current = key;
      } else if (current) sections.get(current).push(line);
      else if (line.trim()) throw new Error('heading');
    }
    if (sections.size !== headings.size) throw new Error('missing');
    for (const [key, lines] of sections) {
      const text = lines.join('\n').trim();
      if (!text) throw new Error('empty');
      sections.set(key, text);
    }
    return sections;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { parse };
  else globalThis.CheckStockInstructionSections = { parse };
})();
