# ============================================================
#  install_postgres_windows.ps1
#  PostgreSQL 18 (Windows x64) EnterpriseDB installer 자동 다운로드 + silent install.
#
#  사용법:
#    1) 관리자 권한 PowerShell 실행
#    2) Set-ExecutionPolicy -Scope Process Bypass
#    3) .\install_postgres_windows.ps1
#       .\install_postgres_windows.ps1 -SuperPassword "MySecret!"
#       .\install_postgres_windows.ps1 -PgVersion 17
#       .\install_postgres_windows.ps1 -InstallerUrl "https://.../postgresql-18.0-1-windows-x64.exe"
#    4) PG 18 자동 다운로드 (~300MB) + 설치 (~5분)
#    5) 이어서: .\install_pgvector_windows.ps1
#
#  주의:
#    - 기본 superuser 비밀번호는 "postgres" — 운영 환경에선 반드시 -SuperPassword 로 변경.
#    - 포트 기본 5432 — 다른 PG 가 점유 중이면 -Port 로 변경.
#    - installer URL 은 PostgreSQL 마이너 버전에 따라 변동.
#      필요 시 https://www.postgresql.org/download/windows/ 에서 최신 URL 을
#      얻어 -InstallerUrl 로 넘긴다.
# ============================================================

[CmdletBinding()]
param(
    [int]   $PgVersion        = 18,
    [string]$InstallerUrl     = "",
    [string]$SuperPassword    = "postgres",
    [int]   $Port             = 5432,
    [string]$InstallDir       = "",
    [string]$DataDir          = "",
    [string]$Locale           = "English, United States",
    [switch]$KeepInstaller
)

$ErrorActionPreference = "Stop"

function Write-OK    ($m) { Write-Host "[OK]    $m" -ForegroundColor Green }
function Write-Info  ($m) { Write-Host "[INFO]  $m" -ForegroundColor Cyan }
function Write-Warn2 ($m) { Write-Host "[WARN]  $m" -ForegroundColor Yellow }
function Write-Err   ($m) { Write-Host "[ERROR] $m" -ForegroundColor Red }

# ------------------------------------------------------------
# 0) 관리자 권한 확인
# ------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
          ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Err "관리자 권한이 필요하다."
    Write-Host ""
    Write-Host "  해결 방법:"
    Write-Host "    1) Windows 시작 메뉴에서 'PowerShell' 검색"
    Write-Host "    2) '관리자 권한으로 실행' 클릭"
    Write-Host "    3) 다음 명령 실행:"
    Write-Host "         Set-ExecutionPolicy -Scope Process Bypass"
    Write-Host "         cd '$PSScriptRoot'"
    Write-Host "         .\install_postgres_windows.ps1"
    exit 1
}
Write-OK "관리자 권한 확인됨"

# ------------------------------------------------------------
# 1) 이미 설치되어 있는지 확인
# ------------------------------------------------------------
$targetHome = if ($InstallDir) { $InstallDir } else { "C:\Program Files\PostgreSQL\$PgVersion" }
$existingPsql = Join-Path $targetHome "bin\psql.exe"
if (Test-Path $existingPsql) {
    Write-Warn2 "이미 PostgreSQL $PgVersion 가 설치되어 있다: $targetHome"
    Write-Host "  재설치하려면 먼저 제어판에서 제거하라."
    Write-Host "  pgvector 만 설치하려면: .\install_pgvector_windows.ps1"
    exit 0
}

# 같은 포트를 다른 PG 가 점유 중인지 확인
$pgRoot = "C:\Program Files\PostgreSQL"
if (Test-Path $pgRoot) {
    $other = Get-ChildItem -Path $pgRoot -Directory -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -ne "$PgVersion" -and (Test-Path (Join-Path $_.FullName "bin\psql.exe")) }
    if ($other) {
        Write-Warn2 ("다른 PG 버전 감지: " + ($other.Name -join ", "))
        Write-Host "  같은 포트 $Port 을 사용 중이면 충돌한다. -Port 로 변경하거나 기존 PG 를 중지하라."
    }
}

