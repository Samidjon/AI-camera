#!/usr/bin/env bash
# Runs every check: backend + edge agent lint and tests, dashboard lint + typecheck + build.
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
step "edge agent: ruff"     "$ROOT/edge/camera-agent" uv run ruff check .
step "edge agent: pytest"   "$ROOT/edge/camera-agent" uv run pytest -q
step "dashboard: lint"      "$ROOT/apps/dashboard"  npm run lint
step "dashboard: typecheck" "$ROOT/apps/dashboard"  npm run typecheck
step "dashboard: build"     "$ROOT/apps/dashboard"  npm run build
# Browser end-to-end tests against the real stack (several minutes): ACI_E2E=1 to include them.
if [ "${ACI_E2E:-0}" = "1" ]; then step "dashboard: e2e (Playwright)" "$ROOT/apps/dashboard" npx playwright test; fi

if [ ${#failed[@]} -gt 0 ]; then
  printf '\nFAILED: %s\n' "${failed[*]}"; exit 1
fi
printf '\nAll checks passed.\n'
