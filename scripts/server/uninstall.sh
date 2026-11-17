#!/usr/bin/env bash
# Remove the Docker Compose installation.
#
#   scripts/server/uninstall.sh            # stop and remove containers + images, KEEP data volumes
#   scripts/server/uninstall.sh --purge    # also delete the database and TLS volumes (irreversible)
#
# Backups in /var/backups/aci are never touched.
set -euo pipefail
cd "$(dirname "$0")/../.."
ENV_FILE="${ENV_FILE:-.env.production}"
DC=(docker compose --env-file "$ENV_FILE")
if [[ "${1:-}" == "--purge" ]]; then
  read -r -p "Delete ALL data (database, certificates)? Type 'purge' to continue: " ok
  [[ "$ok" == "purge" ]] || { echo "aborted"; exit 1; }
  "${DC[@]}" down --rmi local --volumes
  echo "removed containers, images and volumes"
else
  "${DC[@]}" down --rmi local
  echo "removed containers and images; data volumes kept (use --purge to delete them)"
fi
