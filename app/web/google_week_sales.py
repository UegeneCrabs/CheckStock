"""Compact, escaped status for the last Google Sheets order or stock export."""

import html
from collections import defaultdict

from app.core.formatting import format_dt


def render_result(
    result: dict | None,
    *,
    button_label: str = "Выгрузить заказы",
    data_label: str = "воронки",
    backup_folder: str = "google-week-sales",
) -> str:
    if not result:
        return f'<p class="week-update-hint">Нажмите «{html.escape(button_label)}», чтобы заполнить найденные недели в Google Таблице.</p>'
    parts = [
        f"<p>Записано ячеек: <strong>{result['written_cells']}</strong>. "
        f"Уже актуальны: {result.get('unchanged_cells', 0)}. "
        f"Сопоставлено товаров: {result['matched_products']} из {result['product_rows']}.</p>"
    ]
    if "checked_cells" in result:
        parts.append(
            f"<p>Проверено ячеек: <strong>{result['checked_cells']}</strong>. "
            f"Из записанных обнулено: <strong>{result.get('zeroed_cells', 0)}</strong>.</p>"
        )
    if result["issues"] or result["missing_data"]:
        parts.append(
            '<p class="week-search-warning">Часть данных пропущена. Соответствующие ячейки не изменены.</p>'
        )
    parts.append("<details><summary>Даты найденных недель</summary><ul>")
    for period in result["periods"]:
        dates = (
            f"снимок за {period['snapshot_day']}"
            if "snapshot_day" in period
            else f"{period['date_from']} — {period['date_to']}"
        )
        parts.append(f"<li>{html.escape(period['value'])}: {html.escape(dates)}</li>")
    parts.append("</ul></details>")
    zero_filled = result.get("zero_filled", [])
    if zero_filled:
        explanation = (
            "При отсутствии снимка FBO за нужное воскресенье ячейка обнуляется."
            if any("snapshot_day" in period for period in result["periods"])
            else "Дни без данных в БД учтены как 0. "
            "При отсутствии данных за всю неделю ячейка обнуляется; "
            "при частичных данных записывается сумма доступных значений."
        )
        parts.append(f'<p class="week-update-hint">{explanation}</p>')
        parts.append(
            f"<details><summary>Ноль вместо отсутствующих данных: ячеек {len(zero_filled)}</summary><ul>"
        )
        filled_groups = defaultdict(list)
        for item in zero_filled:
            filled_groups[(item["store_slug"], item["week"], tuple(item["missing_days"]))].append(item["row"])
        for (store, week, days), rows in filled_groups.items():
            parts.append(
                f"<li>{html.escape(store)} · {html.escape(week)}: товаров {len(rows)}. "
                f"Учтено как 0 за {html.escape(', '.join(days))}.</li>"
            )
        parts.append("</ul></details>")
    groups = defaultdict(list)
    for item in result["missing_data"]:
        groups[(item["store_slug"], item["week"], tuple(item["missing_days"]))].append(item["row"])
    if groups:
        parts.append(
            f"<details><summary>Недостаточно данных {html.escape(data_label)}: ячеек "
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
            f"{html.escape(result['backup'])} (data/backups/{html.escape(backup_folder)}).</p>"
        )
    if result.get("snapshot_times"):
        parts.append("<details><summary>Время сохранения снимков FBO</summary><ul>")
        for item in result["snapshot_times"]:
            parts.append(
                f"<li>{html.escape(item['store_slug'])}: за {html.escape(item['day'])} — "
                f"{html.escape(format_dt(item['captured_at']))}</li>"
            )
        parts.append("</ul></details>")
    return "".join(parts)
