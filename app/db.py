import hashlib
import sqlite3
import threading
from pathlib import Path

# app/db.py живёт на уровень глубже корня проекта — data/ остаётся в корне.
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "checkstock.db"

# SQLite лочит на запись весь файл целиком, а не отдельную таблицу — этим локом
# сериализуем фактическую запись между разными частями приложения (синхронизация
# с WB в нескольких потоках, ручная загрузка остатков на ФФ и т.п.), чтобы не
# ловить "database is locked" при параллельной работе.
WRITE_LOCK = threading.Lock()

# Маркетплейсы, по которым разделяются остатки на фулфилментах.
# Пока весь товар лежит как WB — остальные появятся, когда пойдут поставки.
MARKETPLACES = ["WB", "OZON", "YANDEX MARKET"]
DEFAULT_MARKETPLACE = "WB"

FULFILLMENTS = [
    "ФулСервис Подольск",
    "AFFLATUS Купавна",
    "ФФ Самара",
    "ФФ GO Екатеринбург",
    "ФФ Царицыно Казань",
    "ФФ Бабай",
    "Чувашия (Козловка)",
]

# Каталог товаров (артикул/баркод/название) — без количеств.
# Количества живут отдельно: mp_stock / mp_warehouse_stock (остатки маркетплейсов)
# и ff_stock (остатки на наших фулфилментах).
STOCK_ITEMS_SEED = [
    ("rimili", "949558341", "2050292584830", "Ретро гирлянда 20 метров 40 ламп"),
    ("rimili", "949563410", "2050292688583", "Ретро гирлянда 20 метров 60 ламп"),
    ("rimili", "949725484", "2050294733366", "Ретро гирлянда 10 метров 30 ламп Солнечная бат"),
]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fulfillments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
        """
    )

    # Каталог товаров по магазину
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'WB',
            article TEXT NOT NULL,
            barcode TEXT NOT NULL,
            name TEXT NOT NULL,
            mp_sku TEXT,
            mp_product_id TEXT,
            is_service INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, marketplace, article)
        )
        """
    )



    # Остатки на фулфилментах — по товару и по конкретному ФФ
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ff_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'WB',
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, fulfillment, marketplace)
        )
        """
    )


    # Сопоставление наших фулфилментов со складами продавца в WB (по названию).
    # Заполняется автоматически при каждой синхронизации FBS. История не ведётся.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ff_warehouse_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            wb_warehouse_id INTEGER NOT NULL,
            wb_warehouse_name TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(store_slug, fulfillment)
        )
        """
    )


    # Журнал загруженных поставок на ФФ — чтобы не прибавить одну и ту же
    # поставку дважды (например, если файл/ссылку случайно загрузили повторно).
    # Дубль определяется по ссылке на Google Таблицу (если грузили по ссылке)
    # либо по названию самой таблицы/файла (обычно это номер поставки).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ff_stock_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            source_type TEXT NOT NULL,
            sheet_url TEXT,
            table_title TEXT NOT NULL,
            total_rows INTEGER NOT NULL DEFAULT 0,
            matched INTEGER NOT NULL DEFAULT 0,
            unmatched INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    # Мусорка: товар, который у нас числился, а фулфилмент сказал, что его нет.
    #
    # Отдельная таблица, а не псевдо-фулфилмент внутри ff_stock. Иначе мусорку
    # пришлось бы исключать из каждого подсчёта доступного остатка, из списков
    # выбора склада и из подсказок поиска — и достаточно один раз забыть, чтобы
    # потерянный товар снова считался пригодным к отгрузке.
    #
    # Склад-источник сохраняем: важно не только «сколько потеряли», но и «где».
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trash_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            checked INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, marketplace, fulfillment)
        )
        """
    )

    # Состояние доступов: что ответила площадка при последней синхронизации.
    #
    # Нужно, чтобы страница магазина могла предупредить о неработающем ключе,
    # не дёргая ради этого API. Синхронизация идёт раз в полчаса, а магазин
    # открывают десятки раз в день — проверять доступ на каждом открытии
    # значило бы жечь лимиты площадок впустую.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            scope TEXT NOT NULL,
            ok INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            checked_at TEXT NOT NULL,
            UNIQUE(store_slug, marketplace, scope)
        )
        """
    )

    # Уже использованные источники: файлы, ссылки на таблицы.
    #
    # Защита от повторного проведения одной и той же бумаги. Отпечаток —
    # для файла хеш содержимого, для Google Таблицы её ссылка. Именно хеш,
    # а не имя файла: «поставка.xlsx» переименовывают в «поставка (1).xlsx»
    # и грузят снова, а содержимое при этом то же самое.
    #
    # Запись появляется ТОЛЬКО после успешно проведённой операции. Если
    # операция упала на проверке остатков, источник остаётся свободным —
    # иначе исправить файл и загрузить заново было бы нельзя.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS used_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            kind TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            label TEXT NOT NULL,
            source_type TEXT NOT NULL,
            operation_id INTEGER,
            user_name TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(store_slug, kind, fingerprint)
        )
        """
    )

    # Сотрудники и их роли. Пароль хранится только хешем (см. app/auth.py).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            google_email TEXT NOT NULL,
            login TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            can_edit_stock INTEGER NOT NULL DEFAULT 1,
            can_manage_users INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )

    # Активные сессии (кука -> пользователь). Логаут = удаление строки.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )

    # Журнал действий сотрудников — что именно сделали, а не какие кнопки жали.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    # Срок действия ключей WB. Обновляется еженедельно (по воскресеньям),
    # чтобы вовремя предупредить о протухающем ключе.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wb_token_info (
            store_slug TEXT PRIMARY KEY,
            expires_at TEXT,
            checked_at TEXT NOT NULL
        )
        """
    )

    # История перемещений между фулфилментами и маркетплейсами.
    # Хранится позиционно: по каждому товару видно, сколько и куда уехало.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ff_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            from_fulfillment TEXT NOT NULL,
            from_marketplace TEXT NOT NULL,
            to_fulfillment TEXT NOT NULL,
            to_marketplace TEXT NOT NULL,
            user_id INTEGER,
            user_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Операции со стоком: поставка, ручная докладка, перемещение.
    # Храним не сами файлы, а применённые строки — файл для скачивания
    # собирается на лету. Так одинаково работает и для загрузки файлом,
    # и по ссылке, и для ручного ввода, и ничего не распухает на диске.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_name TEXT,
            sheet_url TEXT,
            from_fulfillment TEXT,
            from_marketplace TEXT,
            to_fulfillment TEXT,
            to_marketplace TEXT,
            note TEXT,
            user_id INTEGER,
            user_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_operation_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id INTEGER NOT NULL,
            article TEXT NOT NULL,
            barcode TEXT,
            name TEXT,
            quantity INTEGER NOT NULL
        )
        """
    )

    _migrate_activity_log_operation(conn)

    # ------------------------------------------------------------------
    # Остатки маркетплейсов — единые таблицы вместо четырёх раздельных.
    #
    # Ключ везде включает marketplace и scheme, поэтому остатки WB и Ozon
    # физически не могут наложиться друг на друга: это разные строки даже
    # для одного и того же товара. Раньше fbs_stock/fbo_stock не знали про
    # маркетплейс, и запись Ozon затёрла бы данные WB.
    #
    # scheme: 'fbs' | 'fbo' | 'rfbs' (у WB только первые две).
    # ------------------------------------------------------------------
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mp_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            scheme TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, marketplace, scheme)
        )
        """
    )

    # Разрез по складам. warehouse — склад маркетплейса (ГРИВНО_РФЦ, Коледино)
    # или наш фулфилмент, если маркетплейс отдаёт остатки в разрезе ФФ.
    # cluster заполняет только Ozon — у него 37 складов группируются в кластеры.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mp_warehouse_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            marketplace TEXT NOT NULL,
            scheme TEXT NOT NULL,
            warehouse TEXT NOT NULL,
            cluster TEXT,
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, marketplace, scheme, warehouse)
        )
        """
    )

    # Кластеры складов маркетплейса. Не привязаны к магазину: склад
    # ХОРУГВИНО_РФЦ лежит в одном и том же кластере для всех кабинетов,
    # поэтому спрашивать это у API по каждому магазину — пустая трата
    # лимитов. Меняется раз в год, поэтому храним и переспрашиваем редко.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mp_warehouse_cluster (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketplace TEXT NOT NULL,
            warehouse TEXT NOT NULL,
            cluster TEXT NOT NULL,
            updated_at TEXT,
            UNIQUE(marketplace, warehouse)
        )
        """
    )

    _migrate_stock_to_unified(conn)

    _migrate_ff_stock_marketplace(conn)

    _migrate_catalog_marketplace(conn)

    _migrate_operation_note(conn)

    _migrate_user_permissions(conn)

    _migrate_trash_checked(conn)

    _migrate_mp_updated_at(conn)

    _migrate_delivery_marketplace(conn)

    conn.commit()
    conn.close()


