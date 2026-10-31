#!/usr/bin/env bash
set -euo pipefail

tag="${1:?Usage: deploy-release.sh TAG COMMIT_SHA}"
expected_sha="${2:?Usage: deploy-release.sh TAG COMMIT_SHA}"
root=/home/checkstock
backup_dir=/home/bd_backup

[[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || { echo 'Invalid tag' >&2; exit 1; }
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid SHA' >&2; exit 1; }
cd "$root" || { echo "Application checkout $root is unavailable" >&2; exit 1; }
[[ -f .env ]] || { echo 'Missing .env' >&2; exit 1; }
[[ -d secrets ]] || { echo 'Missing secrets directory' >&2; exit 1; }
[[ -d "$backup_dir" && -w "$backup_dir" ]] || { echo 'Backup directory is unavailable' >&2; exit 1; }
old_compose_file=deploy/docker-compose.yml
[[ -f "$old_compose_file" ]] || old_compose_file=docker-compose.yml
[[ -f "$old_compose_file" ]] || { echo 'Missing current Docker Compose file' >&2; exit 1; }
old_compose=(docker compose --env-file .env -f "$old_compose_file")
old_observability=false
if [[ -f deploy/docker-compose.observability.yml ]] && grep -q '^GRAFANA_ADMIN_PASSWORD=.' .env; then
  old_compose+=(-f deploy/docker-compose.observability.yml)
  old_observability=true
fi
compose=("${old_compose[@]}")
command -v docker >/dev/null || { echo 'Docker is not installed on the server' >&2; exit 1; }
docker compose version >/dev/null || { echo 'Docker Compose is unavailable on the server' >&2; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || {
  echo 'Tracked server files have local changes' >&2; exit 1;
}

old_sha="$(git rev-parse HEAD)"
GIT_TERMINAL_PROMPT=0 git fetch origin "refs/tags/$tag:refs/tags/$tag" </dev/null
actual_sha="$(git rev-parse "refs/tags/$tag^{commit}")"
[[ "$actual_sha" == "$expected_sha" ]] || { echo 'Tag SHA mismatch' >&2; exit 1; }
deployed_tags=()
while IFS= read -r candidate; do
  [[ "$candidate" == "$tag" ]] && continue
  [[ "$candidate" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || continue
  deployed_tags+=("$candidate")
done < <(git tag --points-at "$old_sha")
if ((${#deployed_tags[@]})); then
  deployed_tag="$(printf '%s\n' "${deployed_tags[@]}" | sort -V | tail -n 1)"
  [[ "$(printf '%s\n%s\n' "$deployed_tag" "$tag" | sort -V | tail -n 1)" == "$tag" && "$tag" != "$deployed_tag" ]] || {
    echo "Release tag $tag must exceed deployed tag $deployed_tag" >&2; exit 1;
  }
fi
echo "Checking current Compose configuration: $old_compose_file"
"${compose[@]}" config --quiet </dev/null
db_id="$("${compose[@]}" ps -q db)"
[[ -n "$db_id" && "$(docker inspect --format '{{.State.Health.Status}}' "$db_id")" == healthy ]] || {
  echo 'Database is not healthy' >&2; exit 1;
}

stamp="$(date -u +%Y%m%d-%H%M%S)"
backup="$backup_dir/checkstock-$stamp-$tag.dump"
temporary="$(mktemp "$backup_dir/.checkstock-$stamp-$tag.XXXXXX.tmp")"
trap 'rm -f -- "$temporary"' EXIT
echo "Creating database backup $backup"
"${compose[@]}" exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' </dev/null > "$temporary" || {
  echo 'Database backup failed while running pg_dump; application code was not changed' >&2
  exit 1
}
[[ -s "$temporary" ]] || { echo 'Database backup is empty' >&2; exit 1; }
"${compose[@]}" exec -T db pg_restore -l < "$temporary" > /dev/null || {
  echo 'Database backup verification failed; application code was not changed' >&2
  exit 1
}
[[ ! -e "$backup" ]] || { echo 'Backup filename already exists' >&2; exit 1; }
chmod 600 "$temporary"
mv -- "$temporary" "$backup"
echo "Verified database backup: $backup"

show_service_failure() {
  local service="$1" reason="$2" id
  echo "Service $service failed deployment check: $reason" >&2
  id="$("${compose[@]}" ps -a -q "$service")"
  if [[ -z "$id" ]]; then
    echo "No container found for $service" >&2
    return
  fi
  docker inspect --format 'Container {{.Name}}: status={{.State.Status}} running={{.State.Running}} exit_code={{.State.ExitCode}}{{if .State.Health}} health={{.State.Health.Status}}{{end}}' "$id" >&2 || true
  echo "Recent healthcheck results for $service:" >&2
  docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}exit_code={{.ExitCode}} output={{.Output}}{{end}}{{else}}No healthcheck configured{{end}}' "$id" >&2 || true
  echo "Recent container logs for $service:" >&2
  docker logs --tail 40 "$id" >&2 || true
}

wait_healthy() {
  local service="$1" attempts="$2" id state i
  echo "Waiting for $service to become healthy" >&2
  id="$("${compose[@]}" ps -a -q "$service")"
  if [[ -z "$id" ]]; then
    show_service_failure "$service" 'container not found'
    return 1
  fi
  for ((i=0; i<attempts; i++)); do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")"
    if [[ "$state" == healthy ]]; then
      echo "Service $service is healthy" >&2
      return 0
    fi
    if [[ "$state" == unhealthy || "$state" == none ]]; then
      show_service_failure "$service" "health status is $state"
      return 1
    fi
    sleep 5
  done
  show_service_failure "$service" "healthcheck did not pass within $((attempts * 5)) seconds"
  return 1
}

watchdog_running() {
  local id
  id="$("${compose[@]}" ps -a -q watchdog)"
  if [[ -n "$id" && "$(docker inspect --format '{{.State.Running}}' "$id")" == true ]]; then
    echo 'Service watchdog is running' >&2
    return 0
  fi
  show_service_failure watchdog 'container is not running'
  return 1
}

service_running() {
  local service="$1" id
  id="$("${compose[@]}" ps -a -q "$service")"
  if [[ -n "$id" && "$(docker inspect --format '{{.State.Running}}' "$id")" == true ]]; then
    echo "Service $service is running" >&2
    return 0
  fi
  show_service_failure "$service" 'container is not running'
  return 1
}

rollback() {
  local status=$? rollback_failed=0
  trap - ERR
  echo "Deployment failed during $deploy_stage (exit status $status); restoring application commit $old_sha" >&2
  if [[ "$old_observability" == false ]]; then
    "${compose[@]}" stop grafana alloy loki </dev/null || true
  fi
  git checkout --detach "$old_sha" || rollback_failed=1
  if ((rollback_failed == 0)); then
    compose=("${old_compose[@]}")
    "${compose[@]}" config --quiet </dev/null || rollback_failed=1
  fi
  if ((rollback_failed == 0)); then
    "${compose[@]}" build app </dev/null || rollback_failed=1
  fi
  if ((rollback_failed == 0)); then
    if [[ "$old_observability" == true ]]; then
      "${compose[@]}" up -d --no-deps app scheduler watchdog loki grafana alloy </dev/null || rollback_failed=1
    else
      "${compose[@]}" up -d --no-deps app scheduler watchdog </dev/null || rollback_failed=1
    fi
  fi
  if ((rollback_failed == 0)); then
    wait_healthy app 24 || rollback_failed=1
    wait_healthy scheduler 30 || rollback_failed=1
    watchdog_running || rollback_failed=1
    if [[ "$old_observability" == true ]]; then
      service_running loki || rollback_failed=1
      service_running alloy || rollback_failed=1
      wait_healthy grafana 24 || rollback_failed=1
    fi
  fi
  if ((rollback_failed)); then
    echo "Rollback did not restore healthy services; inspect the server immediately" >&2
  else
    echo "Previous application commit restored and services are healthy" >&2
  fi
  exit "$status"
}
deploy_stage='checkout of release commit'
trap rollback ERR

echo "Switching checkout to release $tag ($actual_sha)"
git checkout --detach "$actual_sha"
compose=(docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.observability.yml)
deploy_stage='validation of release Compose configuration'
echo 'Checking release Compose configuration'
"${compose[@]}" config --quiet </dev/null
deploy_stage='release image build'
echo 'Building release image on server'
"${compose[@]}" build app </dev/null
deploy_stage='container update'
echo 'Updating application and observability containers'
"${compose[@]}" up -d --no-deps app scheduler watchdog loki grafana alloy </dev/null

deploy_stage='app healthcheck'
wait_healthy app 24
deploy_stage='scheduler healthcheck'
wait_healthy scheduler 30
deploy_stage='watchdog running check'
watchdog_running
deploy_stage='observability service checks'
service_running loki
service_running alloy
wait_healthy grafana 24
trap - ERR

# Only completed CheckStock dumps are eligible for retention cleanup.
mapfile -t backups < <(find "$backup_dir" -maxdepth 1 -type f \
  -regextype posix-extended \
  -regex '.*/checkstock-[0-9]{8}-[0-9]{6}-v[0-9]+\.[0-9]+\.[0-9]+\.dump' \
  -printf '%f\n' | sort -r)
if ((${#backups[@]} > 3)); then
  for old_backup in "${backups[@]:3}"; do
    rm -- "$backup_dir/$old_backup" || echo "Could not remove old backup $old_backup" >&2
  done
fi
echo "Deployed $tag ($actual_sha); backup: $backup"
