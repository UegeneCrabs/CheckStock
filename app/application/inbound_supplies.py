import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.dto.inbound_supplies import (
    TERMINAL_STAGES,
    InboundItem,
    InboundMarketplace,
    InboundSnapshot,
    InboundSupply,
)

Target = tuple[str, InboundMarketplace]
SyncResult = dict[str, object]


class InboundSync(Protocol):
    def __call__(self, targets: Sequence[Target], *, recover_interrupted: bool) -> SyncResult: ...


class InboundRepository(Protocol):
    def read(self, targets: Sequence[Target]) -> list[InboundSnapshot]: ...

    def claim(self, target: Target, token: str, now: datetime) -> bool: ...

    def release_interrupted_claims(self, targets: Sequence[Target]) -> int: ...

    def finish(
        self,
        target: Target,
        token: str,
        now: datetime,
        *,
        supplies: tuple[InboundSupply, ...] | None = None,
        status: str = "ok",
        error: str = "",
    ) -> bool: ...

    def enrich(self, target: Target, supplies: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]: ...


class AsyncInboundRepository(Protocol):
    async def previous(self, target: Target) -> tuple[InboundSupply, ...]: ...

    async def claim(self, target: Target, token: str, now: datetime, *, recover: bool) -> bool: ...

    async def finish(
        self,
        target: Target,
        token: str,
        now: datetime,
        *,
        supplies: tuple[InboundSupply, ...] | None = None,
        status: str = "ok",
        error: str = "",
    ) -> bool: ...

    async def enrich(
        self, target: Target, supplies: tuple[InboundSupply, ...]
    ) -> tuple[InboundSupply, ...]: ...


class InboundNotConfiguredError(Exception):
    pass


class InboundSourceError(Exception):
    pass


def acceptance_key(item: InboundItem, marketplace: InboundMarketplace) -> tuple[str, str, str]:
    if marketplace == "WB":
        return item.sku or item.article, item.barcode, item.variant
    return item.sku or item.article, "", ""


def merge_supplies(
    loaded: tuple[InboundSupply, ...],
    previous: tuple[InboundSupply, ...],
    now: datetime,
    marketplace: InboundMarketplace = "OZON",
) -> tuple[InboundSupply, ...]:
    keys = {supply.key for supply in loaded}
    if len(keys) != len(loaded):
        raise InboundSourceError("Площадка вернула повторяющиеся поставки. Сохранены предыдущие данные.")
    current = {supply.key: supply.model_copy(update={"checked_at": now.isoformat()}) for supply in loaded}
    replaced = {supply.parent_key for supply in loaded if supply.parent_key}
    for old in previous:
        fresh = current.get(old.key)
        if fresh is not None:
            accepted = {acceptance_key(item, marketplace): item.accepted_quantity for item in old.items}
            if marketplace == "WB":
                old_counts = Counter(acceptance_key(item, marketplace) for item in old.items)
                new_counts = Counter(acceptance_key(item, marketplace) for item in fresh.items)
                accepted = {
                    key: value for key, value in accepted.items() if old_counts[key] == new_counts[key] == 1
                }
            items = tuple(
                item
                if item.accepted_quantity is not None
                else item.model_copy(
                    update={"accepted_quantity": accepted.get(acceptance_key(item, marketplace))}
                )
                for item in fresh.items
            )
            current[old.key] = fresh.model_copy(
                update={
                    "items": items or (old.items if fresh.stage in TERMINAL_STAGES else ()),
                    "warehouse": fresh.warehouse
                    or (
                        old.warehouse
                        if fresh.destination_cluster_id in ("", old.destination_cluster_id)
                        else ""
                    ),
                    "transit_warehouse": fresh.transit_warehouse or old.transit_warehouse,
                    "destination_cluster": fresh.destination_cluster
                    or (
                        old.destination_cluster
                        if fresh.destination_cluster_id in ("", old.destination_cluster_id)
                        else ""
                    ),
                    "destination_cluster_id": fresh.destination_cluster_id or old.destination_cluster_id,
                    "acceptance_checked_at": fresh.acceptance_checked_at or old.acceptance_checked_at,
                }
            )
        elif old.key not in replaced:
            current[old.key] = (
                old
                if old.stage in TERMINAL_STAGES or not old.listed_status
                else old.model_copy(
                    update={
                        "unavailable": True,
                        "warning": "Поставка отсутствует в последнем ответе площадки. Показаны последние полученные данные.",
                    }
                )
            )
    return tuple(current.values())