def _migrate_mp_updated_at(conn: sqlite3.Connection) -> None:
    """Дата последнего изменения карточки НА ПЛОЩАДКЕ.

    Не путать с updated_at: тот про нашу базу и меняется, когда карточку
    правим мы. Этот приходит от площадки и отвечает на другой вопрос —
    когда товар последний раз трогали в кабинете. По нему видно залежавшиеся
    карточки, которые никто не ведёт.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stock_items)")}
    if "mp_updated_at" not in columns:
        conn.execute("ALTER TABLE stock_items ADD COLUMN mp_updated_at TEXT")


def _migrate_delivery_marketplace(conn: sqlite3.Connection) -> None:
    """Маркетплейс в журнале поставок.

    Журнал ловит повторную загрузку одной и той же поставки по ссылке или
    названию файла. Без площадки он ловил и то, что дублем не является:
    остатки ФФ по WB и по Ozon обычно ведут в таблицах с одинаковыми
    названиями, и вторая площадка отвергалась как «уже загружено».

    Старые записи помечаем WB — до этой правки других поставок и не было.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(ff_stock_deliveries)")}
    if "marketplace" not in columns:
        conn.execute(
            "ALTER TABLE ff_stock_deliveries ADD COLUMN marketplace TEXT NOT NULL DEFAULT 'WB'"
        )


