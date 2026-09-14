"""Load HTML fragments used by the scripts included on a page."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings

SCRIPT_PATH = re.compile(r'<script\b[^>]*\bsrc=["\']/static/([^"\'?]+)\.js(?:\?[^"\']*)?["\']')
FRAGMENT = re.compile(r"<!-- template: ([\w-]+) -->\n(.*?)\n<!-- endtemplate -->", re.DOTALL)


@lru_cache(maxsize=128)
def _read_fragments(path: Path, modified_ns: int, size: int) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    matches = FRAGMENT.findall(source)
    fragments = dict(matches)
    if not fragments or len(fragments) != len(matches) or FRAGMENT.sub("", source).strip():
        raise ValueError(f"Invalid client template file: {path}")
    return fragments


def render_client_templates(markup: str) -> str:
    templates: dict[str, str] = {}
    for module in dict.fromkeys(SCRIPT_PATH.findall(markup)):
        relative = Path(module)
        path = settings.templates_dir / relative.parent / "client" / f"{relative.name}.html"
        if not path.is_file():
            continue
        stat = path.stat()
        for name, fragment in _read_fragments(path, stat.st_mtime_ns, stat.st_size).items():
            templates[f"{module}/{name}"] = fragment
    payload = (
        json.dumps(templates, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f'<script type="application/json" id="client-templates">{payload}</script>'
