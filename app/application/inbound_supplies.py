from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.dto.inbound_supplies import TERMINAL_STAGES, InboundSnapshot, InboundSupply

Target = tuple[str, str]


class InboundRepository(Protocol):
    def read(self, targets: Sequence[Target]) -> list[InboundSnapshot]: ...

    def claim(self, target: Target, token: str, now: datetime) -> bool: ...

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


class InboundNotConfiguredError(Exception):
    pass


class InboundSourceError(Exception):
    pass


class InboundSupplyService:
    def __init__(
        self,
        repository: InboundRepository,
        loader: Callable[[Target, tuple[InboundSupply, ...]], tuple[InboundSupply, ...]],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.loader = loader
        self.clock = clock or (lambda: datetime.now(UTC))

    def report(self, targets: Sequence[Target]) -> list[InboundSnapshot]:
        return self.repository.read(targets)

    def claim(self, targets: Sequence[Target]) -> list[tuple[Target, str]]:
        claimed = []
        for target in dict.fromkeys(targets):
            token = uuid4().hex
            if self.repository.claim(target, token, self.clock()):
                claimed.append((target, token))
        return claimed

    def sync_claimed(self, claimed: Sequence[tuple[Target, str]]) -> dict:
        if not claimed:
            return {}
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="inbound-supplies") as pool:
            results = list(pool.map(lambda claim: self._sync_one(*claim), claimed))
        return {
            f"{target[0]} / {target[1]}": result for (target, _), result in zip(claimed, results, strict=True)
        }

    def sync(self, targets: Sequence[Target]) -> dict:
        return self.sync_claimed(self.claim(targets))

    def _sync_one(self, target: Target, token: str) -> dict:
        try:
            previous = self.repository.read((target,))[0].supplies
            loaded = self.loader(target, previous)
            keys = [supply.key for supply in loaded]
            if len(keys) != len(set(keys)):
                raise InboundSourceError(
                    "Площадка вернула повторяющиеся поставки. Сохранены предыдущие данные."
                )
            now = self.clock().isoformat()
            current = {supply.key: supply.model_copy(update={"checked_at": now}) for supply in loaded}
            replaced = {supply.parent_key for supply in loaded if supply.parent_key}
            for old in previous:
                if old.key not in current and old.key not in replaced:
                    current[old.key] = (
                        old
                        if old.stage in TERMINAL_STAGES
                        else old.model_copy(
                            update={
                                "unavailable": True,
                                "warning": "Поставка отсутствует в последнем ответе площадки. Показаны последние полученные данные.",
                            }
                        )
                    )
            supplies = self.repository.enrich(target, tuple(current.values()))
            partial = any(supply.unavailable or supply.warning for supply in supplies)
            saved = self.repository.finish(
                target,
                token,
                self.clock(),
                supplies=supplies,
                status="partial" if partial else "ok",
            )
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