def _migrate_stock_to_unified(conn: sqlite3.Connection) -> None:
    """Переносит остатки из старых раздельных таблиц в единые.

    Старые таблицы не знали про маркетплейс, и всё, что в них лежит, —
    это данные WB, поэтому переносим их с marketplace='WB'.
    После переноса старые таблицы удаляются, чтобы не остаться с двумя
    источниками правды, которые начнут расходиться.
    """
    existing = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    moves = [
        # (старая таблица, схема, колонка склада или None)
        ("fbs_stock", "fbs", None),
        ("fbo_stock", "fbo", None),
        ("fbs_ff_stock", "fbs", "fulfillment"),
        ("fbo_warehouse_stock", "fbo", "warehouse"),
    ]

    for table, scheme, warehouse_column in moves:
        if table not in existing:
            continue

        if warehouse_column is None:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO mp_stock
                    (store_slug, article, marketplace, scheme, quantity, updated_at)
                SELECT store_slug, article, 'WB', ?, quantity, updated_at FROM {table}
                """,
                (scheme,),
            )
        else:
            conn.execute(
                f"""
                INSERT OR IGNORE INTO mp_warehouse_stock
                    (store_slug, article, marketplace, scheme, warehouse, quantity, updated_at)
                SELECT store_slug, article, 'WB', ?, {warehouse_column}, quantity, updated_at
                FROM {table}
                """,
                (scheme,),
            )

        conn.execute(f"DROP TABLE {table}")


def _migrate_trash_checked(conn: sqlite3.Connection) -> None:
    """Отметка «разобрались» у позиции в мусорке.

    Мусорка копится, и без отметки к ней перестают возвращаться: непонятно,
    что уже выяснили с фулфилментом, а что нет. Новых ограничений нет —
    хватает ALTER TABLE.
    """
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(trash_stock)")]
    if columns and "checked" not in columns:
        conn.execute("ALTER TABLE trash_stock ADD COLUMN checked INTEGER NOT NULL DEFAULT 0")


def _migrate_user_permissions(conn: sqlite3.Connection) -> None:
    """Отдельные разрешения поверх роли.

    Роль отвечает на вопрос «кто это», разрешение — «что ему можно сейчас».
    Их приходится разделять: бывает сотрудник с ролью пользователя, которому
    временно нельзя менять остатки, и суперадмин на тестовом стенде, которому
    нельзя трогать сотрудников. Заводить под каждый такой случай новую роль
    значит получить десяток ролей, в которых никто не разберётся.

    Первичная раскладка: обычным пользователям правку остатков выключаем,
    включать её будет админ по мере того, как человек освоился.
    """
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
    if not columns:
        return

    if "can_edit_stock" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN can_edit_stock INTEGER NOT NULL DEFAULT 1")
        conn.execute("UPDATE users SET can_edit_stock = 0 WHERE role = 'user'")

    if "can_manage_users" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN can_manage_users INTEGER NOT NULL DEFAULT 1")
        # тестовый стенд смотрит админку, но никого не заводит и не правит
        conn.execute("UPDATE users SET can_manage_users = 0 WHERE login = 'test'")


def _migrate_operation_note(conn: sqlite3.Connection) -> None:
    """Примечание к операции — например, номер отгрузки или получатель.
    Новых ограничений нет, поэтому хватает обычного ALTER TABLE."""
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(stock_operations)")]
    if columns and "note" not in columns:
        conn.execute("ALTER TABLE stock_operations ADD COLUMN note TEXT")


def _migrate_catalog_marketplace(conn: sqlite3.Connection) -> None:
    """Разделяет каталог по маркетплейсам.

    До этого каталог был один на магазин и молча считался каталогом WB.
    Практика показала, что это неверно: у одного магазина на Ozon свой
    ассортимент — 232 карточки против 433 позиций WB, причём у части товаров
    там другой артикул и другой баркод. Складывать их в одну таблицу значит
    либо терять карточки, либо показывать несуществующие.

    Пересоздаём таблицу, потому что старый UNIQUE(store_slug, article)
    запретил бы один и тот же артикул под разными маркетплейсами.
    Всё, что накоплено, — это WB.
    """
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(stock_items)")]
    if not columns or "marketplace" in columns:
        return  # таблицы ещё нет (создастся сразу новой) или миграция уже прошла

    conn.executescript(
        """
        ALTER TABLE stock_items RENAME TO stock_items_old;

        CREATE TABLE stock_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'WB',
            article TEXT NOT NULL,
            barcode TEXT NOT NULL,
            name TEXT NOT NULL,
            mp_sku TEXT,
            mp_product_id TEXT,
            is_service INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, marketplace, article)
        );

        -- id сохраняем: по нему идёт сортировка каталога в интерфейсе,
        -- и порядок товаров для пользователя не должен перемешаться
        INSERT INTO stock_items (id, store_slug, marketplace, article, barcode, name)
        SELECT id, store_slug, 'WB', article, barcode, name FROM stock_items_old;

        DROP TABLE stock_items_old;
        """
    )


def _migrate_activity_log_operation(conn: sqlite3.Connection) -> None:
    """Добавляет ссылку на операцию в журнал действий. Здесь хватает
    обычного ALTER TABLE: новых ограничений не появляется."""
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(activity_log)")]
    if columns and "operation_id" not in columns:
        conn.execute("ALTER TABLE activity_log ADD COLUMN operation_id INTEGER")


def _migrate_ff_stock_marketplace(conn: sqlite3.Connection) -> None:
    """Добавляет колонку marketplace в уже существующую ff_stock.

    Простым ALTER TABLE не обойтись: в старой таблице стоит
    UNIQUE(store_slug, article, fulfillment), и он бы запретил хранить один и
    тот же товар на одном ФФ по разным маркетплейсам. Поэтому таблица
    пересоздаётся с новым UNIQUE, а все старые записи помечаются как WB.
    """
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(ff_stock)")]
    if not columns or "marketplace" in columns:
        return  # таблицы ещё нет (создастся сразу новой) или миграция уже прошла

    conn.executescript(
        """
        ALTER TABLE ff_stock RENAME TO ff_stock_old;

        CREATE TABLE ff_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_slug TEXT NOT NULL,
            article TEXT NOT NULL,
            fulfillment TEXT NOT NULL,
            marketplace TEXT NOT NULL DEFAULT 'WB',
            quantity INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            UNIQUE(store_slug, article, fulfillment, marketplace)
        );

        INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
        SELECT store_slug, article, fulfillment, 'WB', quantity, updated_at FROM ff_stock_old;

        DROP TABLE ff_stock_old;
        """
    )


def seed_defaults() -> None:
    conn = get_connection()
    for name in FULFILLMENTS:
        conn.execute("INSERT OR IGNORE INTO fulfillments (name) VALUES (?)", (name,))
    for store_slug, article, barcode, name in STOCK_ITEMS_SEED:
        conn.execute(
            """
            INSERT OR IGNORE INTO stock_items (store_slug, marketplace, article, barcode, name)
            VALUES (?, 'WB', ?, ?, ?)
            """,
            (store_slug, article, barcode, name),
        )
    conn.commit()
    conn.close()


def get_fulfillments() -> list[str]:
    conn = get_connection()
    rows = conn.execute("SELECT name FROM fulfillments ORDER BY id").fetchall()
    conn.close()
    return [row["name"] for row in rows]


def get_catalog_items(store_slug: str, marketplace: str = "WB",
                     include_service: bool = False) -> list[dict]:
    """Каталог товаров магазина на одном маркетплейсе (без количеств).

    Ассортименты площадок не совпадают, поэтому marketplace здесь не
    декоративный параметр: каталог WB и каталог Ozon — это разные списки
    товаров с разными артикулами и баркодами.

    Служебные позиции (на Ozon в каталоге лежат «Инструкция мойка 1» и
    подобные) по умолчанию скрыты — товаром они не являются.
    """
    conn = get_connection()
    sql = """
        SELECT article, barcode, name, mp_sku, mp_product_id, mp_updated_at
        FROM stock_items
        WHERE store_slug = ? AND marketplace = ?
    """
    if not include_service:
        sql += " AND is_service = 0"
    sql += " ORDER BY id"
    rows = conn.execute(sql, (store_slug, marketplace)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stock_items(store_slug: str, marketplace: str,
                    schemes: tuple[str, ...] | None = None) -> list[dict]:
    """Каталог товаров магазина вместе с остатками по ОДНОМУ маркетплейсу.

    marketplace обязателен: без него остатки разных площадок смешались бы
    в одной строке.

    schemes — какие схемы продаж вытащить. Набор разный у площадок: у WB две,
    у Ozon три, а у Яндекса он вообще зависит от магазина — там на каждый
    FBS-кабинет своя схема, потому что это разные склады разных партнёров.
    Поэтому подзапросы собираются по списку, а не зашиты в текст запроса.

    Каждая схема приходит полем "<схема>_stock".
    """
    schemes = tuple(schemes or ("fbs", "rfbs", "fbo"))

    joins = []
    columns = []
    params: list = []

    for index, scheme in enumerate(schemes):
        alias = f"s{index}"
        columns.append(f"{alias}.quantity AS {scheme}_stock")
        joins.append(
            f"LEFT JOIN mp_stock {alias}"
            f" ON {alias}.store_slug = si.store_slug AND {alias}.article = si.article"
            f" AND {alias}.marketplace = ? AND {alias}.scheme = ?"
        )
        params.extend([marketplace, scheme])

    params.append(marketplace)          # подзапрос по остаткам ФФ
    params.extend([store_slug, marketplace])

    sql = f"""
        SELECT
            si.article,
            si.barcode,
            si.name,
            si.mp_updated_at,
            ff.total_qty AS ff_available,
            {", ".join(columns)}
        FROM stock_items si
        {" ".join(joins)}
        LEFT JOIN (
            SELECT store_slug, article, SUM(quantity) AS total_qty
            FROM ff_stock WHERE marketplace = ?
            GROUP BY store_slug, article
        ) ff
            ON ff.store_slug = si.store_slug AND ff.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
        ORDER BY si.id
    """

    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def articles_with_own_stock(store_slug: str, marketplace: str,
                            conn: sqlite3.Connection | None = None) -> set[str]:
    """Артикулы, по которым у нас числится СВОЙ остаток: на фулфилменте или
    в мусорке. Остатки самой площадки сюда не входят — они приходят от неё же
    и вместе с карточкой исчезнут.

    Используется и синхронизацией каталога, и скриптом предпросмотра, поэтому
    принимает готовое соединение: внутри replace_catalog открывать второе
    подключение к тому же файлу незачем.
    """
    own = conn or get_connection()
    rows = own.execute(
        """
        SELECT article FROM ff_stock
         WHERE store_slug = ? AND marketplace = ? AND quantity <> 0
        UNION
        SELECT article FROM trash_stock
         WHERE store_slug = ? AND marketplace = ? AND quantity <> 0
        """,
        (store_slug, marketplace, store_slug, marketplace),
    ).fetchall()
    if conn is None:
        own.close()
    return {row["article"] for row in rows}


def replace_catalog(store_slug: str, marketplace: str, items: list[dict],
                    updated_at: str) -> dict:
    """Обновляет каталог одного магазина на одном маркетплейсе.

    items — список словарей с ключами article, barcode, name и необязательными
    mp_sku, mp_product_id, is_service.

    Не полная перезапись, а согласование: существующие карточки обновляются,
    новые добавляются, пропавшие удаляются. Так сохраняются id, а по ним идёт
    сортировка каталога в интерфейсе — при полной перезаписи порядок товаров
    у пользователя перемешивался бы после каждой синхронизации.

    Каталоги других маркетплейсов не затрагиваются: marketplace входит в
    условие и в UNIQUE-ключ таблицы.

    Позиции, по которым у нас числится собственный остаток (на фулфилменте
    или в мусорке), не удаляются, даже если площадка их больше не отдаёт.
    Карточку убирают из кабинета, когда товар распродан НА ПЛОЩАДКЕ, но на
    нашем складе он при этом лежать продолжает. Удалив такую строку, мы
    оставили бы остаток висеть на артикуле, которого нет в каталоге: он
    пропал бы из таблицы, но остался в базе, и отгрузить его стало бы нельзя.

    Возвращает {"added": n, "updated": n, "removed": n, "kept": n} для отчёта.
    """
    conn = get_connection()

    protected = articles_with_own_stock(store_slug, marketplace, conn)

    existing = {
        row["article"]: row
        for row in conn.execute(
            "SELECT article, barcode, name, mp_sku, mp_product_id, is_service,"
            " mp_updated_at FROM stock_items WHERE store_slug = ? AND marketplace = ?",
            (store_slug, marketplace),
        )
    }

    seen: set[str] = set()
    added = updated = 0

    for item in items:
        article = str(item.get("article") or "").strip()
        if not article:
            continue
        seen.add(article)

        row = (
            str(item.get("barcode") or ""),
            str(item.get("name") or ""),
            str(item.get("mp_sku") or "") or None,
            str(item.get("mp_product_id") or "") or None,
            1 if item.get("is_service") else 0,
            str(item.get("mp_updated_at") or "") or None,
        )

        old = existing.get(article)
        if old is None:
            conn.execute(
                """
                INSERT INTO stock_items
                    (store_slug, marketplace, article, barcode, name,
                     mp_sku, mp_product_id, is_service, mp_updated_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (store_slug, marketplace, article, *row, updated_at),
            )
            added += 1
            continue

        current = (
            old["barcode"], old["name"],
            old["mp_sku"], old["mp_product_id"], old["is_service"],
            old["mp_updated_at"],
        )
        if current == row:
            continue  # ничего не изменилось — не трогаем строку

        conn.execute(
            """
            UPDATE stock_items
               SET barcode = ?, name = ?, mp_sku = ?, mp_product_id = ?,
                   is_service = ?, mp_updated_at = ?, updated_at = ?
             WHERE store_slug = ? AND marketplace = ? AND article = ?
            """,
            (*row, updated_at, store_slug, marketplace, article),
        )
        updated += 1

    missing = set(existing) - seen
    kept = sorted(missing & protected)
    gone = sorted(missing - protected)

    for article in gone:
        conn.execute(
            "DELETE FROM stock_items WHERE store_slug = ? AND marketplace = ? AND article = ?",
            (store_slug, marketplace, article),
        )

    conn.commit()
    conn.close()
    return {"added": added, "updated": updated,
            "removed": len(gone), "kept": len(kept)}


