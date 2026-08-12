import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db

TABLES = [
    "ff_stock",
    "fbs_stock",
    "fbo_stock",
    "fbs_ff_stock",
    "fbo_warehouse_stock",
]


def main() -> None:
    apply_changes = "--apply" in sys.argv

    size_before = db.DB_PATH.stat().st_size if db.DB_PATH.exists() else 0
    conn = db.get_connection()

    print(f"База: {db.DB_PATH}")
    print(f"Размер сейчас: {size_before // 1024} КБ\n")

    total_zeros = 0
    print(f"{'таблица':24} {'всего':>8} {'нулей':>8} {'останется':>10}")
    print("-" * 54)

    for table in TABLES:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        zeros = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE quantity = 0").fetchone()[0]
        total_zeros += zeros
        print(f"{table:24} {total:>8} {zeros:>8} {total - zeros:>10}")

    print("-" * 54)
    print(f"{'ИТОГО к удалению':24} {'':>8} {total_zeros:>8}\n")

    if not apply_changes:
        print("Это предпросмотр — ничего не изменено.")
        print("Чтобы удалить: python scripts/cleanup_zero_stock.py --apply")
        conn.close()
        return

    before = {t: conn.execute(f"SELECT COALESCE(SUM(quantity), 0) FROM {t}").fetchone()[0] for t in TABLES}

    with db.WRITE_LOCK:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table} WHERE quantity = 0")
        conn.commit()

    after = {t: conn.execute(f"SELECT COALESCE(SUM(quantity), 0) FROM {t}").fetchone()[0] for t in TABLES}

    print("Проверка сумм (должны совпасть — удалялись только нули):")
    ok = True
    for table in TABLES:
        same = before[table] == after[table]
        ok = ok and same
        print(
            f"  {table:24} было {before[table]:>9}  стало {after[table]:>9}  {'OK' if same else 'РАСХОЖДЕНИЕ'}"
        )

    if not ok:
        print("\nВНИМАНИЕ: суммы разошлись — откатывать вручную!")
        conn.close()
        return

    print("\nСжимаю файл базы (VACUUM)...")
    try:
        conn.isolation_level = None
        conn.execute("VACUUM")
    except Exception as e:
        print(f"  не удалось: {e} (данные всё равно удалены)")
    conn.close()

    size_after = db.DB_PATH.stat().st_size
    print(f"\nРазмер был : {size_before // 1024} КБ")
    print(f"Размер стал: {size_after // 1024} КБ")
    if size_before:
        print(
            f"Освободилось: {(size_before - size_after) // 1024} КБ "
            f"({100 - size_after * 100 // size_before}%)"
        )


if __name__ == "__main__":
    main()
