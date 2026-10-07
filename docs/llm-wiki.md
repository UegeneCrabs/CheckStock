# LLM wiki: карта проекта CheckStock

Этот файл — точка входа для ИИ-ассистента и разработчика. Он описывает, где искать ответ, а не копирует весь код. Состояние сверено с деревом проекта на 7 октября 2026 года. При расхождении приоритет у текущего кода, схемы БД, тестов и конфигурации запуска; документацию затем нужно обновить.

## Что делает приложение

CheckStock — FastAPI-приложение для каталогов, остатков, поставок, продаж и юнит-экономики Wildberries, Ozon и Яндекс Маркета. В нём есть веб-страницы, HTTP API, API аналитики для ИИ-агентов, фоновые загрузки и выгрузки в Google Таблицы/FTP. [Обзор архитектуры](architecture.md) описывает границы слоёв.

## С чего начинать поиск

| Задача | Сначала открыть |
|---|---|
| Сборка приложения и список подключённых роутеров | `app/main.py`, `app/web/routers/__init__.py` |
| URL, права и обработка запроса | `app/web/routers/`, `app/web/middleware.py`, `app/web/dependencies.py` |
| Прикладной сценарий складского изменения | `app/application/`, `app/infrastructure/stock_repository.py`, [контракт транзакций](f03-stock-transactions.md) |
| SQL, снимки и старые read-модели | `app/repositories/`, `app/db.py` |
| Схема и подключение БД | `app/repositories/schema.py`, `app/infrastructure/database.py`, `app/config.py` |
| Загрузка маркетплейса | `app/wb/`, `app/ozon/`, `app/yandex/`, `app/jobs/` |
| Юнит-экономика и цены | `app/economics/`, `app/yandex/economics*.py`, `app/web/routers/unit_economics.py` |
| Финансовый отчёт Яндекс Маркета | `app/application/finance.py`, `app/finance/`, `app/infrastructure/finance_*`, [финансовый учёт](yandex-finance.md), [ТЗ](yandex-finance-spec.md) |
| Веб-интерфейс | `templates/`, `static/`, соответствующий файл в `app/web/routers/` |
| Выгрузки и Google-интеграция | `app/exports/`, `app/integrations/`, [формат выгрузки](project-sheet-export.md) |
| API ИИ-агентов | `app/agents/`, `app/web/routers/agent_*.py`, [контракт API](chatgpt-analytics.md) |
| Команды обслуживания | `scripts/`, [список команд](../scripts/README.md) |
| Проверки поведения | `tests/` — Python `unittest` и Node `node --test` |

## Поток запроса и данных

`app.main:create_app` создаёт приложение и `ApplicationContainer`, подключает middleware и роутеры. Для части новых сценариев маршрут вызывает сервис из `app/application`, а тот работает через порт и SQLAlchemy Unit of Work в `app/infrastructure`. Часть аналитических чтений и загрузчиков по-прежнему использует `app/repositories` и фасад `app/db.py`; поэтому не следует предполагать, что весь код проходит через один слой. Инициализация БД и запуск фоновых задач находятся в `app/jobs/background.py`.

Складские HTTP-изменения требуют `Idempotency-Key`; повтор с тем же ключом не должен повторять начисление. Подробности и границы транзакции — в [F03](f03-stock-transactions.md). Для экономических отчётов различайте отсутствие данных и подтверждённый ноль; источники и полнота описаны в [F05](f05-economics-completeness.md).

## Где живёт состояние

- Локально по умолчанию используется SQLite `data/checkstock.db`; при `CHECKSTOCK_DATABASE_URL` — PostgreSQL. В `docker-compose.yml` PostgreSQL хранится в томе `postgres-data`.
- Настройки окружения и значения по умолчанию определены в `app/config.py`; образец — `.env.example`. Настройки отдельных фоновых задач дополнительно хранятся в БД через `app/jobs/settings.py`.
- Секреты берутся из `secrets/` или путей, заданных переменными окружения. Образцы файлов — в `secrets/example/`; реальные секреты и локальные данные не коммитятся.
- Шаблоны страниц находятся в `templates/`, клиентский код — в `static/`. Состояние браузерных сборщиков хранится в `data/` или Docker volumes.

## Запуск и проверка

Из корня проекта установите `requirements-dev.txt`, скопируйте `.env.example` в `.env`, добавьте необходимые файлы в `secrets/` и запустите `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log`. Для Docker используйте `docker compose up --build -d`; Compose требует `POSTGRES_PASSWORD` в `.env`. Для локальной диагностики доступны `/healthz` и `/readyz`.

Проверки: `python -m ruff format --check app scripts`, `python -m ruff check app scripts`, `python -m unittest discover -s tests -v` и `node --test` с конкретными файлами `tests/*.cjs`. Интеграции с внешними API, браузером, Google и FTP требуют отдельных доступов; прохождение локальных тестов не подтверждает их работу в production.

## Как обновлять wiki

1. Найдите текущую реализацию по таблице выше и проверьте связанные тесты, настройки и схемы.
2. Обновите подробный предметный документ, если изменились контракт, формула, источник данных или порядок запуска.
3. Обновите этот указатель и [индекс документов](README.md), если появились новые модули или инструкции.
4. Для HTTP API сверяйте описание с `/openapi.json` или специализированной схемой `/api/agent/v1/openapi.json` запущенного приложения.

Не помещайте в wiki ключи, содержимое `.env`, реальные данные клиентов и временные результаты выгрузок.