def upsert_mp_stock(
    store_slug: str, article: str, marketplace: str, scheme: str,
    quantity: int, updated_at: str,
) -> None:
    """Тотал по товару в разрезе маркетплейса и схемы.

    Ноль удаляет строку: отсутствие строки и ноль означают одно и то же,
    а нулей в остатках подавляющее большинство.
    """
    conn = get_connection()
    if quantity:
        conn.execute(
            """
            INSERT INTO mp_stock (store_slug, article, marketplace, scheme, quantity, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, article, marketplace, scheme)
            DO UPDATE SET quantity = excluded.quantity, updated_at = excluded.updated_at
            """,
            (store_slug, article, marketplace, scheme, quantity, updated_at),
        )
    else:
        conn.execute(
            """
            DELETE FROM mp_stock
            WHERE store_slug = ? AND article = ? AND marketplace = ? AND scheme = ?
            """,
            (store_slug, article, marketplace, scheme),
        )
    conn.commit()
    conn.close()


def replace_mp_warehouse_stock(
    store_slug: str, marketplace: str, scheme: str,
    entries: list[tuple[str, str, str | None, int, str]],
) -> None:
    """Полностью заменяет разрез по складам для пары (маркетплейс, схема).

    entries — (article, warehouse, cluster, quantity, updated_at).
    Удаляем строго свою выборку: чужой маркетплейс и чужая схема не трогаются.
    """
    conn = get_connection()
    conn.execute(
        "DELETE FROM mp_warehouse_stock WHERE store_slug = ? AND marketplace = ? AND scheme = ?",
        (store_slug, marketplace, scheme),
    )
    conn.executemany(
        """
        INSERT INTO mp_warehouse_stock
            (store_slug, article, marketplace, scheme, warehouse, cluster, quantity, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (store_slug, article, marketplace, scheme, warehouse, cluster, quantity, updated_at)
            for article, warehouse, cluster, quantity, updated_at in entries
            if quantity  # нулевые остатки не храним
        ],
    )
    conn.commit()
    conn.close()


def get_warehouse_clusters(marketplace: str) -> dict[str, str]:
    """{склад: кластер} для маркетплейса — из нашего кэша, без обращения к API."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT warehouse, cluster FROM mp_warehouse_cluster WHERE marketplace = ?",
        (marketplace,),
    ).fetchall()
    conn.close()
    return {row["warehouse"]: row["cluster"] for row in rows}


def save_warehouse_clusters(marketplace: str, mapping: dict[str, str],
                            updated_at: str) -> int:
    """Дописывает соответствия склад-кластер. Возвращает число новых записей."""
    if not mapping:
        return 0

    conn = get_connection()
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM mp_warehouse_cluster WHERE marketplace = ?",
        (marketplace,),
    ).fetchone()["n"]

    conn.executemany(
        """
        INSERT INTO mp_warehouse_cluster (marketplace, warehouse, cluster, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(marketplace, warehouse)
        DO UPDATE SET cluster = excluded.cluster, updated_at = excluded.updated_at
        """,
        [(marketplace, w, c, updated_at) for w, c in mapping.items() if w and c],
    )
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM mp_warehouse_cluster WHERE marketplace = ?",
        (marketplace,),
    ).fetchone()["n"]
    conn.commit()
    conn.close()
    return after - before


