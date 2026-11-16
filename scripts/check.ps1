# Runs every check: backend + edge agent lint and tests, dashboard lint + typecheck + build.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts\check.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$failed = @()

function Step($name, $dir, [scriptblock]$cmd) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    Push-Location $dir
    try { & $cmd; if ($LASTEXITCODE -ne 0) { $script:failed += $name } }
    catch { $script:failed += $name; Write-Host $_ -ForegroundColor Red }
    finally { Pop-Location }
}

$api = Join-Path $root "apps\api-server"
$web = Join-Path $root "apps\dashboard"
$edge = Join-Path $root "edge\camera-agent"

Step "backend: ruff"            $api { uv run ruff check . }
Step "backend: pytest"          $api { uv run pytest -q }
Step "edge agent: ruff"         $edge { uv run ruff check . }
Step "edge agent: pytest"       $edge { uv run pytest -q }
Step "dashboard: lint"          $web { npm run lint }
Step "dashboard: typecheck"     $web { npm run typecheck }
Step "dashboard: build"         $web { npm run build }
# Browser end-to-end tests against the real stack (several minutes): set ACI_E2E=1 to include them.
if ($env:ACI_E2E -eq "1") { Step "dashboard: e2e (Playwright)" $web { npx playwright test } }

if ($failed.Count -gt 0) {
    Write-Host "`nFAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "`nAll checks passed." -ForegroundColor Green
