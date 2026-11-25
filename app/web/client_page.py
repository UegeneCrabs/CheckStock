import json

from app.access.sections import section_path
from app.dto.identity import SectionName
from app.web.templating import fill_template, read_template


def client_page(template_name: str) -> str:
    return fill_template(
        "layout/client-page.html",
        client_header=fill_template(
            "layout/client-header.html",
            finance_url=section_path(None, SectionName.FINANCE_YANDEX),
        ),
        page_template=json.dumps(read_template(template_name), ensure_ascii=False).replace("<", "\\u003c"),
    )