def get_mp_warehouse_details(
    store_slug: str, marketplace: str, scheme: str, group_by_cluster: bool = False
) -> list[dict]:
    """Детализация по складам (или кластерам) для одной пары маркетплейс+схема."""
    column = "COALESCE(NULLIF(ws.cluster, ''), ws.warehouse)" if group_by_cluster else "ws.warehouse"
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT si.article, si.barcode, si.name,
               {column} AS warehouse,
               SUM(ws.quantity) AS quantity
        FROM stock_items si
        JOIN mp_warehouse_stock ws
            ON ws.store_slug = si.store_slug AND ws.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
          AND ws.marketplace = ? AND ws.scheme = ?
        GROUP BY si.id, {column}
        ORDER BY si.id, {column}
        """,
        (store_slug, marketplace, marketplace, scheme),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_mp_stock_by_warehouse(
    store_slug: str, marketplace: str, scheme: str, warehouse: str
) -> dict[str, int]:
    """Остатки на конкретном складе: {article: quantity}."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT article, SUM(quantity) AS quantity FROM mp_warehouse_stock
        WHERE store_slug = ? AND marketplace = ? AND scheme = ? AND warehouse = ?
        GROUP BY article
        """,
        (store_slug, marketplace, scheme, warehouse),
    ).fetchall()
    conn.close()
    return {row["article"]: row["quantity"] for row in rows}


def get_mp_stock_totals(store_slug: str, marketplace: str, scheme: str) -> dict[str, int]:
    """Тоталы по товару для пары маркетплейс+схема: {article: quantity}."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT article, quantity FROM mp_stock
        WHERE store_slug = ? AND marketplace = ? AND scheme = ?
        """,
        (store_slug, marketplace, scheme),
    ).fetchall()
    conn.close()
    return {row["article"]: row["quantity"] for row in rows}


def upsert_ff_stock(
    store_slug: str,
    article: str,
    fulfillment: str,
    quantity: int,
    updated_at: str,
    marketplace: str = DEFAULT_MARKETPLACE,
) -> None:
    """Перезаписывает остаток на ФФ (используется для точечных ручных правок).
    Для загрузки поставок используй increment_ff_stock — там нужно прибавлять,
    а не перезатирать (см. модуль ff_import)."""
    conn = get_connection()
    if quantity:
        conn.execute(
            """
            INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, article, fulfillment, marketplace)
            DO UPDATE SET quantity = excluded.quantity, updated_at = excluded.updated_at
            """,
            (store_slug, article, fulfillment, marketplace, quantity, updated_at),
        )
    else:
        # ноль = товара на этом ФФ нет, строка не нужна
        conn.execute(
            """
            DELETE FROM ff_stock
            WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
            """,
            (store_slug, article, fulfillment, marketplace),
        )
    conn.commit()
    conn.close()


def increment_ff_stock(
    store_slug: str,
    article: str,
    fulfillment: str,
    delta: int,
    updated_at: str,
    marketplace: str = DEFAULT_MARKETPLACE,
) -> None:
    """Прибавляет delta к текущему остатку на ФФ, а не перезаписывает —
    так каждая загруженная поставка складывается с уже имеющимся остатком."""
    if not delta:
        return  # прибавлять ноль незачем — и пустую строку плодить тоже

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(store_slug, article, fulfillment, marketplace)
        DO UPDATE SET quantity = ff_stock.quantity + excluded.quantity, updated_at = excluded.updated_at
        """,
        (store_slug, article, fulfillment, marketplace, delta, updated_at),
    )
    # если после прибавления вышел ноль — строку убираем
    conn.execute(
        """
        DELETE FROM ff_stock
        WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ? AND quantity = 0
        """,
        (store_slug, article, fulfillment, marketplace),
    )
    conn.commit()
    conn.close()


def find_existing_delivery(store_slug: str, sheet_url: str | None, table_title: str,
                           marketplace: str = DEFAULT_MARKETPLACE) -> dict | None:
    """Ищет уже загруженную поставку — по ссылке на таблицу (если грузили по
    ссылке) или по названию таблицы/файла (обычно это номер поставки). Нужно,
    чтобы не прибавить остатки одной и той же поставки дважды.

    Площадка входит в поиск: одна и та же таблица, загруженная на вкладке WB и
    на вкладке Ozon, — это две разные поставки на два разных склада, а не
    повторная загрузка. Названия у таких файлов обычно совпадают.
    """
    conn = get_connection()
    row = None
    if sheet_url:
        row = conn.execute(
            "SELECT * FROM ff_stock_deliveries"
            " WHERE store_slug = ? AND marketplace = ? AND sheet_url = ? LIMIT 1",
            (store_slug, marketplace, sheet_url),
        ).fetchone()
    if row is None and table_title:
        row = conn.execute(
            "SELECT * FROM ff_stock_deliveries"
            " WHERE store_slug = ? AND marketplace = ? AND table_title = ? LIMIT 1",
            (store_slug, marketplace, table_title),
        ).fetchone()
    conn.close()
    return dict(row) if row is not None else None


def record_sync_health(store_slug: str, marketplace: str, scope: str,
                       ok: bool, error: str | None, checked_at: str) -> None:
    """Запоминает исход последней синхронизации по площадке.

    scope — что именно проверяли: 'fbs', 'fbo', 'catalog', 'stocks'. У площадок
    доступы гранулярные: у WB ключ может отдавать FBO и не отдавать FBS, и
    писать одну общую отметку значило бы прятать половину проблемы.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO sync_health (store_slug, marketplace, scope, ok, error, checked_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(store_slug, marketplace, scope)
        DO UPDATE SET ok = excluded.ok, error = excluded.error,
                      checked_at = excluded.checked_at
        """,
        (store_slug, marketplace, scope, 1 if ok else 0, error, checked_at),
    )
    conn.commit()
    conn.close()


def get_sync_health(store_slug: str) -> list[dict]:
    """Все известные проблемы доступа по магазину."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM sync_health WHERE store_slug = ? AND ok = 0"
        " ORDER BY marketplace, scope",
        (store_slug,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def source_fingerprint(source_type: str, sheet_url: str | None,
                       file_bytes: bytes | None) -> str | None:
    """Отпечаток источника: для ссылки — сама ссылка, для файла — хеш
    содержимого. Ручной ввод отпечатка не имеет: там человек каждый раз
    набирает позиции заново, и «повтор» — это осознанное действие."""
    if source_type == "sheet" and sheet_url:
        return f"sheet:{sheet_url.strip()}"
    if source_type == "file" and file_bytes:
        return "file:" + hashlib.sha256(file_bytes).hexdigest()
    return None


def find_used_source(store_slug: str, kind: str, fingerprint: str | None) -> dict | None:
    """Этот файл или ссылку уже проводили по этому магазину и типу операции?"""
    if not fingerprint:
        return None
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM used_sources WHERE store_slug = ? AND kind = ? AND fingerprint = ?",
        (store_slug, kind, fingerprint),
    ).fetchone()
    conn.close()
    return dict(row) if row is not None else None


def record_used_source(
    store_slug: str,
    kind: str,
    fingerprint: str | None,
    label: str,
    source_type: str,
    operation_id: int | None,
    user_name: str,
    created_at: str,
) -> None:
    """Помечает источник использованным. Вызывать только после успеха."""
    if not fingerprint:
        return
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO used_sources
            (store_slug, kind, fingerprint, label, source_type,
             operation_id, user_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (store_slug, kind, fingerprint, label, source_type,
         operation_id, user_name, created_at),
    )
    conn.commit()
    conn.close()


def record_delivery(
    store_slug: str,
    fulfillment: str,
    source_type: str,
    sheet_url: str | None,
    table_title: str,
    total_rows: int,
    matched: int,
    unmatched: int,
    created_at: str,
    marketplace: str = DEFAULT_MARKETPLACE,
) -> None:
    """Записывает в журнал факт загрузки поставки (см. find_existing_delivery)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO ff_stock_deliveries
            (store_slug, fulfillment, marketplace, source_type, sheet_url, table_title,
             total_rows, matched, unmatched, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (store_slug, fulfillment, marketplace, source_type, sheet_url, table_title,
         total_rows, matched, unmatched, created_at),
    )
    conn.commit()
    conn.close()


