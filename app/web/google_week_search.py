"""Render the saved read-only search result without trusting spreadsheet text as HTML."""

import html
from urllib.parse import urlencode

from app.integrations.google_week_update import spreadsheet_id


def render_result(result: dict | None) -> str:
    if result is None:
        return (
            '<p class="week-update-hint">Нажмите «Найти недели», чтобы увидеть адреса и значения ячеек.</p>'
        )

    def link(cell: str) -> str:
        doc_id = spreadsheet_id(result["spreadsheet_url"])
        fragment = urlencode({"gid": result["sheet_id"], "range": cell})
        url = f"https://docs.google.com/spreadsheets/d/{doc_id}/edit#{fragment}"
        return f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(cell)}</a>'

    sections = []
    for source in result["sources"]:
        parts = [
            f'<section class="week-search-source"><h4>Исходная ячейка {link(source["cell"])}: '
            f"{html.escape(source['value'] or 'пусто')}</h4>"
        ]
        if source.get("issue"):
            parts.append(f'<p class="week-search-warning">{html.escape(source["issue"])}</p>')
        elif not source["matches"]:
            parts.append(
                '<p class="week-update-hint">Других ячеек с таким значением на листе не найдено.</p>'
            )
        for match in source["matches"]:
            parts.append(
                f'<div class="week-search-match"><p>Найдено в {link(match["cell"])}. '
                f"Диапазон {link(match['range'])} — ячеек: {len(match['headers'])}.</p>"
            )
            if "columns" in match:
                columns = []
                for name in ("ARTICLE", "BARCODE"):
                    cells = match["columns"].get(name, [])
                    addresses = ", ".join(link(cell["cell"]) for cell in cells) if cells else "не найден"
                    columns.append(f"<strong>{name}</strong> — {addresses}")
                parts.append(
                    f"<p>Столбцы в строке {html.escape(str(match['row']))}: " + "; ".join(columns) + ".</p>"
                )
            else:
                parts.append(
                    '<p class="week-update-hint">Чтобы найти ARTICLE и BARCODE, повторите поиск.</p>'
                )
            parts.append(
                '<div class="week-search-table-wrap"><table><thead><tr>'
                '<th scope="col">Ячейка</th><th scope="col">Значение</th>'
                "</tr></thead><tbody>"
            )
            for cell in match["headers"]:
                parts.append(f"<tr><td>{link(cell['cell'])}</td><td>{html.escape(cell['value'])}</td></tr>")
            parts.append("</tbody></table></div>")
            stop = match["stop"]
            if stop:
                reason = "исходная ячейка" if stop["reason"] == "source" else "другой формат заголовка"
                parts.append(
                    f'<p class="week-update-hint">Остановка: {link(stop["cell"])} — '
                    f"{html.escape(stop['value'] or 'пусто')} ({reason}).</p>"
                )
            else:
                parts.append('<p class="week-update-hint">Достигнута левая граница листа — колонка A.</p>')
            parts.append("</div>")
        parts.append("</section>")
        sections.append("".join(parts))
    return "".join(sections)
