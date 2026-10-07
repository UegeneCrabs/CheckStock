"""Reproduce a monthly finance bundle in memory, without API calls or application DB."""

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.finance import FinanceService
from app.dto.finance import ConnectionUpdate, CostUpdate
from app.finance.calculation import SOURCES
from app.infrastructure.finance_repository import FinanceRepository
from app.infrastructure.orm import OrmBase
from app.yandex.finance_mapping import PARSERS


def reconcile(bundle):
    """The input contains raw sources, explicit scope, dated costs and expected totals."""
    connection = ConnectionUpdate.model_validate(bundle["connection"])
    start, end = (date.fromisoformat(bundle["period"][key]) for key in ("from", "to"))
    expected = bundle.get("expected", {})
    if not isinstance(expected, dict) or not expected:
        raise ValueError("Bundle must contain nonempty expected totals for reconciliation")
    engine = create_engine("sqlite://")
    try:
        tables = [t for name, t in OrmBase.metadata.tables.items() if name.startswith("finance_yandex_")]
        OrmBase.metadata.create_all(engine, tables=tables)
        repository = FinanceRepository(sessionmaker(engine, expire_on_commit=False))
        service = FinanceService(repository)
        conn = repository.add_connection(connection, "offline-reconciliation")
        for row in bundle.get("costs", []):
            repository.add_cost(
                CostUpdate.model_validate({**row, "store_slug": conn["store_slug"]}), "offline-reconciliation"
            )
        batches = [PARSERS[source](conn, bundle[source], start, end) for source in SOURCES]
        repository.publish(conn, service.price_batches(conn, batches), "offline-reconciliation")
        result = service.report((conn["store_slug"],), start, end)
        result.pop("_events")
        differences = []
        for metric, target in expected.items():
            actual = result["metrics"][metric]["value"]
            equal = (
                actual is None
                if target is None
                else actual is not None and Decimal(actual) == Decimal(str(target))
            )
            if not equal:
                differences.append({"metric": metric, "expected": target, "actual": actual})
        incomplete = [
            key
            for key, metric in result["metrics"].items()
            if not metric["complete"] and key != "margin_percent"
        ]
        return {
            "matched": not differences and not incomplete,
            "differences": differences,
            "incomplete": incomplete,
            "report": result,
        }
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="UTF-8 JSON; see tests/fixtures/yandex_finance/month.json")
    parser.add_argument(
        "--output", type=Path, help="Save the reconciliation with daily totals and quality reasons"
    )
    args = parser.parse_args()
    result = reconcile(json.loads(args.bundle.read_text(encoding="utf-8-sig")))
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({key: result[key] for key in ("matched", "differences", "incomplete")}, ensure_ascii=True)
    )
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
