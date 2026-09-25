"""One transaction for a user request, its movement, audit trail and replay result."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.ports import StockUnitOfWork, StockUnitOfWorkFactory


class StockRequestConflict(Exception):
    pass


class InvalidStockRequest(Exception):
    pass


@dataclass(frozen=True)
class StockRequestResult:
    body: dict
    status_code: int = 200


def execute_stock_request(
    factory: StockUnitOfWorkFactory,
    *,
    store_slug: str,
    user_id: int,
    request_key: str,
    payload: dict,
    action: Callable[[StockUnitOfWork], StockRequestResult],
) -> StockRequestResult:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request_key):
        raise InvalidStockRequest("Передайте Idempotency-Key: идентификатор операции длиной 16–128 символов")
    if not isinstance(user_id, int) or user_id <= 0:
        raise InvalidStockRequest("Для операции необходим авторизованный пользователь")
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    scope = (store_slug, user_id, request_key)
    with factory() as uow:
        connection = uow.connection
        # The unique PK arbitrates simultaneous deliveries in the DB, including across workers.
        # PostgreSQL waits for the conflicting transaction; SQLite serializes writers here,
        # before any stock read. Rollback removes the claim so the same key can be retried.
        claimed = connection.execute(
            """
            INSERT INTO stock_mutation_requests
                (store_slug, user_id, request_key, payload_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (store_slug, user_id, request_key) DO NOTHING
            RETURNING request_key
            """,
            (*scope, payload_hash, datetime.now(UTC).isoformat()),
        ).fetchone()
        if claimed is None:
            existing = connection.execute(
                "SELECT payload_hash, response_json, status_code FROM stock_mutation_requests "
                "WHERE store_slug=? AND user_id=? AND request_key=?",
                scope,
            ).fetchone()
            if existing is None or existing["payload_hash"] != payload_hash:
                raise StockRequestConflict("Этот идентификатор операции уже использован с другими данными")
            if existing["response_json"] is None:
                raise StockRequestConflict(
                    "Результат операции ещё не доступен; повторите запрос с тем же ключом"
                )
            return StockRequestResult(json.loads(existing["response_json"]), int(existing["status_code"]))
        result = action(uow)
        if result.status_code >= 400:
            uow.rollback()
            return result
        connection.execute(
            "UPDATE stock_mutation_requests SET response_json=?, status_code=? "
            "WHERE store_slug=? AND user_id=? AND request_key=?",
            (json.dumps(result.body, ensure_ascii=False), result.status_code, *scope),
        )
        uow.commit()
        return result
