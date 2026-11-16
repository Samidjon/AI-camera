#!/usr/bin/env bash
# Build an update package for `aci-agentctl update`: dist/aci-agent-<version>.tar.gz + .sha256
#   ./deploy/package.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
NAME="aci-agent-$VERSION"
OUT="$ROOT/dist"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME/deploy" "$OUT"
cp -r "$ROOT/aci_agent" "$STAGE/$NAME/"
cp -r "$ROOT/deploy/linux" "$STAGE/$NAME/deploy/"
cp "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$ROOT/README.md" "$STAGE/$NAME/"
find "$STAGE" -name '__pycache__' -prune -exec rm -rf {} +
# Model files are not bundled (large, licensed separately, verified by SHA-256 at load time).
tar -C "$STAGE" -czf "$OUT/$NAME.tar.gz" "$NAME"
( cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )
echo "$OUT/$NAME.tar.gz"
cat "$OUT/$NAME.tar.gz.sha256"
