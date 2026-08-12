import html

from fastapi import Request

from app.web.templating import fill_template, render_page


def render_sales_placeholder(request: Request, title: str, active: str, note: str) -> str:
    content = fill_template(
        "sales_placeholder_content.html",
        title=html.escape(title),
        note=html.escape(note),
    )
    return render_page(f"CheckStock — {title}", active, content, request.state.user)
