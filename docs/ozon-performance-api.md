# Реклама Ozon в юнит-экономике

Для расходов, показов и кликов нужен отдельный доступ **Performance API** каждого кабинета. Ключ Seller API для рекламы не подходит. Создайте сервисный аккаунт и ключ на странице [Ozon → Настройки → API-ключи → Performance API](https://seller.ozon.ru/app/settings/api-keys?currentTab=performanceApi).

В `secrets/ozon_tokens.json` у каждого кабинета добавьте два поля:

```json
{
  "rimili": {
    "client_id": "seller-client-id",
    "api_key": "seller-api-key",
    "performance_client_id": "service-account@advertising.performance.ozon.ru",
    "performance_client_secret": "performance-secret"
  }
}
```

Секреты не добавляйте в Git. После настройки автоматическая выгрузка запускается при старте приложения и ежедневно в 04:00 МСК. В таблице Ozon расходы складываются по SKU и артикулу; CTR = клики / показы, CPC = расходы на оплату за клик / клики, ДРР = все расходы / ТО после отмен. Без отчёта реклама показывается как отсутствующие данные, а не как ноль.
