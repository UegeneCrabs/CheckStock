"""OpenAPI presentation for employee API clients, separate from report execution."""

import os

from fastapi.openapi.utils import get_openapi


def build_action_schema(routes):
    from app.web.routers.agent_full import PERIOD_REPORTS, SPECS, allowed_fields

    schema = get_openapi(title="CheckStock employee analytics", version="2.0.0", routes=routes)
    for path, item in schema["paths"].items():
        name = path.rsplit("/", 1)[-1]
        fields = (
            allowed_fields(name)
            if name in SPECS
            else {"store", "marketplace"}
            if name == "data-status"
            else None
        )
        if fields is not None:
            item["get"]["parameters"] = [p for p in item["get"].get("parameters", []) if p["name"] in fields]
        for parameter in item["get"].get("parameters", []):
            query_schema = parameter.get("schema", {})
            variants = query_schema.get("anyOf", [])
            non_null = [variant for variant in variants if variant.get("type") != "null"]
            if len(non_null) == 1 and len(variants) == 2:
                parameter["schema"] = {
                    **{key: value for key, value in query_schema.items() if key != "anyOf"},
                    **non_null[0],
                }
                if parameter["schema"].get("default") is None:
                    parameter["schema"].pop("default", None)
            if name in PERIOD_REPORTS and parameter["name"] in {"date_from", "date_to"}:
                parameter["required"] = True
                parameter["description"] = (
                    "Required inclusive date in YYYY-MM-DD format. Supply both dates; at most 90 days."
                )
            if name in {"product-details", "economics-history"} and parameter["name"] == "article":
                parameter["required"] = True
            if parameter["name"] == "store" and (name in SPECS or name == "data-status"):
                parameter["required"] = name == "data-status"
                parameter["schema"] = {"type": "string", "minLength": 1, "maxLength": 100}
                parameter["description"] = (
                    "Optional with exact article: server resolves store automatically. Omit unknown store; never guess."
                    if name in SPECS
                    else "Store slug"
                )
        if name == "profit-calculator":
            for parameter in item["get"]["parameters"]:
                if parameter["name"] == "article":
                    parameter["required"] = True
                    parameter["schema"] = {"type": "string", "minLength": 1, "maxLength": 100}
                    parameter["description"] = (
                        "Exact product article, e.g. 856546716. Ask the user if missing."
                    )
    public_url = os.getenv("CHECKSTOCK_AGENT_PUBLIC_URL", "").strip().rstrip("/")
    from app.agents.catalog import DOCS, documentation

    for path, item in schema["paths"].items():
        name = path.rsplit("/", 1)[-1]
        if name in DOCS:
            doc = documentation(name)
            item["get"]["summary"] = doc["title"]
            item["get"]["x-response-field-guide"] = doc["fields"]
            if doc.get("marketplace_guides"):
                item["get"]["x-marketplace-guides"] = doc["marketplace_guides"]
    schema["servers"] = [{"url": public_url}] if public_url else []
    return schema
