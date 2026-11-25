# Миграции удалённых отчётов и журналов

`app/repositories/schema.py:init_db()` автоматически выполняет миграции до создания
текущей ORM-схемы. Они поддерживают SQLite и PostgreSQL, допускают повторный запуск
и отсутствие части старых таблиц. Отдельная команда миграции не требуется.

`app/repositories/retired_reports_migration.py` удаляет следующие таблицы:

- `finance_yandex_operations`
- `finance_yandex_heads`
- `finance_yandex_attempts`
- `finance_yandex_snapshots`
- `finance_yandex_connections`
- `finance_yandex_report_tickets`
- `finance_yandex_unallocated`
- `finance_yandex_costs`
- `stock_operation_report_flags`
- `economics_daily_events`

Этот список задан явно в `RETIRED_REPORT_STORAGE` в `app/core/constants.py`.
Удаление выполняется одной транзакцией: сначала дочерние таблицы, затем родительские.
Миграция не использует `CASCADE`. Если другая таблица ссылается на удаляемую,
миграция завершится ошибкой без удаления перечисленных таблиц.

`app/repositories/analyzer_notes_migration.py` переименовывает `rnp_action_log`
в `analyzer_daily_notes`, сохраняя записи и их ID. Из последней правки переносит
время и автора, затем удаляет `rnp_action_revisions`. Сам текст заметки уже хранится
в основной записи. Эта миграция также выполняется в своей транзакции.

`economics_daily_events` — прежний журнал изменений дневной экономики. Снимки,
последние значения и сохранённые корректировки остаются в `economics_daily`.

Всего удаляется одиннадцать таблиц и переименовывается одна. Модели удалённых таблиц
отключены, поэтому создание текущей схемы их не восстанавливает. Таблицы каталога,
штрихкодов, характеристик товаров и действующей юнит-экономики эти две миграции
не меняют.

Поведение интерфейса описано в документах о [финансовом отчёте Яндекса](yandex-finance.md),
[заметках анализатора](wb-analyzer.md#дневные-заметки) и [складских операциях](f03-stock-transactions.md).
