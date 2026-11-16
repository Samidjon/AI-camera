#!/usr/bin/env bash
# Runs every MVP 01 check: backend lint + tests (incl. migrations), dashboard lint + typecheck + build.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
failed=()

step() {
  local name="$1" dir="$2"; shift 2
  printf '\n=== %s ===\n' "$name"
  (cd "$dir" && "$@") || failed+=("$name")
}

step "backend: ruff"        "$ROOT/apps/api-server" uv run ruff check .
step "backend: pytest"      "$ROOT/apps/api-server" uv run pytest -q
step "dashboard: lint"      "$ROOT/apps/dashboard"  npm run lint
step "dashboard: typecheck" "$ROOT/apps/dashboard"  npm run typecheck
step "dashboard: build"     "$ROOT/apps/dashboard"  npm run build

if [ ${#failed[@]} -gt 0 ]; then
  printf '\nFAILED: %s\n' "${failed[*]}"; exit 1
fi
printf '\nAll checks passed.\n'