class AsyncInboundSupplyService:
    def __init__(
        self,
        repository: AsyncInboundRepository,
        loader: Callable[[Target, tuple[InboundSupply, ...]], Awaitable[tuple[InboundSupply, ...]]],
        after_sync: Callable[[Target], Awaitable[None]],
    ) -> None:
        self.repository = repository
        self.loader = loader
        self.after_sync = after_sync

    async def sync(self, targets: Sequence[Target], *, recover_interrupted: bool) -> SyncResult:
        async with asyncio.TaskGroup() as group:
            tasks = {
                target: group.create_task(self._sync_one(target, recover_interrupted))
                for target in dict.fromkeys(targets)
            }
        return {f"{target[0]} / {target[1]}": task.result() for target, task in tasks.items()}

    async def _sync_one(self, target: Target, recover: bool) -> SyncResult:
        token = uuid4().hex
        try:
            if not await self.repository.claim(target, token, datetime.now(UTC), recover=recover):
                return {"ok": False, "status": "busy"}
            previous = await self.repository.previous(target)
            loaded = await self.loader(target, previous)
            now = datetime.now(UTC)
            supplies = await self.repository.enrich(target, merge_supplies(loaded, previous, now, target[1]))
            status = "partial" if any(supply.unavailable or supply.warning for supply in supplies) else "ok"
            saved = await self.repository.finish(target, token, now, supplies=supplies, status=status)
        except InboundNotConfiguredError as error:
            return await self._finish_error(target, token, "not_configured", str(error))
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, InboundSourceError)
                else f"Не удалось обновить поставки ({type(error).__name__})."
            )
            return await self._finish_error(target, token, "error", message)
        if saved:
            try:
                await self.after_sync(target)
            except Exception as error:
                return {
                    "ok": False,
                    "error": f"Состав обновлён, сверка движения не завершена ({type(error).__name__}).",
                }
        return {"ok": saved, "count": len(loaded), "status": status}

    async def _finish_error(self, target: Target, token: str, status: str, message: str) -> SyncResult:
        try:
            await self.repository.finish(
                target, token, datetime.now(UTC), status=status, error=message[:1000]
            )
        except Exception as error:
            return {"ok": False, "error": f"{message[:1000]} ({type(error).__name__})"}
        return {"status": status, **({"ok": False, "error": message[:1000]} if status == "error" else {})}


class InboundSupplyService:
    def __init__(
        self,
        repository: InboundRepository,
        loader: Callable[[Target, tuple[InboundSupply, ...]], tuple[InboundSupply, ...]],
        clock: Callable[[], datetime] | None = None,
        after_sync: Callable[[Target], None] | None = None,
        ozon_sync: InboundSync | None = None,
    ) -> None:
        self.repository = repository
        self.loader = loader
        self.clock = clock or (lambda: datetime.now(UTC))
        self.after_sync = after_sync
        self.ozon_sync = ozon_sync

    def report(self, targets: Sequence[Target]) -> list[InboundSnapshot]:
        return self.repository.read(targets)

    def claim(self, targets: Sequence[Target]) -> list[tuple[Target, str]]:
        claimed = []
        for target in dict.fromkeys(targets):
            token = uuid4().hex
            if self.repository.claim(target, token, self.clock()):
                claimed.append((target, token))
        return claimed

    def sync_claimed(self, claimed: Sequence[tuple[Target, str]]) -> SyncResult:
        if not claimed:
            return {}
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="inbound-supplies") as pool:
            results = list(pool.map(lambda claim: self._sync_one(*claim), claimed))
        return {
            f"{target[0]} / {target[1]}": result for (target, _), result in zip(claimed, results, strict=True)
        }

    def sync(self, targets: Sequence[Target], *, recover_interrupted: bool = False) -> SyncResult:
        ozon = tuple(target for target in targets if target[1] == "OZON")
        if ozon and self.ozon_sync is not None:
            other = tuple(target for target in targets if target[1] != "OZON")
            if not other:
                return self.ozon_sync(ozon, recover_interrupted=recover_interrupted)
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="inbound-legacy") as pool:
                legacy = pool.submit(self.sync, other, recover_interrupted=recover_interrupted)
                current = self.ozon_sync(ozon, recover_interrupted=recover_interrupted)
                return legacy.result() | current
        if recover_interrupted:
            # Only callers holding the shared inbound job lock may recover claims:
            # no other scheduled/manual loader can still own these leases.
            self.repository.release_interrupted_claims(targets)
        claimed = self.claim(targets)
        if targets and not claimed:
            return {
                "ok": False,
                "error": "Поставки не обновлены: все выбранные магазины уже обновляются "
                "или запускались менее минуты назад. Повторите обновление позже.",
            }
        return self.sync_claimed(claimed)

    def _sync_one(self, target: Target, token: str) -> SyncResult:
        try:
            previous = self.repository.read((target,))[0].supplies
            loaded = self.loader(target, previous)
            supplies = self.repository.enrich(
                target, merge_supplies(loaded, previous, self.clock(), target[1])
            )
            partial = any(supply.unavailable or supply.warning for supply in supplies)
            saved = self.repository.finish(
                target,
                token,
                self.clock(),
                supplies=supplies,
                status="partial" if partial else "ok",
            )
            if saved and self.after_sync is not None:
                self.after_sync(target)
            return {"ok": saved, "count": len(loaded), "status": "partial" if partial else "ok"}
        except InboundNotConfiguredError as error:
            self.repository.finish(target, token, self.clock(), status="not_configured", error=str(error))
            return {"status": "not_configured"}
        except Exception as error:
            message = (
                str(error)
                if isinstance(error, InboundSourceError)
                else (f"Не удалось обновить поставки ({type(error).__name__}). Предыдущие данные сохранены.")
            )
            self.repository.finish(target, token, self.clock(), status="error", error=message[:1000])
            return {"ok": False, "error": message[:1000]}
