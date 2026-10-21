#!/usr/bin/env bash
set -euo pipefail

tag="${1:?Usage: deploy-release.sh TAG COMMIT_SHA}"
expected_sha="${2:?Usage: deploy-release.sh TAG COMMIT_SHA}"
root=/home/checkstock
backup_dir=/home/bd_backup
compose=(docker compose --env-file .env -f deploy/docker-compose.yml)

[[ "$tag" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || { echo 'Invalid tag' >&2; exit 1; }
[[ "$expected_sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid SHA' >&2; exit 1; }
cd "$root"
[[ -f .env ]] || { echo 'Missing .env' >&2; exit 1; }
[[ -d secrets ]] || { echo 'Missing secrets directory' >&2; exit 1; }
[[ -d "$backup_dir" && -w "$backup_dir" ]] || { echo 'Backup directory is unavailable' >&2; exit 1; }
command -v docker >/dev/null
docker compose version >/dev/null
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || {
  echo 'Tracked server files have local changes' >&2; exit 1;
}

old_sha="$(git rev-parse HEAD)"
old_version=''
if [[ -f deploy/VERSION ]]; then
  old_version="$(tr -d '\r' < deploy/VERSION)"
fi
GIT_TERMINAL_PROMPT=0 git fetch origin "refs/tags/$tag:refs/tags/$tag" </dev/null
actual_sha="$(git rev-parse "refs/tags/$tag^{commit}")"
[[ "$actual_sha" == "$expected_sha" ]] || { echo 'Tag SHA mismatch' >&2; exit 1; }
new_version="$(git show "$actual_sha:deploy/VERSION" | tr -d '\r')"
[[ "$new_version" == "${tag#v}" ]] || { echo 'Tag and deploy/VERSION mismatch' >&2; exit 1; }
if [[ -n "$old_version" ]]; then
  [[ "$old_version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || {
    echo 'Invalid deployed VERSION' >&2; exit 1;
  }
  [[ "$(printf '%s\n%s\n' "$old_version" "$new_version" | sort -V | head -n 1)" == "$old_version" && "$new_version" != "$old_version" ]] || {
    echo "New version $new_version must exceed deployed version $old_version" >&2; exit 1;
  }
fi
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
"${compose[@]}" exec -T db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' </dev/null > "$temporary"
[[ -s "$temporary" ]] || { echo 'Database backup is empty' >&2; exit 1; }
"${compose[@]}" exec -T db pg_restore -l < "$temporary" > /dev/null
[[ ! -e "$backup" ]] || { echo 'Backup filename already exists' >&2; exit 1; }
chmod 600 "$temporary"
mv -- "$temporary" "$backup"
echo "Verified database backup: $backup"

# Only completed CheckStock dumps are eligible for retention cleanup.
mapfile -t backups < <(find "$backup_dir" -maxdepth 1 -type f \
  -regextype posix-extended \
  -regex '.*/checkstock-[0-9]{8}-[0-9]{6}-v[0-9]+\.[0-9]+\.[0-9]+\.dump' \
  -printf '%f\n' | sort -r)
if ((${#backups[@]} > 3)); then
  for old_backup in "${backups[@]:3}"; do
    rm -- "$backup_dir/$old_backup"
  done
fi

rollback() {
  status=$?
  trap - ERR
  echo "Deployment failed; restoring application commit $old_sha" >&2
  git checkout --detach "$old_sha"
  "${compose[@]}" build app </dev/null
  "${compose[@]}" up -d --no-deps app scheduler watchdog </dev/null
  exit "$status"
}
trap rollback ERR

git checkout --detach "$actual_sha"
"${compose[@]}" config --quiet </dev/null
"${compose[@]}" build app </dev/null
"${compose[@]}" up -d --no-deps app scheduler watchdog </dev/null

wait_healthy() {
  local service="$1" attempts="$2" id state
  id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$id" ]] || return 1
  for ((i=0; i<attempts; i++)); do
    state="$(docker inspect --format '{{.State.Health.Status}}' "$id")"
    [[ "$state" == healthy ]] && return 0
    [[ "$state" != unhealthy ]] || return 1
    sleep 5
  done
  return 1
}
wait_healthy app 24
wait_healthy scheduler 30
watchdog_id="$("${compose[@]}" ps -q watchdog)"
[[ -n "$watchdog_id" && "$(docker inspect --format '{{.State.Running}}' "$watchdog_id")" == true ]]
trap - ERR
echo "Deployed $tag ($actual_sha); backup: $backup"
