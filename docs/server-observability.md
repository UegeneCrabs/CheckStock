# Grafana, Loki и Alloy на сервере CheckStock

Серверный стек добавляется к основному Docker Compose через
`deploy/docker-compose.observability.yml`. Он запускает Grafana, Loki и Alloy
в той же сети, что `app` и `scheduler`. FastAPI и планировщик пишут логи в
общий именованный том `checkstock-logs`; Alloy читает этот том без доступа к
Docker socket. Локальный стенд `deploy/observability/docker-compose.yml` остаётся
отдельным проектом.

## Запуск

На Linux-сервере перейдите в корень актуального checkout CheckStock. В `.env`
задайте `POSTGRES_PASSWORD` и новый длинный `GRAFANA_ADMIN_PASSWORD`. Настройки
приложения и каталог `secrets/` подготовьте как для обычного серверного запуска.
Если порт 3000 на сервере занят, задайте `GRAFANA_PORT` в `.env`.

```bash
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml config --quiet
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml up -d --build
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml ps
```

Команда запускает PostgreSQL, `app`, `scheduler`, `watchdog`, Loki, Alloy и
Grafana. Grafana доступна только на `127.0.0.1` сервера; Loki не публикует порт.
Для доступа со своего компьютера откройте SSH-туннель, затем перейдите на
`http://127.0.0.1:3300`:

```bash
ssh -L 3300:127.0.0.1:3000 user@server
```

Если изменён `GRAFANA_PORT`, замените правый порт в команде туннеля. Логин
Grafana — `admin`, пароль — `GRAFANA_ADMIN_PASSWORD` из `.env`. После первого
создания тома Grafana изменение этой переменной не меняет сохранённый пароль.

## Проверка

```bash
curl -fsS http://127.0.0.1:3000/api/health
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml exec -T grafana curl -fsS http://loki:3100/ready
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml logs --tail=50 alloy grafana
```

Если изменён `GRAFANA_PORT`, замените порт в первой проверке. После появления
логов откройте в Grafana дашборды CheckStock. `app` пишет в `app.log`, а
`scheduler` — в `scheduler.log`; рядом создаются `app.tags.log` и
`scheduler.tags.log` для выпадающего списка тегов. Новые данные появляются
после событий в приложении или работы фоновой задачи.
Страница HTTP-запросов показывает 100 ручек с наибольшим средним временем
среди ручек с запросами в выбранном периоде. Список содержит HTTP-метод и маршрут; график
показывает среднее время по интервалам. Новые записи группируются по
HTTP-методу и шаблону маршрута FastAPI (`route`), старые — по фактическому
`path`. Ручки без запросов в периоде не отображаются.
При уровне INFO записываются и быстрые успешные GET-запросы; проверки
`/healthz`, `/readyz` и файлы `/static/` в этот журнал не входят.

Выпуск через `deploy/deploy-release.sh` использует оба Compose-файла и обновляет
сервисы наблюдаемости вместе с приложением. До отправки релизного тега задайте
`GRAFANA_ADMIN_PASSWORD` в серверном `.env`; без него выпуск остановится при
проверке конфигурации и вернёт прежний коммит. Скрипт проверяет здоровье
Grafana и запуск Loki и Alloy. Общий том логов сохраняется при пересоздании
`app` и `scheduler`. При выпуске `grafana`, `loki` и `alloy` пересоздаются для
обновления смонтированных файлов конфигурации; сценарий сравнивает JSON
HTTP-дашбордов внутри Grafana с файлами релизного коммита.

Чтобы вручную перечитать JSON из текущего серверного checkout, выполните в
`/home/checkstock`:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml up -d --no-deps --force-recreate grafana
sha256sum deploy/observability/checkstock-http-logs.json
docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml exec -T grafana sha256sum /var/lib/grafana/dashboards/checkstock-http-logs.json
```

Хеши должны совпасть. Эта операция перечитывает файл из текущего checkout; она
не меняет сам запрос. Текущий запрос откройте в панели Grafana через **Edit →
Queries**. Если он начинается с `topk(100, avg_over_time(`, JSON новой версии
уже загружен и ошибку нужно исправлять в LogQL или лимитах Loki.

Loki хранит записи семь дней в томе `loki-data`. Тома Grafana и Alloy также
сохраняются между перезапусками. Исходные файлы в `checkstock-logs` Loki не
очищает: следите за размером тома и настройте ротацию файлов до длительной
эксплуатации. Не выполняйте `down --volumes`, если данные нужно сохранить.
Лимит Loki `max_query_series` равен 5000: панель времени по HTTP-ручкам
создаёт промежуточные ряды до отбора `topk(100)`. Больший лимит требует больше
памяти при широких запросах. При новой ошибке лимита сначала сузьте период
или фильтры и проверьте количество различных маршрутов в логах.
