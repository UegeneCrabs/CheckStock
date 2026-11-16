from dataclasses import dataclass

from app.economics.completeness import LABELS


@dataclass(frozen=True, slots=True)
class MarginDiagnostic:
    day: str
    parameter: str
    description: str


def margin_diagnostics(missing: object) -> list[MarginDiagnostic]:
    if not isinstance(missing, dict):
        return []
    result = []
    for day in sorted(key for key in missing if isinstance(key, str)):
        parameters = missing[day]
        if not isinstance(parameters, list):
            continue
        for parameter in dict.fromkeys(value for value in parameters if isinstance(value, str)):
            if parameter == "snapshot":
                description = "Подробная причина неполного расчёта недоступна."
            elif parameter in LABELS:
                description = f"Неизвестно значение: {LABELS[parameter]}."
            else:
                description = "Неизвестен обязательный параметр расчёта; расшифровка недоступна."
            result.append(MarginDiagnostic(day=day, parameter=parameter, description=description))
    return result
