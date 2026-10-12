"""One-time transfer of configured Yandex connections into RBE's own secret store.

Only this migration tool reads the main application's credential store.  The
running RBE website uses its own ``secrets/yandex_connections.json`` file and
never imports the main application at runtime.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SITE))

from app.yandex import tokens  # noqa: E402
from main import save_connections  # noqa: E402

STORES = ("rimili", "tris", "rockkiddo", "trusthome", "sokoloff", "gogol")


def main() -> None:
    copied: list[dict] = []
    for store_slug in STORES:
        for account in tokens.get_accounts(store_slug):
            copied.append(
                {
                    "store_slug": store_slug,
                    "business_id": int(account["business_id"]),
                    "campaign_ids": [int(value) for value in account.get("campaign_ids") or []],
                    "api_key": str(account["api_key"]),
                }
            )
    save_connections(copied)
    print(json.dumps({"connections": len(copied), "stores": [item["store_slug"] for item in copied]}))


if __name__ == "__main__":
    main()
