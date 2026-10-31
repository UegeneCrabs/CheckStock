---
name: checkstock-logging
description: "Use when adding, changing, or reviewing Python logging in CheckStock, including log levels, messages, tags, request context, and compatibility with the local Grafana/Loki/Alloy stack. Do not use for merely reading existing logs without changing code or configuration."
---

# CheckStock logging

Apply `$checkstock-task-guide` first. Apply `$checkstock-backend` when changing Python code and `$checkstock-testing` when verifying changes. Treat `app/core/logging_config.py` as the source of truth for the logging API; consult `docs/local-observability.md` and `deploy/observability/` for the local pipeline. If documentation and code differ, follow the code and update the documentation.

## Write a log event

- Create a module logger with `tagged_logger(__name__, "category", "operation")`. The formatter supplies the full module name, timestamp, and level. Do not put those fields into the message yourself.
- Pass event-specific tags with `extra=log_tags(store_slug)` or multiple string arguments. Tags are separate strings, not fixed `marketplace=...` fields or words inferred from the message. Prefer stable categories such as marketplace, operation, job, and store when that context is available.
- Keep high-cardinality values such as `request_id`, product IDs, URLs, and error text out of tags. Never log secrets or personal data. `LogTags` rejects empty values, surrounding whitespace, control characters, double quotes, and backslashes.
- Keep a recognizable event name and useful diagnostic fields in the message. When editing events used by existing dashboards, preserve the searchable fragments `request_completed`, `status=`, `duration_ms=`, and `job=` where applicable. An HTTP status in the message does not replace the log level.
- Use `DEBUG` for detailed diagnostics, `INFO` for meaningful progress, and `WARNING` for a recoverable problem. Use `ERROR` for a failure without an active exception. Call `logger.exception(...)` inside an exception handler when the traceback matters. Avoid logging the same exception repeatedly across call layers.
- The middleware and formatter append `request_id` from request context. Do not duplicate it in tags or the message.

```python
from app.core.logging_config import log_tags, tagged_logger

logger = tagged_logger(__name__, "wb", "hourly_prices")
logger.info("wb_catalog_loaded store=%s items=%s", store_slug, len(items), extra=log_tags(store_slug))
```

## Preserve the pipeline

The formatter emits `ISO timestamp in Europe/Moscow | LEVEL | full.module | [tags] | message`. It omits the tag segment when no tags exist. If you change this contract, update `deploy/observability/config.alloy`, the four dashboard JSON files in that directory, and `docs/local-observability.md` together. Check time, level, module, and tag filters; the separate `tag_catalog` stream; parsing of older lines; and multiline tracebacks.

Use a temporary scenario to verify records with and without tags, an exception, and the tag catalog. Run Ruff and mypy when available on affected code. For pipeline changes, validate Alloy and query local Loki when the stack is available. Follow `$checkstock-task-guide` on avoiding permanent test files.
