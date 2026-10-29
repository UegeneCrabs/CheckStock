---
name: checkstock-deploy
description: "Use for CheckStock releases through GitHub: prepare dev-release and a version tag, hand off Actions verification to the user, and troubleshoot SSH or server deployment failures. Do not use for ordinary local Docker runs."
---

# Release CheckStock through GitHub

Apply [checkstock-task-guide](../checkstock-task-guide/SKILL.md) first. Use [docs/releases.md](../../../docs/releases.md) for the release and database recovery procedures, and compare it with `.github/workflows/deploy-release.yml` and `deploy/deploy-release.sh` before acting. Documentation does not prove the current state of GitHub or the server.

## Prepare the release

1. Determine whether the user wants a review, a fix for a failed deployment, or a new release. Push branches and a tag only when the user has authorized a release or deployment. That request authorizes the necessary pushes and the resulting Actions run.
2. Check the working tree, current branch, `origin/master`, `origin/dev-release`, existing `v*` tags, and their commits. Refresh remote refs before choosing a version. Never force-push over someone else's branches or tags.
3. For a normal release, prepare a verified commit on `master`, fast-forward `dev-release` to that commit without an extra commit, then create a `vMAJOR.MINOR.PATCH` tag on it. Its version must exceed every previously published valid release tag. `deploy/VERSION` is not used. If `dev-release` does not exist, create it at the selected `master` commit.
4. Before tagging, run checks relevant to the changes, including at least the Ruff command from the workflow and `docker compose --env-file .env.example -f deploy/docker-compose.yml config --quiet`. Fix blocking failures; do not disable checks to release. If the image was not built locally, leave that verification to Actions and say so.

## Access and publication

- The workflow reads Repository Secrets `DEV_DEPLOY_HOST`, `DEV_DEPLOY_USER`, `DEV_DEPLOY_SSH_KEY`, and `DEV_DEPLOY_KNOWN_HOSTS`. GitHub Variable `DEPLOY_PORT` is optional and defaults to 22. Step environment variables without the `DEV_` prefix are not Secret names. If these are Environment Secrets, the job must explicitly use that environment; the current workflow does not. Never request secret values in chat or print them in logs.
- Verify that `DEV_DEPLOY_HOST` matches the host entry in `DEV_DEPLOY_KNOWN_HOSTS`, and that the private key corresponds to a public key in `authorized_keys` for `DEV_DEPLOY_USER`. `known_hosts` contains the server address, key type, and server public key; it is not the deployment user's key. If Secret values are inaccessible, check only their presence or the Actions error and do not claim access is configured.
- The `DEV_` prefix does not establish that the target is a test environment. `DEV_DEPLOY_HOST` selects the actual server; the server script uses `/home/checkstock` and stores backups in `/home/bd_backup`.
- Pushing the tag starts `Deploy CheckStock release`. Once the tag push succeeds, end work on that deployment turn. Do not wait for, poll, or inspect the Actions run. In the final response, write in Russian that the tag was pushed and deployment success is not yet confirmed. Tell the user to check GitHub Actions and paste the failed step's log into the chat if an error occurs. Include an Actions link when one is already known without polling. Do not claim that the application was deployed merely because the tag push succeeded.

## Troubleshoot a reported failure

- When the user later reports an Actions failure, read the specific failed step. Empty SSH environment variables mean the required Secrets are absent or unavailable to the job; changing SSH server settings will not fix that.
- If the code and tag are unchanged, rerun the failed workflow or job in Actions. Do not delete or push the same tag again. If a fix needs a new commit, prepare the next strictly increasing tag; never reuse a published version number.
- The server script creates and verifies a PostgreSQL dump before switching code, then checks `app`, `scheduler`, and `watchdog`. On failure it attempts to restore the previous code. Code rollback does not restore the database. Assess the rollback log and server state before suggesting database recovery.
