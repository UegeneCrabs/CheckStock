import json

from app.web.templating import fill_template, read_template


def client_page(template_name: str) -> str:
    return fill_template(
        "layout/client-page.html",
        client_header=read_template("layout/client-header.html"),
        page_template=json.dumps(read_template(template_name), ensure_ascii=False).replace("<", "\\u003c"),
    )