# ------------------------------------------------------------
# 2) installer URL 결정 + 다운로드
# ------------------------------------------------------------
# EnterpriseDB 공식 installer (PostgreSQL 마이너 버전이 변하면 URL 도 변함)
# 패치 버전을 확실히 모를 때를 대비해 알려진 패치 후보들을 순회 시도한다.
$defaultUrls = switch ($PgVersion) {
    18 { @(
        "https://get.enterprisedb.com/postgresql/postgresql-18.1-1-windows-x64.exe",
        "https://get.enterprisedb.com/postgresql/postgresql-18.0-1-windows-x64.exe"
    ) }
    17 { @(
        "https://get.enterprisedb.com/postgresql/postgresql-17.4-1-windows-x64.exe",
        "https://get.enterprisedb.com/postgresql/postgresql-17.3-1-windows-x64.exe",
        "https://get.enterprisedb.com/postgresql/postgresql-17.2-1-windows-x64.exe"
    ) }
    16 { @(
        "https://get.enterprisedb.com/postgresql/postgresql-16.8-1-windows-x64.exe",
        "https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64.exe",
        "https://get.enterprisedb.com/postgresql/postgresql-16.4-1-windows-x64.exe"
    ) }
    default { @() }
}

$urlsToTry = @()
if ($InstallerUrl) { $urlsToTry += $InstallerUrl }
$urlsToTry += $defaultUrls

if ($urlsToTry.Count -eq 0) {
    Write-Err "PG $PgVersion 에 대한 기본 URL 이 없다. -InstallerUrl 로 직접 지정하라."
    Write-Host "  최신 URL: https://www.postgresql.org/download/windows/"
    exit 1
}

$workDir = Join-Path $env:TEMP "pg_install"
if (-not (Test-Path $workDir)) { New-Item -ItemType Directory -Path $workDir | Out-Null }

$installerPath = $null
foreach ($u in $urlsToTry) {
    $name = Split-Path -Leaf $u
    $out = Join-Path $workDir $name
    if (Test-Path $out) {
        $sz = (Get-Item $out).Length
        if ($sz -gt 100MB) {
            Write-OK "캐시된 installer 사용: $out ($([math]::Round($sz/1MB)) MB)"
            $installerPath = $out
            break
        }
        Remove-Item $out -Force
    }
    try {
        Write-Info "installer 다운로드 시도: $u"
        Write-Host "  (~300MB, 네트워크 속도에 따라 수 분 소요)"
        # ProgressPreference = SilentlyContinue 로 다운로드 속도 향상
        $oldProg = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest -Uri $u -OutFile $out -UseBasicParsing -TimeoutSec 1800
        } finally {
            $ProgressPreference = $oldProg
        }
        $sz = (Get-Item $out).Length
        if ($sz -gt 100MB) {
            Write-OK "다운로드 완료: $out ($([math]::Round($sz/1MB)) MB)"
            $installerPath = $out
            break
        } else {
            Write-Warn2 "다운로드 결과가 너무 작다 ($sz bytes) → 다음 후보 시도"
            Remove-Item $out -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Warn2 "다운로드 실패: $($_.Exception.Message)"
    }
}

if (-not $installerPath) {
    Write-Err "PostgreSQL $PgVersion installer 를 다운로드하지 못했다."
    Write-Host ""
    Write-Host "  해결 방법:"
    Write-Host "    1) https://www.postgresql.org/download/windows/ 에서 EDB installer 직접 다운로드"
    Write-Host "    2) 다운로드한 .exe 경로를 -InstallerUrl 또는 file:// URL 로 지정"
    Write-Host "       또는 .\install_postgres_windows.ps1 호출 전에 워크 디렉터리에 복사:"
    Write-Host "         $workDir"
    exit 1
}

