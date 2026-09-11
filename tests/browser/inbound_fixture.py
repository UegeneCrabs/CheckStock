"""Isolated local UI fixture: python -m tests.browser.inbound_fixture."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.dto.inbound_supplies import InboundItem, InboundSupply
from tests.unit.test_web_routes import WebRouteUnitTests


def fixture_supplies(target, previous):
    now = datetime.now(UTC).isoformat()
    store, marketplace = target
    stages = ["planned", "transit", "acceptance", "placement", "discrepancy", "completed"]
    result = []
    for index, stage in enumerate(stages):
        if stage == "placement" and marketplace != "WB":
            continue
        result.append(
            InboundSupply(
                key=str(index + 1),
                supply_id=str(120001 + index),
                number=str(320001 + index),
                order_id="fixture-order",
                stage=stage,
                status=stage,
                status_label={
                    "planned": "Готова к отгрузке",
                    "transit": "В пути",
                    "acceptance": "Приёмка на складе",
                    "placement": "Принято",
                    "discrepancy": "Спор по акту",
                    "completed": "Завершена",
                }[stage],
                warehouse="Коледино" if marketplace == "WB" else "Склад хранения · Подольск",
                transit_warehouse="Сортировочный центр Чехов" if stage == "transit" else "",
                planned_at=(datetime.now(UTC) + timedelta(days=index - 2)).isoformat(),
                checked_at=now,
                note="Тестовые данные для проверки интерфейса.",
                items=(
                    InboundItem(
                        article=f"{store}-{index + 1}",
                        barcode="0001234567890",
                        name="Конструктор детский · набор 120 деталей",
                        quantity=100,
                        accepted_quantity=70
                        if stage == "acceptance"
                        else 100
                        if stage in {"placement", "completed"}
                        else None,
                        ready_quantity=60 if stage == "placement" else 100 if stage == "completed" else None,
                    ),
                    InboundItem(
                        article=f"special-{index}",
                        barcode="0009876543210",
                        name="<img src=x onerror=alert(1)> & безопасный текст",
                        quantity=40,
                        accepted_quantity=40 if stage == "completed" else None,
                    ),
                ),
            )
        )
    return tuple(result)


if __name__ == "__main__":
    case = WebRouteUnitTests()
    case.setUp()
    service = case.app.state.container.inbound_supplies
    service.loader = fixture_supplies
    service.sync(
        tuple(
            (slug, mp) for slug in ("rimili", "toyka", "rockkiddo") for mp in ("WB", "OZON", "YANDEX MARKET")
        )
    )
    preview = FastAPI()
    preview.mount(
        "/static", StaticFiles(directory=Path(__file__).resolve().parents[2] / "static"), name="static"
    )

    @preview.get("/stock/inbound", response_class=HTMLResponse)
    def page(request: Request):
        return case.client.get(f"/stock/inbound?{request.url.query}").text

    @preview.get("/stock/inbound/data")
    def data(request: Request):
        response = case.client.get(f"/stock/inbound/data?{request.url.query}")
        return Response(response.content, status_code=response.status_code, media_type="application/json")

    @preview.post("/stock/inbound/sync")
    async def sync(request: Request):
        response = case.client.post("/stock/inbound/sync", json=await request.json())
        return Response(response.content, status_code=response.status_code, media_type="application/json")

    try:
        uvicorn.run(preview, host="127.0.0.1", port=18117, log_level="warning")
    finally:
        case.tearDown()
