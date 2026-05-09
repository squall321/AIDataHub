# ===========================================================================
# AI Data Hub — Windows PowerShell 원터치 셋업 (Docker Desktop)
#
# 사용 (PowerShell):
#   cd deploy
#   .\install.ps1
#
# 실행 정책 차단 시 한 번만:
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
# ===========================================================================
$ErrorActionPreference = "Stop"

Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "================================================================"
Write-Host " AI Data Hub — install.ps1"
Write-Host "================================================================"
Write-Host " deploy dir : $PSScriptRoot"
Write-Host "================================================================"

# ---- 1) Docker 검증 -------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Docker 가 PATH 에 없다. Docker Desktop 설치 필요:" -ForegroundColor Red
    Write-Host "        https://docs.docker.com/desktop/install/windows-install/" -ForegroundColor Red
    exit 1
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] docker compose v2 가 필요하다." -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Docker / compose v2 확인됨"

# ---- 2) .env 준비 ---------------------------------------------------------
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[INFO] .env 생성됨 — 운영 전 비밀번호/포트 수정 권장"
}

# .env 파싱 (간단 KEY=VALUE)
$envVars = @{}
Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $k = $line.Substring(0, $idx).Trim()
        $v = $line.Substring($idx + 1).Trim()
        $envVars[$k] = $v
    }
}
$apiPort = if ($envVars.ContainsKey("API_PORT") -and $envVars["API_PORT"]) { $envVars["API_PORT"] } else { "8000" }
Write-Host "[OK] .env 로드됨 (API_PORT=$apiPort)"

# ---- 3) compose up --------------------------------------------------------
Write-Host ""
Write-Host "[1/3] PostgreSQL + API 빌드 + 기동..."
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] docker compose up 실패" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 컨테이너 기동 명령 완료"

# ---- 4) 헬스체크 ----------------------------------------------------------
Write-Host ""
Write-Host "[2/3] /api/system/health 응답 대기 (최대 60초)..."
$healthUrl = "http://localhost:$apiPort/api/system/health"
$success = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $success = $true; break }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if ($success) {
    Write-Host "[OK] API 응답 확인: $healthUrl"
} else {
    Write-Host "[WARN] 60초 안에 응답 없음 — 'docker compose logs api' 로 진단 권장" -ForegroundColor Yellow
}

# ---- 5) 안내 --------------------------------------------------------------
Write-Host ""
Write-Host "================================================================"
Write-Host " 셋업 완료"
Write-Host "================================================================"
Write-Host " API        : http://localhost:$apiPort"
Write-Host " 헬스체크   : http://localhost:$apiPort/api/system/health"
Write-Host " API docs   : http://localhost:$apiPort/docs"
Write-Host " discover   : http://localhost:$apiPort/api/discover"
Write-Host ""
Write-Host " 로그       : docker compose logs -f api"
Write-Host " 재시작     : docker compose restart api"
Write-Host " 종료       : docker compose down"
Write-Host " 데이터삭제 : docker compose down -v"
Write-Host "================================================================"