def replace_ff_warehouse_map(
    store_slug: str, entries: list[tuple[str, int, str, str]]
) -> None:
    """Полностью заменяет сопоставление фулфилмент -> склад WB для магазина.
    entries — список (fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at)."""
    conn = get_connection()
    conn.execute("DELETE FROM ff_warehouse_map WHERE store_slug = ?", (store_slug,))
    conn.executemany(
        """
        INSERT INTO ff_warehouse_map (store_slug, fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (store_slug, fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at)
            for fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at in entries
        ],
    )
    conn.commit()
    conn.close()


def get_ff_warehouse_map(store_slug: str) -> list[dict]:
    """Какой склад WB сейчас сопоставлен с каким фулфилментом (для диагностики/UI)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT fulfillment, wb_warehouse_id, wb_warehouse_name, updated_at
        FROM ff_warehouse_map WHERE store_slug = ?
        ORDER BY fulfillment
        """,
        (store_slug,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_last_sync_at(marketplace: str | None = None) -> str | None:
    """Когда в последний раз обновлялись остатки. Без marketplace — по всем."""
    conn = get_connection()
    if marketplace:
        row = conn.execute(
            "SELECT MAX(updated_at) AS last_sync FROM mp_stock WHERE marketplace = ?",
            (marketplace,),
        ).fetchone()
    else:
        row = conn.execute("SELECT MAX(updated_at) AS last_sync FROM mp_stock").fetchone()
    conn.close()
    return row["last_sync"] if row is not None else None


def get_ff_available_totals(
    store_slug: str,
    fulfillment: str | None = None,
    marketplace: str | None = None,
) -> dict[str, int]:
    """Остатки "Доступно ФФ для распределения" по товару.

    Без fulfillment — сумма ff_stock по ВСЕМ фулфилментам сразу ("Общее",
    та же цифра, что в таблице при первой загрузке страницы). С fulfillment —
    только остаток на этом конкретном ФФ (для переключателя ФФ на странице).
    marketplace аналогично ограничивает выборку одним маркетплейсом.
    """
    where = ["store_slug = ?"]
    params: list = [store_slug]
    if fulfillment:
        where.append("fulfillment = ?")
        params.append(fulfillment)
    if marketplace:
        where.append("marketplace = ?")
        params.append(marketplace)

    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT article, SUM(quantity) AS total
        FROM ff_stock
        WHERE {" AND ".join(where)}
        GROUP BY article
        """,
        params,
    ).fetchall()
    conn.close()
    return {row["article"]: row["total"] for row in rows}


# ---------------------------------------------------------------------------
# Сотрудники, сессии, журнал действий
# ---------------------------------------------------------------------------

ROLES = ["superadmin", "admin", "user"]
ROLE_LABELS = {
    "superadmin": "Суперадмин",
    "admin": "Админ",
    "user": "Пользователь",
}


