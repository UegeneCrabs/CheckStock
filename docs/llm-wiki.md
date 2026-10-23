# LLM wiki: карта проекта CheckStock

Этот файл — точка входа для ИИ-ассистента и разработчика. Он описывает, где искать ответ, а не копирует весь код. Состояние сверено с деревом проекта на 7 октября 2026 года. При расхождении приоритет у текущего кода, схемы БД, тестов и конфигурации запуска; документацию затем нужно обновить.

## Что делает приложение

CheckStock — FastAPI-приложение для каталогов, остатков, поставок, продаж и юнит-экономики Wildberries, Ozon и Яндекс Маркета. В нём есть веб-страницы, HTTP API, API аналитики для ИИ-агентов, фоновые загрузки и выгрузки в Google Таблицы/FTP. [Обзор архитектуры](architecture.md) описывает границы слоёв.

## С чего начинать поиск

| Задача | Сначала открыть |
|---|---|
| Правила разработки Ozon перед изменением кода | [Единый источник правил в скилле](../.agents/skills/checkstock-task-guide/SKILL.md#обязательные-правила-разработки-ozon) |
| Сборка приложения и список подключённых роутеров | `app/main.py`, `app/web/routers/__init__.py` |
| URL, права и обработка запроса | `app/web/routers/`, `app/web/middleware.py`, `app/web/dependencies.py` |
| Пользователи, сессии и удаление прежней матрицы доступа | `app/access/authentication.py`, `app/access/resources.py`, `app/repositories/identity_migration.py`, [контракт и миграция](authentication.md) |
| Прикладной сценарий складского изменения | `app/application/`, `app/infrastructure/stock_repository.py`, [контракт транзакций](f03-stock-transactions.md) |
| Расписание поставок и отметка «Поставлена на сток» | `app/application/supply_arrival_stock.py`, `app/infrastructure/supply_arrival_stock_repository.py`, `static/stock/supply-arrivals.js`, [хранение, ID и API](supply-arrivals.md#отметка-поставлена-на-сток) |
| SQL, снимки и старые read-модели | `app/repositories/`, `app/db.py` |
| Схема и подключение БД | `app/repositories/schema.py`, `app/infrastructure/database.py`, `app/config.py` |
| Загрузка маркетплейса | `app/wb/`, `app/ozon/`, `app/yandex/`, `app/jobs/` |
| Цены Ozon, комиссия за продажу FBO и дневная история | `app/economics/ozon_prices.py`, `app/ozon/async_prices.py`, `app/infrastructure/async_prices_repository.py`, `app/infrastructure/async_commissions_repository.py`, [контракт](ozon-prices.md) |
| Связанные цены калькулятора Ozon, СПП, кошелёк и комиссия | `app/economics/ozon_price_calculator.py`, `app/dto/ozon_price_calculator.py`, `static/economics/ozon/prices.js`, [формулы и источники себестоимости](ozon-prices.md#связь-цен-калькулятора) |
| Каталог задач, названия и подписи расписаний | `app/core/constants.py`, `app/jobs/catalog.py`, [метаданные](background-jobs.md#метаданные-каталога-задач) |
| Автоматическое расписание, повторы и восстановление | `app/jobs/worker.py`, `app/jobs/scheduling.py`, `app/repositories/job_schedules.py`, [контракт планировщика](background-jobs.md) |
| Локальный запуск сайта и загрузчика одной командой | `run.py`, `app/development.py`, `app/infrastructure/process_job.py`, [запуск и остановка](background-jobs.md#запуск) |
| Загрузка и нормализация заказов | `app/orders/sync.py`, `app/orders/normalizers/`, [устройство модуля](architecture.md#заказы-маркетплейсов), [контракт FBS](f09-fbs-export.md) |
| Скорость таблиц, графиков и отчётов WB и Яндекс Маркета | `app/yandex/economics.py`, `app/web/routers/unit_economics.py`, [область оптимизаций](economics-performance.md) |
| Юнит-экономика и цены | `app/economics/`, `app/yandex/economics*.py`, `app/web/routers/unit_economics.py` |
| Расходы без ИУ WB | `app/application/wb_standard_costs.py`, `app/infrastructure/wb_standard_costs_repository.py`, `app/economics/wb/catalogue.py`, `app/dto/wb_standard_costs.py`, `app/economics/wb/standard_sources.py`, `app/economics/wb/standard_costs.py`, `app/economics/wb/standard_sync.py`, `app/wb/standard_costs.py`, `app/wb/standard_costs_sources.py`, `app/economics/wb/standard_logistics.py`, `app/economics/wb/standard_storage.py`, `app/web/routers/wb_standard_costs.py`, [контракт](wb-standard-costs.md) |
| Коэффициент D по часам для каждого кабинета WB | `app/analytics/analyzer.py`, `app/dto/unit_economics_1c.py`, `static/economics/wb/cabinet-settings.js`, [значения, время и сохранение](wb-cabinet-settings.md) |
| Асинхронные заказы Ozon | `app/economics/ozon_turnover.py`, `app/orders/ozon_async.py`, `app/ozon/async_api.py`, `app/infrastructure/async_*`, `app/jobs/async_*`, [контракт](background-jobs.md#асинхронные-заказы-ozon) |
| Группы констант, датаклассы и Enum | `app/core/constants.py`: `OZON_ORDERS`, `OZON_API`, `JOBS` и общая инфраструктура; [правила использования](background-jobs.md#константы-и-перечисления-заказов-ozon) |
| Типы и проверка заказов Ozon, восстановление артикула по SKU | `app/dto/orders.py`, `app/dto/ozon_orders.py`, `app/infrastructure/async_catalog_repository.py`, `app/orders/normalizers/ozon_legacy.py`, [DTO и совместимость](background-jobs.md#контракты-данных-заказов-ozon) |
| Параметры асинхронного PostgreSQL | `app/dto/database.py`, `app/infrastructure/async_database.py`, [подключение и SSL](postgresql-migration.md#async-order-connections) |
| Частичные ТО и реклама Ozon | `app/infrastructure/async_turnover_repository.py`, `app/ozon/turnover_normalizer.py`, [полнота](f05-economics-completeness.md#то-и-рекламные-расходы-ozon) |
| Выкуп и ДРР Ozon, сегодняшние CPC/CPO | `app/ozon/drr.py`, `app/dto/ozon_economics.py`, `app/infrastructure/ozon_economics_repository.py`, [формулы и даты](ozon-buyout.md) |
| Почасовая реклама Ozon, пропуски, кэш отчётов и лимиты | `app/economics/ozon_advertising_breakdown.py`, `app/ozon/async_performance.py`, `app/infrastructure/async_advertising_repository.py`, [контракт и ограничения](ozon-performance-api.md) |
| Эфемериды и недельные комментарии WB | `app/analytics/ephemerides.py`, `app/repositories/ephemerides.py`, `app/jobs/wb_transit.py`, [контракт](ephemerides.md) |
| Финансовый отчёт Яндекс Маркета | `app/application/finance.py`, `app/finance/`, `app/infrastructure/finance_*`, [финансовый учёт](yandex-finance.md), [ТЗ](yandex-finance-spec.md) |
| Веб-интерфейс | `templates/`, `static/`, соответствующий файл в `app/web/routers/` |
| Интерфейс Ozon и общие страницы без Python-разметки | `app/web/client_page.py`, `app/dto/{integrations,ui,stock_pages,ozon_economics,google_export}.py`, `app/infrastructure/async_read_models.py`, `static/common/client-page.js`, [контракты и границы переноса](ozon-web-contracts.md) |
| Выгрузки и Google-интеграция | `app/exports/`, `app/integrations/`, [формат выгрузки](project-sheet-export.md) |
| Три независимых недельных блока WB/Ozon/ЯМ | `app/application/google_weeks.py`, `app/infrastructure/google_week_repository.py`, `app/integrations/google_week_{client,plans}.py`, `app/web/routers/google_weeks.py`, [контракт и перенос WB](google-week-update.md) |
| API ИИ-агентов | `app/agents/`, `app/web/routers/agent_*.py`, [контракт API](chatgpt-analytics.md) |
| Команды обслуживания | `scripts/`, [список команд](../scripts/README.md) |
| Статические проверки | `pyproject.toml`, цели `lint` и `check` в `Makefile` |

## Поток запроса и данных

`app.main:create_app` создаёт приложение и `ApplicationContainer`, подключает middleware и роутеры. Для части новых сценариев маршрут вызывает сервис из `app/application`, а тот работает через порт и SQLAlchemy Unit of Work в `app/infrastructure`. Часть аналитических чтений и загрузчиков по-прежнему использует `app/repositories` и фасад `app/db.py`; поэтому не следует предполагать, что весь код проходит через один слой. `app/jobs/background.py` содержит общую инициализацию и описания задач. Автоматические задачи запускает отдельный `app/jobs/worker.py`; вебовый `lifespan` их не запускает.

Складские HTTP-изменения требуют `Idempotency-Key`; повтор с тем же ключом не должен повторять начисление. Подробности и границы транзакции — в [F03](f03-stock-transactions.md). Для экономических отчётов различайте отсутствие данных и подтверждённый ноль; источники и полнота описаны в [F05](f05-economics-completeness.md).

## Где живёт состояние

- Локально по умолчанию используется SQLite `data/checkstock.db`; при `CHECKSTOCK_DATABASE_URL` — PostgreSQL. В `docker-compose.yml` PostgreSQL хранится в томе `postgres-data`.
- Настройки окружения и значения по умолчанию определены в `app/config.py`; образец — `.env.example`. Настройки отдельных фоновых задач дополнительно хранятся в БД через `app/jobs/settings.py`.
- Сроки и попытки автоматических загрузок хранятся в `sync_job_schedules`; история и отображаемые статусы — в `sync_job_runs` и `sync_job_states`. Они переживают перезапуск планировщика.
- Секреты берутся из `secrets/` или путей, заданных переменными окружения. Образцы файлов — в `secrets/example/`; реальные секреты и локальные данные не коммитятся.
- Шаблоны страниц находятся в `templates/`, клиентский код — в `static/`. Состояние браузерных сборщиков хранится в `data/` или Docker volumes.

## Запуск и проверка

Из корня проекта установите `requirements-dev.txt`, скопируйте `.env.example` в `.env`, добавьте необходимые файлы в `secrets/` и запустите `python run.py` с активированным окружением. Команда запускает сайт с reload и планировщик в одном терминале; `Ctrl+C` останавливает оба процесса. Для Docker используйте `docker compose up --build -d`; Compose запускает отдельный сервис `scheduler` и требует `POSTGRES_PASSWORD` в `.env`. Веб проверяется через `/healthz` и `/readyz`, планировщик — командой `python -m app.jobs.worker --healthcheck`.

Статические проверки: `python -m ruff format --check app scripts run.py` и `python -m ruff check app scripts run.py`. Регрессионные Python-тесты находятся в `tests/` (`python -m unittest discover -s tests -v`), клиентские — в `tests/*.cjs` (`node --test` с нужными файлами). Проверки поведения описаны в предметных документах; интеграции с внешними API, браузером, Google и FTP требуют отдельных доступов и проверки в соответствующем окружении.

После объединения локальной ветки и stash 19 октября 2026 года модули Ozon подключены через `app/jobs/ozon.py`, `app/web/ozon_jobs.py` и `app/web/routers/ozon_economics.py`. Страница использует асинхронный JSON API; четыре задачи обслуживают заказы, цены с комиссией, закрытие дней и рекламу. Общие складские страницы сохраняют прежние обработчики; [фактические подключения и совместимость](architecture.md#состояние-объединения-19-октября-2026).

`python -m mypy` проверяет типизированный контур асинхронных заказов Ozon;
список файлов и строгие настройки заданы в `pyproject.toml`. Проверка входит
в `make check`, зависимость — в `requirements-dev.txt`.

## Как обновлять wiki

1. Найдите текущую реализацию по таблице выше и проверьте связанные тесты, настройки и схемы.
2. Обновите подробный предметный документ, если изменились контракт, формула, источник данных или порядок запуска.
3. Обновите этот указатель и [индекс документов](README.md), если появились новые модули или инструкции.
4. Для HTTP API сверяйте описание с `/openapi.json` или специализированной схемой `/api/agent/v1/openapi.json` запущенного приложения.

Не помещайте в wiki ключи, содержимое `.env`, реальные данные клиентов и временные результаты выгрузок.
