# Документация CheckStock

Начните с [LLM wiki](llm-wiki.md), если нужно быстро разобраться в проекте или дать контекст ИИ-ассистенту. Она указывает на исходный код и подробные документы, но не заменяет проверку реализации перед изменением поведения.

## Устройство проекта и эксплуатация

- [Архитектура и структура](architecture.md)
- [Ручные и фоновые загрузки](manual-sync.md)
- [Миграция SQLite в PostgreSQL](postgresql-migration.md)

## Остатки, поставки и Google Таблицы

- [Атомарность складских операций и повторы запросов](f03-stock-transactions.md)
- [Поставки на склады маркетплейсов](inbound-supplies.md)
- [Табло поставок](supply-arrivals.md)
- [Общая Google-выгрузка проектов](project-sheet-export.md)
- [Проверка периода заказов перед FBS-выгрузкой](f09-fbs-export.md)
- [Автообновление недели в Google Таблице](google-week-update.md)

## Экономика и цены

- [Финансовый отчёт Яндекс Маркета: работа, проверки и эксплуатация](yandex-finance.md)
- [Техническое задание на финансовый отчёт Яндекс Маркета](yandex-finance-spec.md)
- [Полнота расчёта экономики WB и Яндекс Маркета](f05-economics-completeness.md)
- [Дневной календарь параметров](daily-economics-calendar.md)
- [Юнит-экономика Яндекс Маркета](unit-economics-yandex.md)
- [Настройки калькулятора Яндекс Маркета](yandex-calculator-settings.md)
- [Категории и комиссии Яндекс Маркета](yandex-category-commissions.md)
- [Доступы к кабинетам Яндекс Маркета](yandex-access.md)
- [Цены витрины Яндекс Маркета](yandex-storefront-prices.md)
- [Цены WB Кошелька](wb-wallet-prices.md)
- [Реклама Ozon](ozon-performance-api.md)

## API для ИИ-агентов

- [API аналитики CheckStock](chatgpt-analytics.md)
- [Подключение Claude через MCP](claude-mcp.md)
- [Развёртывание API агентов](agent-api-deployment.md)
- [Сводка прибыли](agent-profit-summary.md)
- [Серверный анализ товаров](agent-server-analysis.md)
- [Расширенные отчёты WB](agent-extended-reports.md)
- [Отчёты Яндекс Маркета](agent-yandex-reports.md)
- [Примеры запросов менеджера](agent-manager-queries.md)

Документы с версиями формул и описаниями выпусков содержат историю изменений. Текущий контракт всегда сверяйте с кодом, тестами и, для HTTP API, с OpenAPI работающего приложения.
