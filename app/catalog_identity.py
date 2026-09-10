"""Resolve seller articles and barcode aliases without choosing arbitrary duplicates."""


def barcodes(item: dict) -> set[str]:
    return {str(code).strip() for code in [item.get("barcode"), *(item.get("barcodes") or [])] if code}


class CatalogMatchError(ValueError):
    pass


class CatalogIndex:
    def __init__(self, items: list[dict]) -> None:
        self.articles = {str(item["article"]): item for item in items}
        self.aliases = {alias: item for item in items for alias in item.get("article_aliases", [])}
        self.by_barcode: dict[str, set[str]] = {}
        for article, item in self.articles.items():
            for barcode in barcodes(item):
                self.by_barcode.setdefault(barcode, set()).add(article)

    def resolve(self, article: str = "", barcode: str = "") -> dict | None:
        exact = self.articles.get(article) or self.aliases.get(article)
        if exact is not None:
            candidates = self.by_barcode.get(barcode, set())
            if candidates and exact["article"] not in candidates:
                raise CatalogMatchError(f"Артикул {article} и штрихкод {barcode} относятся к разным товарам")
            return exact
        candidates = self.by_barcode.get(barcode, set())
        if len(candidates) > 1:
            raise CatalogMatchError(
                f"Штрихкод {barcode} соответствует нескольким артикулам: "
                + ", ".join(sorted(candidates))
                + ". Укажите точный артикул."
            )
        return self.articles[next(iter(candidates))] if candidates else None

    def resolve_code(self, code: str) -> dict | None:
        return (
            self.resolve(article=code)
            if code in self.articles or code in self.aliases
            else self.resolve(barcode=code)
        )
