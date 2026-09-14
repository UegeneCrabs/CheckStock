"""Explicit marketplace warehouse aliases; unknown names are kept distinct."""


def normalize(name: str) -> str:
    words = str(name or "").casefold().replace("ё", "е").split()
    if words and words[0] in {"fbs", "rfbs", "фбс"}:
        words = words[1:]
    return "".join(char for char in "".join(words) if char.isalnum())


ALIASES = {
    "ФулСервис Подольск": ("ФуллСервис", "Фулл Сервис", "ФуллСервис Подольск"),
    "AFFLATUS Купавна": ("Afflatus", "Аффлатус", "Аффлатус Купавна"),
    "ФФ Царицыно Казань": ("Казань Царицыно", "Царицыно Казань"),
    "ФФ GO Екатеринбург": ("Екатеринбург", "GO Екатеринбург"),
}


def fulfillment_lookup(names: list[str]) -> dict[str, str]:
    result = {normalize(name): name for name in names}
    for name, aliases in ALIASES.items():
        canonical = result.get(normalize(name))
        if canonical:
            for alias in aliases:
                result.setdefault(normalize(alias), canonical)
    return result
