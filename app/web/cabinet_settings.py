"""Shared cabinet settings data and embedded editor for API/export settings."""

import json

from app import db
from app.access.access_control import accessible_stores
from app.access.sections import has_access
from app.core.stores import STORES
from app.dto.identity import SectionAccessLevel, SectionName
from app.repositories import unit_economics_yandex
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
    wb_content = fill_template(
        "economics/wb/unit_economics_1c_cabinet_settings_content.html",
        cabinet_settings_config=json.dumps(
            {
                "marketplace": "WB",
                "canEdit": has_access(
                    user,
                    SectionName.UNIT_ECONOMICS_WB,
                    SectionAccessLevel.WRITE,
                ),
                "items": cabinet_settings_payload(accessible_stores(user, "WB")),
            },
            ensure_ascii=False,
        ).replace("</", "<\\/"),
    )
    yandex_items = [
        {
            "store_slug": slug,
            "store_name": STORES[slug]["name"],
            **unit_economics_yandex.get_buyout_settings(slug),
        }
        for slug in accessible_stores(user, "YANDEX MARKET")
    ]
    return wb_content + fill_template(
        "economics/yandex/unit_economics_yandex_settings.html",
        yandex_settings_config=json.dumps(
            {
                "items": yandex_items,
                "canEdit": has_access(user, SectionName.UNIT_ECONOMICS_WB, SectionAccessLevel.WRITE),
            },
            ensure_ascii=False,
        ).replace("</", "<\\/"),
    )
