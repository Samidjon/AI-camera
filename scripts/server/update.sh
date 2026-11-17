#!/usr/bin/env bash
# Safe update of the Docker Compose installation with automatic rollback.
#
#   scripts/server/update.sh <git tag or branch, e.g. v1.0.1>
#
# 1. verified database backup (backup.sh) — always before touching anything
# 2. images of the running version are kept as aci-*:rollback
# 3. checkout + build + start the new version (migrations run on API start)
# 4. health check for 120 s; on failure → previous code, previous images and the backup are restored
#    (database restore only if the new version migrated it — asked interactively).
set -euo pipefail
cd "$(dirname "$0")/../.."
TARGET="${1:?usage: update.sh <git tag/branch>}"
ENV_FILE="${ENV_FILE:-.env.production}"
DC=(docker compose --env-file "$ENV_FILE")
PREV=$(git rev-parse HEAD)

echo "[update] backup first"
scripts/server/backup.sh --env-file "$ENV_FILE"
BACKUP=$(ls -1t /var/backups/aci/aci-*.dump | head -1)

for img in aci-api aci-dashboard; do
  cur=$(docker image ls --format '{{.Repository}}:{{.Tag}}' "$img" | head -1 || true)
  [[ -n "$cur" ]] && docker tag "$cur" "$img:rollback"
done

echo "[update] $PREV → $TARGET"
git fetch --tags --quiet
git checkout --quiet "$TARGET"
"${DC[@]}" build
"${DC[@]}" up -d

healthy() { "${DC[@]}" exec -T api curl -fsS http://127.0.0.1:8000/api/v1/system/status > /dev/null 2>&1; }
for _ in $(seq 1 24); do
  if healthy; then echo "[update] healthy on $TARGET"; exit 0; fi
  sleep 5
done

echo "[update] NOT healthy — rolling back to $PREV" >&2
git checkout --quiet "$PREV"
for img in aci-api aci-dashboard; do
  docker image inspect "$img:rollback" > /dev/null 2>&1 && docker tag "$img:rollback" "$img:${ACI_VERSION:-1.0.0}"
done
"${DC[@]}" up -d --no-build
read -r -p "Restore the pre-update database backup $BACKUP too? [y/N] " yn
[[ "$yn" =~ ^[Yy]$ ]] && scripts/server/restore.sh "$BACKUP"
exit 1
