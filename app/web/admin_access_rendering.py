import html

from app.access.sections import (
    SECTION_DESCRIPTIONS,
    SECTION_GROUPS,
    SECTION_LABELS,
    SUPERADMIN_SECTIONS,
    access_level,
    access_limit,
)
from app.dto.identity import AccessProfile, Role, SectionAccessLevel, User

LEVEL_LABELS = {
    SectionAccessLevel.NONE: "Нет доступа",
    SectionAccessLevel.READ: "Просмотр",
    SectionAccessLevel.WRITE: "Просмотр и изменение",
}
PROFILE_DESCRIPTIONS = {
    None: "Вы сами задаёте права каждой вкладки ниже. Рабочую область ограничивают выбранные кабинеты и маркетплейсы.",
    AccessProfile.MARKETPLACE_MANAGER: "Одна площадка. Остатки, приёмка, перемещения и отгрузки. Юнит-экономика и финансовые отчёты закрыты.",
    AccessProfile.SENIOR_MARKETPLACE_MANAGER: "Одна площадка. Операции со стоком, отмена перемещений и просмотр юнит-экономики. Изменение экономики закрыто.",
    AccessProfile.MARKETPLACE_LEAD: "Одна площадка. Операции со стоком, просмотр и изменение юнит-экономики.",
    AccessProfile.STORE_MANAGER: "Одна или несколько площадок. Операции со стоком, перемещения между выбранными площадками и изменение юнит-экономики.",
    AccessProfile.PROCUREMENT: "Одна или несколько площадок. Остатки, приёмка, перемещения, отгрузки и история операций. Остатки Тотал и юнит-экономика закрыты.",
}


def render_section_fields(user: User, editable: bool) -> str:
    groups = []
    for title, sections in SECTION_GROUPS:
        rows = []
        for section in sections:
            level = access_level(user, section)
            limit = access_limit(user, section)
            configured = user.section_access.get(section)
            defaults = user.model_copy(
                update={
                    "section_access": {
                        key: value for key, value in user.section_access.items() if key is not section
                    }
                }
            )
            default_label = LEVEL_LABELS[access_level(defaults, section)]
            options = [
                f'<option value="" title="{default_label}"{" selected" if configured is None else ""}>По умолчанию</option>'
            ]
            for value, label in LEVEL_LABELS.items():
                allowed = (
                    value is SectionAccessLevel.NONE
                    or value is limit
                    or (value is SectionAccessLevel.READ and limit is SectionAccessLevel.WRITE)
                )
                if allowed or configured is value:
                    options.append(
                        f'<option value="{value.value}"{" selected" if configured is value else ""}>{label}</option>'
                    )
            description = SECTION_DESCRIPTIONS[section]
            if section in SUPERADMIN_SECTIONS:
                control = '<span class="ad-permission-fixed">Только суперадминистратор</span>'
            elif user.role is Role.SUPERADMIN:
                control = '<span class="ad-permission-fixed">Полный доступ</span>'
            elif limit is SectionAccessLevel.NONE:
                control = (
                    '<span class="ad-permission-fixed">Недоступно для роли, должности или площадки</span>'
                )
                if editable:
                    control += f'<input type="hidden" name="{section.value}" value="{configured.value if configured else ""}">'
            else:
                control = f'<select class="ad-input" name="{section.value}" aria-label="{html.escape(SECTION_LABELS[section], quote=True)}"{" disabled" if not editable else ""}>{"".join(options)}</select>'
            rows.append(
                f'<div class="ad-permission-row" data-permission-row data-permission-level="{level.value}">'
                f"<div><strong>{html.escape(SECTION_LABELS[section])}</strong><p>{html.escape(description)}</p></div>"
                f'<div class="ad-permission-control">{control}<small class="ad-permission-current">Сейчас: {LEVEL_LABELS[level]}</small></div></div>'
            )
        opened = sum(access_level(user, section) is not SectionAccessLevel.NONE for section in sections)
        groups.append(
            '<details class="ad-permission-group" open><summary>'
            f"<span>{html.escape(title)}</span><small>Доступно {opened} из {len(sections)}</small></summary>"
            f"<div>{''.join(rows)}</div></details>"
        )
    return "".join(groups)
