import html


def copy_identifier(
    value: object,
    kind: str,
    label: str | None = None,
    *,
    class_name: str = "",
) -> str:
    raw = "" if value is None else str(value).strip()
    if not raw:
        return html.escape(label or "—")
    classes = "copy-identifier" + (f" {class_name.strip()}" if class_name.strip() else "")
    visible = raw if label is None else str(label)
    return (
        f'<button class="{html.escape(classes, quote=True)}" type="button" '
        f'data-copy-kind="{html.escape(kind, quote=True)}" '
        f'data-copy-value="{html.escape(raw, quote=True)}" '
        'data-copy-tooltip="Нажмите, чтобы скопировать" '
        f'aria-label="Скопировать {html.escape(kind.lower(), quote=True)} '
        f'{html.escape(raw, quote=True)}">{html.escape(visible)}</button>'
    )
