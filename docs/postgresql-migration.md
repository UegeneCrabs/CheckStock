# SQLite to PostgreSQL migration

Production runs PostgreSQL 17 in the `db` service from `deploy/docker-compose.yml`. The database port is
not published on the host. PostgreSQL data is stored in the persistent `postgres-data` volume.

Set a long hexadecimal `POSTGRES_PASSWORD` in `.env`, start only PostgreSQL, and wait until it is
healthy:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d db
docker compose --env-file .env -f deploy/docker-compose.yml ps
```

Keep the application stopped and place a verified SQLite backup in a temporary host directory.
The migration target must be empty. Run the importer through the application image:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml build app
docker compose --env-file .env -f deploy/docker-compose.yml run --rm \
  -v /opt/checkstock-migration:/migration:ro \
  app python -m scripts.imports.migrate_sqlite_to_postgres /migration/checkstock.db
```

The importer checks SQLite integrity and foreign keys before copying, imports all mapped tables in
dependency order, resets PostgreSQL sequences, and compares the source and target row counts inside
one transaction. Any failure rolls the PostgreSQL import back.

After a successful import, initialize the application with background work disabled, then start it:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml run --rm -e CHECKSTOCK_DISABLE_BACKGROUND_SYNC=1 app \
  python -c "from app.jobs.background import _initialize_application; _initialize_application()"
docker compose --env-file .env -f deploy/docker-compose.yml up -d app
```

Do not delete the source SQLite database or the old server until the new application passes its
readiness and functional checks.
