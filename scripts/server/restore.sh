#!/usr/bin/env bash
# Restore a PostgreSQL dump made by backup.sh. DESTRUCTIVE: replaces the current database.
#
#   scripts/server/restore.sh /var/backups/aci/aci-20261116T023000Z.dump
#
# Steps: checksum check → stop API → safety dump of the current DB → restore (--clean) → start API.
set -euo pipefail
cd "$(dirname "$0")/../.."
DUMP="${1:?usage: restore.sh <file.dump>}"
ENV_FILE="${ENV_FILE:-.env.production}"
DC=(docker compose --env-file "$ENV_FILE")
[[ -f "$DUMP" ]] || { echo "no such file: $DUMP" >&2; exit 1; }
if [[ -f "$DUMP.sha256" ]]; then sha256sum -c "$DUMP.sha256"; fi

read -r -p "Replace the current database with $(basename "$DUMP")? Type 'restore' to continue: " ok
[[ "$ok" == "restore" ]] || { echo "aborted"; exit 1; }

echo "[restore] stopping API"
"${DC[@]}" stop api
SAFETY="$(dirname "$DUMP")/pre-restore-$(date -u +%Y%m%dT%H%M%SZ).dump"
echo "[restore] safety copy of the current database → $SAFETY"
"${DC[@]}" exec -T postgres pg_dump -U aci -d aci --format=custom > "$SAFETY"
echo "[restore] restoring"
"${DC[@]}" exec -T postgres pg_restore -U aci -d aci --clean --if-exists --no-owner < "$DUMP"
echo "[restore] starting API (migrations run on start)"
"${DC[@]}" start api
echo "[restore] done. If something is wrong: restore.sh $SAFETY"
