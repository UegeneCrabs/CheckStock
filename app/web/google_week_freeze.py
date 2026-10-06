"""Escaped preview and outcome of replacing past-week formulas with values."""

import html


def render_result(result: dict | None) -> str:
    if not result:
        return '<p class="week-update-hint">Нажмите «Проверить диапазоны», чтобы увидеть недели и количество формул перед фиксацией.</p>'
    preview = result.get("dry_run", False)
    label = "Найдено формул" if preview else "Заменено формул"
    count = result.get("total_formulas", 0) if preview else result.get("written_cells", 0)
    parts = [
        f"<p>{'Предварительная проверка' if preview else 'Фиксация'}: "
        f"недели перед <strong>{html.escape(str(result.get('week', '')))}</strong>. "
        f"{label}: <strong>{count}</strong>.</p>"
    ]
    if preview:
        parts.append(
            '<p class="week-update-hint">Данные не изменены. При запуске диапазоны проверяются заново.</p>'
        )
    if not result.get("complete", True):
        parts.append(
            '<p class="week-search-warning">Есть пропуски или ошибки. Проверьте результат каждого листа.</p>'
        )
    parts.append('<ul class="week-freeze-results">')
    for sheet in result.get("sheets", []):
        title = html.escape(str(sheet.get("sheet_name", f"Лист #{sheet.get('sheet_id', '')}")))
        ranges = ", ".join(str(value) for value in sheet.get("ranges", []))
        parts.append(f"<li><strong>{title}</strong>")
        if ranges:
            parts.append(f"<span>{html.escape(ranges)}</span>")
        parts.append(f"<span>Формул: {sheet.get('formula_count', 0)}")
        if not preview:
            parts.append(f"; заменено: {sheet.get('written_cells', 0)}")
        parts.append("</span>")
        if sheet.get("issue"):
            parts.append(f'<span class="week-search-warning">{html.escape(str(sheet["issue"]))}</span>')
        parts.append("</li>")
    parts.append("</ul>")
    if result.get("backup"):
        parts.append(
            '<p class="week-update-hint">Резервная копия формул: '
            f"{html.escape(str(result['backup']))} (data/backups/google-week-freeze).</p>"
        )
    return "".join(parts)
