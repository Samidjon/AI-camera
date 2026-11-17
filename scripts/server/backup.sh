#!/usr/bin/env bash
# PostgreSQL backup of the Docker Compose installation, verified and rotated.
#
#   scripts/server/backup.sh [--dir /var/backups/aci] [--keep 14]
#
# Creates <dir>/aci-<UTC timestamp>.dump (pg_dump custom format, compressed), verifies it with
# pg_restore --list (a corrupt dump fails the run and is deleted), keeps the newest N.
# Cron example (daily 02:30):  30 2 * * *  /opt/aci/scripts/server/backup.sh >> /var/log/aci-backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/../.."
DIR=/var/backups/aci
KEEP=14
ENV_FILE=.env.production
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
umask 077
mkdir -p "$DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$DIR/aci-$STAMP.dump"
DC=(docker compose --env-file "$ENV_FILE")

echo "[backup] dumping database → $OUT"
"${DC[@]}" exec -T postgres pg_dump -U aci -d aci --format=custom --compress=6 > "$OUT.partial"
echo "[backup] verifying"
if ! "${DC[@]}" exec -T postgres pg_restore --list < "$OUT.partial" > /dev/null; then
  rm -f "$OUT.partial"
  echo "[backup] FAILED: dump is not readable" >&2
  exit 1
fi
mv "$OUT.partial" "$OUT"
sha256sum "$OUT" > "$OUT.sha256"
echo "[backup] ok: $(du -h "$OUT" | cut -f1)"

# rotation: keep the newest $KEEP dumps
ls -1t "$DIR"/aci-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -f "$old" "$old.sha256"
  echo "[backup] removed old $old"
done
