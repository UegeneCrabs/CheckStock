# Ручные и служебные команды

Запускайте из корня проекта: `python -m scripts.<папка>.<имя>`.

| Папка | Команды |
|---|---|
| `ops` | Ключи API, создание пользователей и Docker watchdog. |
| `sync` | Синхронизация цен, исходных данных и показателей; догрузка заказов. |
| `imports` | Импорт остатков ФФ, исходных данных ЯМ и миграция SQLite в PostgreSQL. |
| `exports` | Отчёт без движения и выгрузка заказов FBS за период. |
| `diagnostics` | Проверка подключений, складов, каталогов и исходных таблиц. |
| `parsers` | Браузерный сборщик цен ЯМ и подготовка отдельного анонимного сеанса WB. |

```shell
python -m scripts.sync.sync_unit_economics_1c_source_data
python -m scripts.sync.sync_unit_economics_yandex --store rimili --source orders
python -m scripts.diagnostics.check_inbound_access
python -m scripts.ops.agent_key --help
python -m scripts.imports.migrate_sqlite_to_postgres --help
python -m scripts.parsers.parse_yandex_storefront_prices --help
python -m scripts.sync.sync_unit_economics_1c_prices
python -m scripts.diagnostics.check_wb_storefront 153985484
```

Синхронизация и импорт записывают данные в выбранную конфигурацией БД. Watchdog запускается сервисом Docker. Несовместимые с текущей схемой/каталогом скрипты и отдельный запуск прототипа удалены.

Полная выгрузка цен WB по умолчанию включает все кабинеты и сохраняет цену
продавца, цену с СПП и цену с WB Кошельком. Устаревший сеанс обновляется
автоматически; локально нужны Яндекс Браузер и Playwright на Windows.
`--prepare-session` можно использовать для принудительной подготовки.
Та же логика действует при ручном запуске из сайта и в плановых задачах.
Ограничения и диагностика описаны в `docs/wb-wallet-prices.md`.
