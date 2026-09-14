# Ручные и служебные команды

Запускайте из корня проекта: `python -m scripts.<папка>.<имя>`.

| Папка | Команды |
|---|---|
| `ops` | Ключи API, создание пользователей и Docker watchdog. |
| `sync` | Синхронизация цен, исходных данных и показателей; догрузка заказов. |
| `imports` | Импорт остатков ФФ, исходных данных ЯМ и миграция SQLite в PostgreSQL. |
| `exports` | Отчёт без движения и выгрузка заказов FBS за период. |
| `diagnostics` | Проверка подключений, складов, каталогов и исходных таблиц. |
| `parsers` | Браузерный сборщик цен ЯМ; также вызывается приложением. |

```shell
python -m scripts.sync.sync_unit_economics_1c_source_data
python -m scripts.sync.sync_unit_economics_yandex --store rimili --source orders
python -m scripts.diagnostics.check_inbound_access
python -m scripts.ops.agent_key --help
python -m scripts.imports.migrate_sqlite_to_postgres --help
python -m scripts.parsers.parse_yandex_storefront_prices --help
```

Синхронизация и импорт записывают данные в выбранную конфигурацией БД. Watchdog запускается сервисом Docker. Несовместимые с текущей схемой/каталогом скрипты и отдельный запуск прототипа удалены.
