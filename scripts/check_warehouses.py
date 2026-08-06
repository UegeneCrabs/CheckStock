"""
Разовый диагностический скрипт: показывает, что реально возвращает
WB для /api/v3/warehouses по каждому магазину, у которого есть токен.

Нужен, чтобы понять, как называются склады продавца в WB, и сопоставить
их с фулфилментами. Никуда данные не отправляет, только печатает в консоль.

Запуск (из корня проекта, в .venv проекта, где уже есть fastapi/py3.11+):
    python scripts/check_warehouses.py
"""

import sys
from pathlib import Path

# Скрипт лежит в scripts/, а пакет приложения — в app/ на уровень выше корня
# этого файла (scripts/check_warehouses.py -> корень проекта -> app/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens


def main() -> None:
    for slug in STORES:
        if not wb_tokens.has_token(slug):
            print(f"{slug}: нет токена")
            continue

        token = wb_tokens.get_token(slug)
        print(f"=== {slug} ===")
        try:
            warehouses = wb_api.get_own_warehouses(token)
        except wb_api.WBApiError as e:
            print(f"  ошибка: {e.friendly}")
            continue

        if not warehouses:
            print("  складов не найдено")
            continue

        for w in warehouses:
            print(f"  id={w.get('id')!r}  name={w.get('name')!r}  officeId={w.get('officeId')!r}"
                  f"  cargoType={w.get('cargoType')!r}  deliveryType={w.get('deliveryType')!r}"
                  f"  isDeleting={w.get('isDeleting')!r}  isProcessing={w.get('isProcessing')!r}")

        print()


if __name__ == "__main__":
    main()