# ------------------------------------------------------------
# 3) silent install
# ------------------------------------------------------------
if (-not $InstallDir) { $InstallDir = $targetHome }
if (-not $DataDir)    { $DataDir    = Join-Path $InstallDir "data" }
$svcName = "postgresql-x64-$PgVersion"

Write-Info "Silent install 시작"
Write-Host "  prefix:      $InstallDir"
Write-Host "  datadir:     $DataDir"
Write-Host "  service:     $svcName"
Write-Host "  port:        $Port"
Write-Host "  superuser:   postgres"
Write-Host "  password:    $(if ($SuperPassword -eq 'postgres') { '(기본값 postgres — 변경 권장)' } else { '<지정됨>' })"

$args = @(
    "--mode", "unattended",
    "--unattendedmodeui", "none",
    "--superpassword", $SuperPassword,
    "--servicename",   $svcName,
    "--serviceaccount","postgres",
    "--servicepassword", $SuperPassword,
    "--serverport",    "$Port",
    "--prefix",        $InstallDir,
    "--datadir",       $DataDir,
    "--locale",        $Locale,
    "--enable-components", "server,commandlinetools"
)

try {
    $proc = Start-Process -FilePath $installerPath -ArgumentList $args -Wait -PassThru -NoNewWindow
    if ($proc.ExitCode -ne 0) {
        Write-Err "installer 가 비정상 종료 (exit=$($proc.ExitCode))"
        Write-Host "  로그: $env:TEMP\install-postgresql.log"
        exit 1
    }
    Write-OK "Silent install 완료 (exit=0)"
} catch {
    Write-Err "installer 실행 실패: $($_.Exception.Message)"
    exit 1
}

# ------------------------------------------------------------
# 4) 설치 결과 검증
# ------------------------------------------------------------
$psql = Join-Path $InstallDir "bin\psql.exe"
if (-not (Test-Path $psql)) {
    Write-Err "psql.exe 가 설치되지 않았다: $psql"
    Write-Host "  로그 확인: $env:TEMP\install-postgresql.log"
    exit 1
}
Write-OK "psql.exe 확인: $psql"

# 서비스 상태
$svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if (-not $svc) {
    $svc = Get-Service -Name "postgresql*$PgVersion*" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($svc) {
    if ($svc.Status -ne "Running") {
        try {
            Start-Service -Name $svc.Name
            Start-Sleep -Seconds 2
        } catch {
            Write-Warn2 "서비스 시작 실패: $($_.Exception.Message)"
        }
    }
    $svc.Refresh()
    Write-OK "서비스 상태: $($svc.Name) = $($svc.Status)"
} else {
    Write-Warn2 "서비스 '$svcName' 을 찾지 못했다."
}

# 연결 테스트
try {
    $env:PGPASSWORD = $SuperPassword
    $verOutput = & $psql -U postgres -h localhost -p $Port -tAc "SELECT version();" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "연결 성공: $verOutput"
    } else {
        Write-Warn2 "연결 테스트 실패 (rc=$LASTEXITCODE): $verOutput"
    }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------
# 5) 정리
# ------------------------------------------------------------
if (-not $KeepInstaller) {
    try {
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        Write-Info "installer 삭제: $installerPath (-KeepInstaller 로 보존 가능)"
    } catch { }
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " PostgreSQL $PgVersion 설치 완료" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host " 설치 정보:"
Write-Host "   prefix:    $InstallDir"
Write-Host "   data:      $DataDir"
Write-Host "   service:   $svcName"
Write-Host "   port:      $Port"
Write-Host "   user:      postgres"
if ($SuperPassword -eq "postgres") {
    Write-Host "   password:  postgres  (기본값 — 즉시 변경 권장!)" -ForegroundColor Yellow
} else {
    Write-Host "   password:  <지정한 값>"
}
Write-Host ""
Write-Host " 다음 단계:"
Write-Host "   1) pgvector 설치:"
Write-Host "        .\install_pgvector_windows.ps1"
Write-Host "   2) api_server 셋업:"
Write-Host "        cd ..\api_server"
Write-Host "        .\setup.bat"
Write-Host ""
