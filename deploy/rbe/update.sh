#!/usr/bin/env bash
# Run as root on the RBE server. Only deployment/rbe is accepted.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
[[ $# == 0 || ( $# == 1 && $1 == --check ) ]] || { echo 'Usage: rbe-update [--check]' >&2; exit 1; }
exec 9>/run/lock/rbe-update.lock
flock -n 9 || { echo 'Another RBE update is running.' >&2; exit 1; }
cd /opt/rbe/code
branch=deployment/rbe
[[ $(git branch --show-current) == "$branch" ]] || { echo 'Wrong branch; stopped.' >&2; exit 1; }
[[ $(git remote get-url origin) == https://github.com/UegeneCrabs/CheckStock.git ]] || { echo 'Unexpected repository; stopped.' >&2; exit 1; }
[[ -z $(git status --porcelain) ]] || { echo 'Working tree has local changes; stopped.' >&2; exit 1; }
GIT_TERMINAL_PROMPT=0 git fetch --no-tags origin "refs/heads/$branch:refs/remotes/origin/$branch"
old=$(git rev-parse HEAD)
target=$(git rev-parse "refs/remotes/origin/$branch")
git merge-base --is-ancestor "$old" "$target" || { echo 'History diverged; manual review required.' >&2; exit 1; }
# Do not overwrite live data or change dependencies without a reviewed migration.
if git ls-tree -r --name-only "$target" | grep -E '^(rbe-yandex-site/(data|secrets)/|data/.*[.]db($|-)|secrets/(yandex_tokens|users|yandex_connections)[.]json$)'; then
    echo 'Tracked runtime data detected; stopped.' >&2; exit 1
fi
if ! git diff --quiet "$old" "$target" -- requirements.txt rbe-yandex-site/requirements.txt pyproject.toml poetry.lock uv.lock; then
    echo 'Dependencies changed; prepare and validate the environment before deployment.' >&2; exit 1
fi
echo "Current: $old"
echo "Target:  $target"
[[ ${1:-} != --check ]] || exit 0
if [[ $old == "$target" ]]; then
    curl --fail --silent --show-error --max-time 15 https://153.76.164.196/health
    echo; echo 'Already up to date.'; exit 0
fi
stopped=0
rollback() {
    result=$?
    trap - ERR
    if [[ $stopped == 1 ]]; then
        echo "Update failed; restoring code $old. Database is not automatically rolled back." >&2
        systemctl stop rbe || true
        git reset --keep "$old" || true
        systemctl start rbe || true
    fi
    exit "$result"
}
trap rollback ERR
# Stop the application and its report workers before taking a consistent backup.
systemctl stop rbe
stopped=1
systemctl start rbe-backup.service
git merge --ff-only "$target"
systemctl start rbe
healthy=0
for attempt in {1..30}; do
    if curl --fail --silent --max-time 3 http://127.0.0.1:8060/health >/dev/null; then healthy=1; break; fi
    sleep 2
done
[[ $healthy == 1 ]]
curl --fail --silent --show-error --max-time 15 https://153.76.164.196/health
stopped=0
trap - ERR
echo; echo "RBE updated: $target"
