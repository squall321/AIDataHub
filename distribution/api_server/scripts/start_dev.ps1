# start_dev.ps1 — one-command local dev bootstrap (Windows / PowerShell).
#
# 1. PostgreSQL 컨테이너 기동 (docker compose).
# 2. healthcheck 통과 대기.
# 3. alembic upgrade head.
# 4. 표준 에이전트 시드.
# 5. uvicorn (api.main) 기동.
#
# Usage:
#   pwsh ./scripts/start_dev.ps1
$ErrorActionPreference = "Stop"

# 프로젝트 루트로 이동 (스크립트 위치 기준).
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Root
Set-Location $Root

Write-Host "[start_dev] root=$Root" -ForegroundColor Cyan

# venv 자동 활성화 (있으면).
$venvActivate = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    Write-Host "[start_dev] activating .venv" -ForegroundColor Cyan
    . $venvActivate
}

$env:PYTHONPATH = Join-Path $Root "src"
$env:PYTHONIOENCODING = "utf-8"

# 1) Postgres 기동 (존재하지 않으면 생성).
Write-Host "[start_dev] docker compose up -d postgres" -ForegroundColor Cyan
docker compose up -d postgres

# 2) healthy 대기.
Write-Host "[start_dev] waiting for postgres healthcheck..." -ForegroundColor Cyan
$tries = 0
$max = 30
while ($tries -lt $max) {
    $status = docker inspect --format '{{json .State.Health.Status}}' (docker compose ps -q postgres) 2>$null
    if ($status -match "healthy") { break }
    Start-Sleep -Seconds 2
    $tries++
}
if ($tries -ge $max) {
    Write-Error "postgres did not become healthy within $($max*2)s"
    exit 1
}
Write-Host "[start_dev] postgres healthy" -ForegroundColor Green

# 3) 마이그레이션.
Write-Host "[start_dev] alembic upgrade head" -ForegroundColor Cyan
python -m alembic -c alembic.ini upgrade head

# 4) 표준 에이전트 시드.
Write-Host "[start_dev] python -m api.seed" -ForegroundColor Cyan
python -m api.seed

# 5) API 서버.
Write-Host "[start_dev] python -m api.main" -ForegroundColor Cyan
python -m api.main
