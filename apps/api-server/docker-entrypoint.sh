#!/bin/sh
# Applies database migrations before the server starts (idempotent; safe on every restart).
# Set ACI_SKIP_MIGRATIONS=1 to start without migrating (e.g. read-only replica).
set -eu
if [ "${ACI_SKIP_MIGRATIONS:-0}" != "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi
exec "$@"
