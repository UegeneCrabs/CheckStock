"""Explicit public projection, independent of the website calculation details."""

from typing import Self

from app.dto.wb_standard_costs import MarginRoi, ReportModel, ReportRow, Scenario


class ProfitSummary(MarginRoi):
    complete: bool
    missing: tuple[str, ...]

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> Self:
        return cls(
            margin=scenario.margin, roi=scenario.roi, complete=scenario.complete, missing=scenario.missing
        )


class StandardCostsRow(ReportModel):
    article: str
    name: str
    scheme: str
    profit: ProfitSummary
    current_profit: ProfitSummary
    delta: MarginRoi
    calculator_date: str | None
    warnings: tuple[str, ...]

    @classmethod
    def from_report(cls, row: ReportRow) -> Self:
        warning = row.storage_calculation.warning
        return cls(
            article=row.article,
            name=row.name,
            scheme=row.scheme,
            profit=ProfitSummary.from_scenario(row.profit),
            current_profit=ProfitSummary.from_scenario(row.current_profit),
            delta=row.delta,
            calculator_date=row.calculator_date,
            warnings=(warning,) if warning else (),
        )
