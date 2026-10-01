"""Compact, escaped status for the last Google Sheets order export."""

import html
from collections import defaultdict


def render_result(result: dict | None) -> str:
    if not result:
        return '<p class="week-update-hint">Нажмите «Выгрузить заказы», чтобы заполнить найденные недели в Google Таблице.</p>'
    parts = [
        f"<p>Записано ячеек: <strong>{result['written_cells']}</strong>. "
        f"Уже актуальны: {result.get('unchanged_cells', 0)}. "
        f"Сопоставлено товаров: {result['matched_products']} из {result['product_rows']}.</p>"
    ]
    if not result["complete"]:
        parts.append(
            '<p class="week-search-warning">Часть данных пропущена. Соответствующие ячейки не изменены.</p>'
        )
    parts.append("<details><summary>Даты найденных недель</summary><ul>")
    for period in result["periods"]:
        parts.append(f"<li>{html.escape(period['value'])}: {period['date_from']} — {period['date_to']}</li>")
    parts.append("</ul></details>")
    groups = defaultdict(list)
    for item in result["missing_data"]:
        groups[(item["store_slug"], item["week"], tuple(item["missing_days"]))].append(item["row"])
    if groups:
        parts.append(
            "<details><summary>Недостаточно данных воронки: ячеек "
            f"{len(result['missing_data'])}</summary><ul>"
        )
        for (store, week, missing), rows in groups.items():
            parts.append(
                f"<li>{html.escape(store)} · {html.escape(week)}: товаров {len(rows)}. "
                f"Нет полных данных за {html.escape(', '.join(missing))}.</li>"
            )
        parts.append("</ul></details>")
    if result["issues"]:
        parts.append(f"<details><summary>Не сопоставлены товары: {len(result['issues'])}</summary><ul>")
        for item in result["issues"]:
            text = f"Строка {item['row']}, ARTICLE {item['article']}, BARCODE {item['barcode']}: {item['reason']}"
            parts.append(f"<li>{html.escape(text)}</li>")
        parts.append("</ul></details>")
    if result.get("backup"):
        parts.append(
            '<p class="week-update-hint">Резервная копия прежних значений: '
            f"{html.escape(result['backup'])} (data/backups/google-week-sales).</p>"
        )
    return "".join(parts)
