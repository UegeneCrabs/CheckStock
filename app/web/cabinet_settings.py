"""Shared cabinet settings data and embedded editor for API/export settings."""

import json

from app import db
from app.access_control import accessible_stores
from app.dto.identity import SectionAccessLevel, SectionName
from app.section_access import has_access
from app.stores import STORES
from app.web.templating import fill_template


def cabinet_settings_payload(store_slugs: tuple[str, ...]) -> list[dict]:
    return [
        {
            **item.model_dump(mode="json"),
            "store_name": STORES[item.store_slug]["name"],
            "store_initials": STORES[item.store_slug]["initials"],
            "store_color": STORES[item.store_slug]["color"],
            "store_text": STORES[item.store_slug]["text"],
        }
        for item in db.list_unit_economics_1c_cabinet_settings(store_slugs)
    ]


def render_cabinet_settings(user) -> str:
    return fill_template(
        "unit_economics_1c_cabinet_settings_content.html",
        cabinet_settings_config=json.dumps(
            {
                "marketplace": "WB",
                "canEdit": has_access(
                    user, SectionName.UNIT_ECONOMICS_1C, SectionAccessLevel.WRITE,
                ),
                "items": cabinet_settings_payload(accessible_stores(user, "WB")),
            },
            ensure_ascii=False,
        ).replace("</", "<\\/"),
    )
