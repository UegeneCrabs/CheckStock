# Выпуск CheckStock на сервер

`master` — ветка разработки. `dev-release` указывает на подготовленную к выпуску
версию. Обновление `dev-release` проверяет рост `deploy/VERSION`, но не запускает деплой.
Деплой запускает только тег `vMAJOR.MINOR.PATCH` на коммите `dev-release`.

## Однократная настройка

1. Убедитесь, что работающий Compose-проект и Git checkout находятся в
   `/home/checkstock`, сервис `db` здоров, `.env` и `secrets/` находятся на сервере.
   Все команды Compose выполняются из корня репозитория с
   `--env-file .env -f deploy/docker-compose.yml`.
   Сценарий не создаёт новый сервер и не переносит данные.
2. Создайте `/home/bd_backup` с доступом на запись для пользователя деплоя.
   Выделите место как минимум для трёх полных дампов. Желательно дополнительно
   копировать их на другой сервер или диск.
3. Создайте отдельного SSH-пользователя для деплоя. Ему нужны права на Git checkout,
   Docker Compose и каталог бэкапов. Дайте серверу доступ на чтение GitHub-репозитория.
   Сохраните приватный ключ для доступа Actions к серверу только в GitHub Secrets.
4. В GitHub задайте Secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`,
   `DEPLOY_KNOWN_HOSTS` и, если SSH использует нестандартный порт, Variable
   `DEPLOY_PORT`. Значение `DEPLOY_KNOWN_HOSTS` получите после проверки отпечатка
   ключа сервера, например через `ssh-keyscan -H SERVER`; не отключайте проверку
   ключа. Разрешите Actions и ограничьте обновление `dev-release` и тегов `v*`.
5. Для первого выпуска убедитесь, что существующий checkout не имеет изменений
   отслеживаемых файлов. Если на нём нет `deploy/VERSION`, сценарий считает первый тег
   начальной версией. Для последующих выпусков требуется строго больший номер.

## Выпуск версии

Работайте в чистом локальном checkout. Замените `0.2.0` выбранным номером и
увеличивайте его относительно текущего `dev-release`:

```bash
git switch master
git pull --ff-only origin master
printf '0.2.0\n' > deploy/VERSION
git add deploy/VERSION
git commit -m 'Release 0.2.0'
git push origin master

git switch dev-release
git pull --ff-only origin dev-release
git merge --ff-only master
git push origin dev-release
```

Если `dev-release` ещё не создана, вместо `git switch dev-release` выполните
`git switch -c dev-release master`, затем `git push -u origin dev-release`.
Дождитесь успешной проверки **Check release version** для нового коммита.
При обычном выпуске `dev-release` должен продвигаться до выбранного коммита
`master` без дополнительных коммитов.

Только после успешной проверки создайте и отправьте тег:

```bash
git tag v0.2.0
git push origin v0.2.0
```

Дождитесь результата **Deploy CheckStock release** в Actions. Не повторяйте
старый тег для повторной установки: выпуск новой версии требует нового номера.
Если проверка обновления `dev-release` не прошла, тег создавать нельзя.

## Что делает деплой

Workflow проверяет формат и совпадение тега с `deploy/VERSION`, присутствие коммита в
`dev-release`, Ruff и сборку Docker-образа. Далее сценарий на сервере проверяет
чистоту checkout, текущую версию и здоровую БД. Он создаёт дамп PostgreSQL в
`/home/bd_backup/checkstock-YYYYMMDD-HHMMSS-vX.Y.Z.dump` (время UTC), проверяет
его через `pg_restore -l` и оставляет три последних успешных дампа CheckStock.
Временные и посторонние файлы не удаляются. Только после бэкапа он переключает
checkout на тег, собирает образ и обновляет `app`, `scheduler`, `watchdog`.
Сервис `db` и Docker volumes не удаляются. Успех требует здорового `app` (его
healthcheck обращается к `/readyz`), здорового `scheduler` и работающего
`watchdog`.

При неуспехе после переключения кода сценарий возвращает прежний коммит,
пересобирает прежний образ и запускает сервисы. Проверьте журнал Actions и
состояние сервера; если новая версия успела изменить схему БД, откат одного
кода может быть недостаточен.

## Проверка и восстановление БД

Список дампов: `ls -lh /home/bd_backup/checkstock-*.dump`. Проверка структуры:

```bash
cd /home/checkstock
docker compose --env-file .env -f deploy/docker-compose.yml exec -T db pg_restore -l \
  < /home/bd_backup/checkstock-YYYYMMDD-HHMMSS-vX.Y.Z.dump > /dev/null
```

Для восстановления сначала остановите `app`, `scheduler` и `watchdog`, чтобы
они не меняли данные. Сохраните отдельную копию текущей БД, даже если она
неработоспособна. Затем восстановите выбранный дамп:

```bash
cd /home/checkstock
docker compose --env-file .env -f deploy/docker-compose.yml stop app scheduler watchdog
docker compose --env-file .env -f deploy/docker-compose.yml exec -T db sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > /home/bd_backup/emergency-before-restore.dump
docker compose --env-file .env -f deploy/docker-compose.yml exec -T db sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_restore --clean --if-exists --create --exit-on-error -U "$POSTGRES_USER" -d postgres' \
  < /home/bd_backup/checkstock-YYYYMMDD-HHMMSS-vX.Y.Z.dump
```

Проверьте журнал восстановления, верните соответствующую версию кода и запустите
сервисы через `docker compose --env-file .env -f deploy/docker-compose.yml up -d app scheduler watchdog`. Восстановление
удаляет изменения данных после даты дампа, поэтому его выполняют вручную после
оценки последствий. Перед первым рабочим релизом проверьте эту процедуру на
отдельной тестовой БД.