def create_user(
    full_name: str,
    google_email: str,
    login: str,
    password_hash: str,
    role: str,
    created_at: str,
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO users (full_name, google_email, login, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (full_name, google_email, login, password_hash, role, created_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_user_by_login(login: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user(user_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_user_permission(user_id: int, field: str, allowed: bool) -> None:
    """Меняет одно разрешение сотрудника.

    Имя поля сверяем со списком: оно приходит из запроса и подставляется
    прямо в SQL, а подставлять туда что попало нельзя.
    """
    if field not in ("can_edit_stock", "can_manage_users"):
        raise ValueError(f"неизвестное разрешение {field!r}")

    conn = get_connection()
    conn.execute(f"UPDATE users SET {field} = ? WHERE id = ?", (1 if allowed else 0, user_id))
    conn.commit()
    conn.close()


def list_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, full_name, google_email, login, role, is_active,"
        " can_edit_stock, can_manage_users, created_at FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_user_active(user_id: int, is_active: bool) -> None:
    conn = get_connection()
    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
    conn.commit()
    conn.close()


def count_users() -> int:
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    conn.close()
    return n


def create_session(token: str, user_id: int, created_at: str, expires_at: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, created_at, expires_at),
    )
    conn.commit()
    conn.close()


def get_session(token: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def delete_expired_sessions(now: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.commit()
    conn.close()


def log_action(user_id: int | None, user_name: str, action: str, details: str, created_at: str) -> None:
    """Пишет в журнал осмысленное действие (например, «загрузила поставку на ФФ»)."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (user_id, user_name, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, user_name, action, details, created_at),
    )
    conn.commit()
    conn.close()


def get_activity_log(limit: int = 200) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def search_catalog(
    store_slug: str,
    query: str,
    limit: int = 15,
    fulfillment: str | None = None,
    marketplace: str | None = None,
) -> list[dict]:
    """Поиск товара по артикулу, баркоду или названию — для подсказок.

    Фильтруем в Python, а не через SQL LIKE: LIKE в SQLite приводит регистр
    только для латиницы, поэтому «гирлянд» не нашёл бы «Гирлянда». casefold()
    корректно работает с кириллицей.

    Если заданы fulfillment и marketplace, в выдачу попадают только товары,
    которые реально лежат в этой ячейке (остаток больше нуля), и к каждому
    добавляется поле stock. Это нужно форме перемещения: предлагать товар,
    которого на источнике нет, бессмысленно.

    Порядок: сначала совпадения по началу артикула и баркода, потом вхождения
    внутри них, и только затем совпадения по названию — чтобы точный ввод кода
    не вытеснялся товарами, у которых искомое встретилось в описании.
    """
    query = (query or "").strip().casefold()
    if not query:
        return []

    stock_map = None
    if fulfillment and marketplace:
        stock_map = get_ff_available_totals(store_slug, fulfillment, marketplace)

    matches = []
    for item in get_catalog_items(store_slug, marketplace or "WB"):
        if stock_map is not None and not stock_map.get(item["article"]):
            continue  # в этой ячейке товара нет — не предлагаем

        article = item["article"].casefold()
        barcode = item["barcode"].casefold()
        name = item["name"].casefold()

        if article.startswith(query):
            rank = 0
        elif barcode.startswith(query):
            rank = 1
        elif query in article:
            rank = 2
        elif query in barcode:
            rank = 3
        elif query in name:
            rank = 4
        else:
            continue

        row = dict(item)
        if stock_map is not None:
            row["stock"] = stock_map.get(item["article"], 0)
        matches.append((rank, item["article"], row))

    matches.sort(key=lambda m: (m[0], m[1]))
    return [m[2] for m in matches[:limit]]


def delete_user(user_id: int) -> None:
    """Удаляет сотрудника и все его сессии — чтобы он тут же перестал иметь
    доступ, даже если сейчас залогинен. Записи в журнале действий остаются:
    там хранится имя строкой, поэтому история не теряется."""
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def update_user_password(user_id: int, password_hash: str) -> None:
    """Меняет пароль и разлогинивает сотрудника на всех устройствах —
    старые сессии после сброса пароля жить не должны."""
    conn = get_connection()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def count_superadmins(exclude_user_id: int | None = None) -> int:
    """Сколько активных суперадминов останется — защита от удаления последнего."""
    conn = get_connection()
    if exclude_user_id is None:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'superadmin' AND is_active = 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'superadmin' AND is_active = 1 AND id != ?",
            (exclude_user_id,),
        ).fetchone()
    conn.close()
    return row["n"]


def delete_sessions_for_user(user_id: int) -> None:
    """Разлогинивает сотрудника на всех устройствах."""
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def upsert_wb_token_info(store_slug: str, expires_at: str | None, checked_at: str) -> None:
    """Сохраняет срок действия ключа WB. expires_at = None, если из токена
    его вычитать не удалось (например, ключ введён с опечаткой)."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO wb_token_info (store_slug, expires_at, checked_at)
        VALUES (?, ?, ?)
        ON CONFLICT(store_slug)
        DO UPDATE SET expires_at = excluded.expires_at, checked_at = excluded.checked_at
        """,
        (store_slug, expires_at, checked_at),
    )
    conn.commit()
    conn.close()


def get_wb_token_infos() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT store_slug, expires_at, checked_at FROM wb_token_info ORDER BY store_slug"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_last_token_check() -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT MAX(checked_at) AS last FROM wb_token_info").fetchone()
    conn.close()
    return row["last"] if row else None


def get_ff_stock_one(store_slug: str, article: str, fulfillment: str, marketplace: str) -> int:
    """Остаток конкретного товара в конкретной ячейке (ФФ + маркетплейс).
    Ноль, если строки нет — отсутствие строки и есть ноль."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT quantity FROM ff_stock
        WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
        """,
        (store_slug, article, fulfillment, marketplace),
    ).fetchone()
    conn.close()
    return row["quantity"] if row else 0


def apply_ff_transfer(
    store_slug: str,
    entries: list[tuple[str, int]],
    from_fulfillment: str,
    from_marketplace: str,
    to_fulfillment: str,
    to_marketplace: str,
    user_id: int | None,
    user_name: str,
    created_at: str,
) -> None:
    """Переносит товары из одной ячейки (ФФ + маркетплейс) в другую.

    entries — список (article, quantity), количество положительное.

    Всё делается одной транзакцией на одном соединении: списание, зачисление
    и запись в историю. Если что-то упадёт посередине, откатится целиком —
    иначе товар мог бы «испариться», списавшись с источника и не появившись
    у получателя.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for article, quantity in entries:
            # списываем с источника
            conn.execute(
                """
                UPDATE ff_stock SET quantity = quantity - ?, updated_at = ?
                WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
                """,
                (quantity, created_at, store_slug, article, from_fulfillment, from_marketplace),
            )
            # зачисляем получателю (строки может ещё не быть)
            conn.execute(
                """
                INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, fulfillment, marketplace)
                DO UPDATE SET quantity = ff_stock.quantity + excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, article, to_fulfillment, to_marketplace, quantity, created_at),
            )
            conn.execute(
                """
                INSERT INTO ff_transfers
                    (store_slug, article, quantity, from_fulfillment, from_marketplace,
                     to_fulfillment, to_marketplace, user_id, user_name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (store_slug, article, quantity, from_fulfillment, from_marketplace,
                 to_fulfillment, to_marketplace, user_id, user_name, created_at),
            )

        # обнулившиеся ячейки не храним — политика «ноль = нет строки»
        conn.execute("DELETE FROM ff_stock WHERE store_slug = ? AND quantity = 0", (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_ff_shipment(
    store_slug: str,
    entries: list[tuple[str, int]],
    fulfillment: str,
    marketplace: str,
    created_at: str,
) -> None:
    """Списывает товары с ячейки (ФФ + маркетплейс) — отгрузка со склада.

    entries — список (article, quantity), количество положительное.

    Одна транзакция на всё: либо списались все позиции, либо ни одной.
    Наличие проверяется вызывающим кодом до входа сюда, но на всякий случай
    ещё раз убеждаемся, что в минус не ушли: отрицательный остаток на складе
    физически невозможен и означал бы ошибку в расчётах.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for article, quantity in entries:
            conn.execute(
                """
                UPDATE ff_stock SET quantity = quantity - ?, updated_at = ?
                WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
                """,
                (quantity, created_at, store_slug, article, fulfillment, marketplace),
            )

        negative = conn.execute(
            """
            SELECT article, quantity FROM ff_stock
            WHERE store_slug = ? AND fulfillment = ? AND marketplace = ? AND quantity < 0
            """,
            (store_slug, fulfillment, marketplace),
        ).fetchall()
        if negative:
            raise ValueError(
                "отгрузка увела бы остаток в минус: "
                + ", ".join(f"{r['article']} -> {r['quantity']}" for r in negative)
            )

        # обнулившиеся ячейки не храним — политика «ноль = нет строки»
        conn.execute("DELETE FROM ff_stock WHERE store_slug = ? AND quantity = 0", (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_ff_trash(
    store_slug: str,
    entries: list[tuple[str, int]],
    fulfillment: str,
    marketplace: str,
    created_at: str,
) -> None:
    """Переносит товар с фулфилмента в мусорку.

    entries — список (article, quantity), количество положительное.

    Это не отгрузка: товар никуда не уехал, он просто не нашёлся. Поэтому
    количество не исчезает, а перекладывается в trash_stock — иначе к нему
    нельзя было бы вернуться и разобраться, где именно теряется товар.

    Всё одной транзакцией: либо списалось и попало в мусорку, либо ничего.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for article, quantity in entries:
            conn.execute(
                """
                UPDATE ff_stock SET quantity = quantity - ?, updated_at = ?
                WHERE store_slug = ? AND article = ? AND fulfillment = ? AND marketplace = ?
                """,
                (quantity, created_at, store_slug, article, fulfillment, marketplace),
            )
            conn.execute(
                """
                INSERT INTO trash_stock
                    (store_slug, article, marketplace, fulfillment, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, marketplace, fulfillment)
                DO UPDATE SET quantity = trash_stock.quantity + excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, article, marketplace, fulfillment, quantity, created_at),
            )

        negative = conn.execute(
            """
            SELECT article, quantity FROM ff_stock
            WHERE store_slug = ? AND fulfillment = ? AND marketplace = ? AND quantity < 0
            """,
            (store_slug, fulfillment, marketplace),
        ).fetchall()
        if negative:
            raise ValueError(
                "списание в мусорку увело бы остаток в минус: "
                + ", ".join(f"{r['article']} -> {r['quantity']}" for r in negative)
            )

        conn.execute("DELETE FROM ff_stock WHERE store_slug = ? AND quantity = 0", (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_trash_checked(store_slug: str, marketplace: str, article: str,
                      fulfillment: str, checked: bool) -> None:
    """Отмечает позицию мусорки как разобранную (или снимает отметку)."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE trash_stock SET checked = ?
        WHERE store_slug = ? AND marketplace = ? AND article = ? AND fulfillment = ?
        """,
        (1 if checked else 0, store_slug, marketplace, article, fulfillment),
    )
    conn.commit()
    conn.close()


def apply_ff_surplus(
    store_slug: str,
    entries: list[tuple[str, int]],
    fulfillment: str,
    marketplace: str,
    created_at: str,
) -> None:
    """Излишек: фулфилмент отдал больше, чем у нас числилось.

    entries — список (article, quantity) с положительным количеством; знак
    минуса разбирается уровнем выше.

    Механика та же, что у мусорки, но с обратным знаком: остаток на складе
    увеличивается, а запись в мусорке уменьшается — вплоть до отрицательной.
    Минус в мусорке не ошибка, а ровно то, что произошло: по этому товару
    фулфилмент отдал больше, чем мы считали. Запретить это значило бы
    заставить оператора округлять факт до удобного.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN")

        for article, quantity in entries:
            conn.execute(
                """
                INSERT INTO trash_stock
                    (store_slug, article, marketplace, fulfillment, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, marketplace, fulfillment)
                DO UPDATE SET quantity = trash_stock.quantity - excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, article, marketplace, fulfillment, -quantity, created_at),
            )
            conn.execute(
                """
                INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, article, fulfillment, marketplace)
                DO UPDATE SET quantity = ff_stock.quantity + excluded.quantity,
                              updated_at = excluded.updated_at
                """,
                (store_slug, article, fulfillment, marketplace, quantity, created_at),
            )

        # ровно нулевые строки не держим, отрицательные оставляем: это факт
        conn.execute("DELETE FROM trash_stock WHERE store_slug = ? AND quantity = 0",
                     (store_slug,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_trash_details(store_slug: str, marketplace: str) -> list[dict]:
    """Содержимое мусорки в том же виде, что и детализация складов:
    строка на товар и склад, с которого он потерялся."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT si.article, si.barcode, si.name,
               t.fulfillment AS warehouse,
               t.quantity AS quantity,
               t.checked AS checked
        FROM trash_stock t
        JOIN stock_items si
            ON si.store_slug = t.store_slug AND si.article = t.article
           AND si.marketplace = t.marketplace
        -- отрицательные строки тоже показываем: это излишек, и он не менее
        -- важен, чем недостача. Прятать его значило бы скрывать расхождение
        WHERE t.store_slug = ? AND t.marketplace = ? AND t.quantity <> 0
        ORDER BY si.id, t.fulfillment
        """,
        (store_slug, marketplace),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_ff_transfers(store_slug: str | None = None, limit: int = 200) -> list[dict]:
    conn = get_connection()
    if store_slug:
        rows = conn.execute(
            "SELECT * FROM ff_transfers WHERE store_slug = ? ORDER BY id DESC LIMIT ?",
            (store_slug, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ff_transfers ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Операции со стоком (для журнала и выгрузки в xlsx)
# ---------------------------------------------------------------------------

OPERATION_LABELS = {
    "delivery": "Поставка на ФФ",
    "manual_add": "Ручная докладка",
    "transfer": "Перемещение",
    "shipment": "Отгрузка со стока",
    "trash": "Списание в мусорку",
}

SOURCE_LABELS = {
    "file": "файл",
    "sheet": "Google Таблица",
    "manual": "ручной ввод",
}


def record_operation(
    store_slug: str,
    kind: str,
    source_type: str,
    items: list[dict],
    user_id: int | None,
    user_name: str,
    created_at: str,
    source_name: str | None = None,
    sheet_url: str | None = None,
    from_fulfillment: str | None = None,
    from_marketplace: str | None = None,
    to_fulfillment: str | None = None,
    to_marketplace: str | None = None,
    note: str | None = None,
) -> int:
    """Сохраняет операцию вместе с применёнными строками.
    items — список {"article", "barcode", "name", "quantity"}.
    Возвращает id операции, чтобы сослаться на неё из журнала действий."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO stock_operations
                (store_slug, kind, source_type, source_name, sheet_url,
                 from_fulfillment, from_marketplace, to_fulfillment, to_marketplace,
                 note, user_id, user_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (store_slug, kind, source_type, source_name, sheet_url,
             from_fulfillment, from_marketplace, to_fulfillment, to_marketplace,
             note, user_id, user_name, created_at),
        )
        operation_id = cur.lastrowid
        conn.executemany(
            """
            INSERT INTO stock_operation_items (operation_id, article, barcode, name, quantity)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (operation_id, i.get("article", ""), i.get("barcode"), i.get("name"),
                 int(i.get("quantity") or 0))
                for i in items
            ],
        )
        conn.commit()
        return operation_id
    finally:
        conn.close()


def get_operation(operation_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM stock_operations WHERE id = ?", (operation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_store_operations(store_slug: str, kinds: tuple[str, ...] | None = None,
                         limit: int = 500) -> list[dict]:
    """История движений стока по магазину — с числом позиций и штук.

    Считаем сразу в запросе, а не перебором в питоне: строк у операции бывает
    несколько сотен, и вытаскивать их все ради двух чисел незачем.
    """
    conn = get_connection()

    sql = """
        SELECT o.*,
               (SELECT COUNT(*) FROM stock_operation_items i
                 WHERE i.operation_id = o.id) AS positions,
               (SELECT COALESCE(SUM(i.quantity), 0) FROM stock_operation_items i
                 WHERE i.operation_id = o.id) AS units
        FROM stock_operations o
        WHERE o.store_slug = ?
    """
    params: list = [store_slug]

    if kinds:
        sql += " AND o.kind IN (%s)" % ",".join("?" for _ in kinds)
        params.extend(kinds)

    sql += " ORDER BY o.id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_operations_with_items(store_slug: str, kinds: tuple[str, ...] | None = None,
                              limit: int = 500) -> list[dict]:
    """То же самое, но сразу со строками — для выгрузки всей истории в xlsx.

    Строки берём одним запросом на всю выборку и раскладываем по операциям:
    отдельный запрос на каждую операцию — это сотни обращений к базе там,
    где хватает двух.
    """
    operations = get_store_operations(store_slug, kinds, limit)
    if not operations:
        return []

    ids = [op["id"] for op in operations]
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM stock_operation_items WHERE operation_id IN (%s) ORDER BY id"
        % ",".join("?" for _ in ids),
        ids,
    ).fetchall()
    conn.close()

    by_operation: dict[int, list[dict]] = {}
    for row in rows:
        by_operation.setdefault(row["operation_id"], []).append(dict(row))

    for op in operations:
        op["items"] = by_operation.get(op["id"], [])
    return operations


def get_operation_items(operation_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT article, barcode, name, quantity FROM stock_operation_items "
        "WHERE operation_id = ? ORDER BY id",
        (operation_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def log_action_for_operation(
    user_id: int | None, user_name: str, action: str, details: str,
    created_at: str, operation_id: int | None = None,
) -> None:
    """То же, что log_action, но с ссылкой на операцию — по ней в админке
    появляется кнопка скачивания файла."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (user_id, user_name, action, details, created_at, operation_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, user_name, action, details, created_at, operation_id),
    )
    conn.commit()
    conn.close()


def get_ff_warehouse_details_by_mp(store_slug: str, marketplace: str) -> list[dict]:
    """Детализация остатков на наших фулфилментах для одного маркетплейса —
    в той же форме, что и склады маркетплейса."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT si.article, si.barcode, si.name,
               ff.fulfillment AS warehouse,
               SUM(ff.quantity) AS quantity
        FROM stock_items si
        JOIN ff_stock ff
            ON ff.store_slug = si.store_slug AND ff.article = si.article
        WHERE si.store_slug = ? AND si.marketplace = ? AND si.is_service = 0
          AND ff.marketplace = ?
        GROUP BY si.id, ff.fulfillment
        ORDER BY si.id, ff.fulfillment
        """,
        (store_slug, marketplace, marketplace),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
