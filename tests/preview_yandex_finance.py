"""Synthetic, read-only UI fixture: python tests/preview_yandex_finance.py.

No application lifespan, real DB, credentials or external API. Listens on localhost.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from test_yandex_finance import FinanceHttp, Role, sale  # noqa: E402

from app.web import templating  # noqa: E402


def main():
    case = FinanceHttp()
    case.setUp()
    case.user = case.user.model_copy(update={"role": Role.SUPERADMIN})
    case.cost()
    case.repo.publish(case.conn, case.service.price_batches(case.conn, case.parsed()), "synthetic fixture")
    large = case.connect("tris", 22, (202,))
    case.publish(
        [
            sale(seller="9007199254740993.17", buyer="9007199254740993.17", cost="0").model_copy(
                update={"campaign_id": 202}
            )
        ],
        large,
    )
    application = case.client.app
    application.mount(
        "/static", StaticFiles(directory=Path(__file__).resolve().parents[1] / "static"), name="static"
    )

    @application.middleware("http")
    async def read_only(request, call_next):
        if request.method != "GET":
            return JSONResponse({"detail": "Синтетическое демо: изменения отключены"}, status_code=403)
        if request.url.path.endswith("/runs"):
            return JSONResponse({"runs": [], "running": False})
        return await call_next(request)

    try:
        with patch.object(templating, "render_system_alerts", return_value=""):
            uvicorn.run(application, host="127.0.0.1", port=8875, log_level="warning")
    finally:
        case.doCleanups()


if __name__ == "__main__":
    main()
