from html.parser import HTMLParser

import pytest

from app.dto.identity import AccessProfile, MarketplaceAccessScope, Role, UserCollection
from app.stores import STORES
from app.web.routers import admin


class Markup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tags = []
        self.in_template = False
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "template":
            self.in_template = True
        self.tags.append((tag, dict(attrs), self.in_template))

    def handle_endtag(self, tag):
        if tag == "template":
            self.in_template = False


def test_employee_rows_keep_edit_controls_out_of_the_table(user_factory):
    actor = user_factory()
    target = user_factory(user_id=2, role=Role.USER, stores=("rimili",))
    parsed = Markup(admin.render_user_rows(actor, UserCollection((actor, target))))
    assert len([tag for tag, attrs, _ in parsed.tags if "data-user-row" in attrs]) == 2
    assert not any(tag in {"input", "select", "form"} and not inert for tag, _, inert in parsed.tags)
    assert not any("data-user-action" in attrs and not inert for _, attrs, inert in parsed.tags)
    row = next(attrs for _, attrs, _ in parsed.tags if attrs.get("data-user-id") == "2")
    assert row["data-stores"] == "rimili"
    assert row["data-status"] == "active"


def test_employee_content_is_escaped_inside_templates_and_attributes(user_factory):
    actor = user_factory()
    name = '\"></template><script>alert(1)</script><img src=x onerror=alert(1)>'
    target = user_factory(user_id=2, role=Role.USER).model_copy(update={"full_name": name})
    parsed = Markup(admin.render_user_rows(actor, UserCollection((target,))))
    assert not any(tag in {"script", "img"} for tag, _, _ in parsed.tags)
    assert not any(key.startswith("on") for _, attrs, _ in parsed.tags for key in attrs)
    assert next(attrs["data-name"] for _, attrs, _ in parsed.tags if "data-user-row" in attrs) == name


@pytest.mark.parametrize("kind", ["self", "outside_scope", "readonly"])
def test_unmanageable_employee_has_no_enabled_editor(kind, user_factory):
    actor = user_factory()
    target = actor
    if kind == "outside_scope":
        actor = user_factory(role=Role.ADMIN, stores=("rimili",))
        target = user_factory(user_id=2, role=Role.USER, stores=("tris",))
    elif kind == "readonly":
        actor = user_factory(can_manage_users=False)
        target = user_factory(user_id=2, role=Role.USER)
    parsed = Markup(admin.render_user_editor(actor, target))
    forms = [attrs for tag, attrs, _ in parsed.tags if tag == "form"]
    assert forms and all("hidden" in attrs or attrs["data-endpoint"] == "sections" for attrs in forms)
    fieldsets = [attrs for tag, attrs, _ in parsed.tags if tag == "fieldset"]
    assert fieldsets and all("disabled" in attrs for attrs in fieldsets)


def test_admin_can_only_edit_legacy_stores_within_their_scope(user_factory):
    actor = user_factory(role=Role.ADMIN, stores=("rimili",))
    target = user_factory(user_id=2, role=Role.USER, stores=("rimili",))
    parsed = Markup(admin.render_user_editor(actor, target))
    visible_forms = [attrs["data-endpoint"] for tag, attrs, _ in parsed.tags if tag == "form" and "hidden" not in attrs]
    assert visible_forms == ["stores", "sections"]
    assert {attrs["value"] for tag, attrs, _ in parsed.tags if tag == "input" and attrs.get("name") == "stores"} == {"rimili"}


def test_profile_and_individual_access_render_the_correct_editors(user_factory):
    actor = user_factory()
    target = user_factory(user_id=2, role=Role.USER, stores=("rimili",)).model_copy(update={
        "access_profile": AccessProfile.MARKETPLACE_MANAGER,
        "access_scopes": (MarketplaceAccessScope(store_slug="rimili", marketplace="OZON"),),
    })
    parsed = Markup(admin.render_user_editor(actor, target))
    visible_forms = [attrs["data-endpoint"] for tag, attrs, _ in parsed.tags if tag == "form" and "hidden" not in attrs]
    assert visible_forms == ["access-policy", "sections", "role"]
    checked = [attrs["value"] for tag, attrs, _ in parsed.tags if tag == "input" and attrs.get("name") == "marketplaces" and "checked" in attrs]
    assert checked == ["OZON"]
    legacy = target.model_copy(update={"access_profile": None, "access_scopes": ()})
    legacy_parsed = Markup(admin.render_user_editor(actor, legacy))
    section_names = {attrs["name"] for tag, attrs, _ in legacy_parsed.tags if tag == "select" and attrs.get("name") not in {"role", "access_profile"}}
    from app.section_access import SECTION_GROUPS

    assert section_names == {section.value for _, group in SECTION_GROUPS[:3] for section in group} | {"ai_agents"}
    assert len([attrs for _, attrs, _ in legacy_parsed.tags if "data-permission-row" in attrs]) == 16


def test_explicit_empty_store_selection_does_not_check_every_store(user_factory):
    parsed = Markup(admin.render_store_checkboxes(user_factory(), ()))
    checkboxes = [attrs for tag, attrs, _ in parsed.tags if tag == "input"]
    assert len(checkboxes) == len(STORES)
    assert not any("checked" in attrs for attrs in checkboxes)
