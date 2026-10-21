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

## Async order connections

Ozon orders use the same `CHECKSTOCK_DATABASE_URL` as the synchronous application.
Both `postgresql://` and `postgresql+psycopg://` URLs are accepted. The async connection
builder normalizes the driver prefix and passes the DSN to `asyncpg` for both the
regular engine and the separate job-lock engine.

`connect_timeout` is parsed as a single integer and passed as the async connection
timeout in seconds. Zero and negative values disable the timeout; an absent value
keeps the driver default. Empty, non-integer and repeated values are configuration
errors. `application_name` is passed through `server_settings` and must occur at most
once. Error messages and the connection-options representation do not include the DSN.

SSL options, including `sslmode`, `sslrootcert`, `sslcert` and `sslkey`, remain in the
DSN and are interpreted by `asyncpg`. The application does not downgrade the requested
SSL mode or replace certificate settings. URL-encode parameter values and credentials
as usual. SQLite configuration and the synchronous PostgreSQL connection are unchanged.
