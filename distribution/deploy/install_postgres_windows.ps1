# ===========================================================================
# AI Data Hub — PostgreSQL 18 자동 설치 (Windows)
#
# 사용:
#   .\install_postgres_windows.ps1 -PostgresPassword "<강한_비번>"
#
# 옵션:
#   -PgVersion 18 | 17 | 16     기본 18
#   -InstallDir  "C:\Program Files\PostgreSQL\<ver>"  기본
#   -Port 5432                    기본
#   -Force                        이미 있을 때도 재설치
#
# 동작:
#   1) 기존 PG 설치 검증 (있으면 skip; -Force 시 무시)
#   2) EDB 인스톨러 다운로드 ($env:TEMP)
#   3) silent install (--mode unattended)
#   4) 서비스 자동 시작 + Path 등록
#   5) psql 로 접속 검증
#
# 종료 코드:
#   0  성공 (또는 이미 설치됨)
#   1  파라미터/검증 실패
#   2  다운로드 실패
#   3  설치 실패
#   4  접속 검증 실패
# ===========================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PostgresPassword,

    [ValidateSet("16", "17", "18")]
    [string]$PgVersion = "18",

    [string]$InstallDir = "",

    [int]$Port = 5432,

    [switch]$Force
)

$ErrorActionPreference = "Stop"

# ---- 관리자 권한 체크 ------------------------------------------------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] 관리자 권한이 필요합니다. 관리자 PowerShell 에서 재실행하세요." -ForegroundColor Red
    exit 1
}

if (-not $InstallDir) {
    $InstallDir = "C:\Program Files\PostgreSQL\$PgVersion"
}
$dataDir = Join-Path $InstallDir "data"
$psqlExe = Join-Path $InstallDir "bin\psql.exe"

Write-Host ""
Write-Host "================================================================"
Write-Host " PostgreSQL $PgVersion 자동 설치"
Write-Host "================================================================"
Write-Host " InstallDir : $InstallDir"
Write-Host " DataDir    : $dataDir"
Write-Host " Port       : $Port"
Write-Host "================================================================"

# ---- 1) 기존 설치 검증 -----------------------------------------------------
if ((Test-Path $psqlExe) -and (-not $Force)) {
    Write-Host "[OK] PostgreSQL $PgVersion 이미 설치됨 — skip (재설치하려면 -Force)" -ForegroundColor Green

    # 서비스 기동 확인
    $svc = Get-Service -Name "postgresql-x64-$PgVersion" -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne "Running") {
        Write-Host "[INFO] 서비스가 멈춰있어 시작합니다..."
        Start-Service $svc.Name
    }
    exit 0
}

# 다른 버전 충돌 검사 — 같은 포트 사용 시 경고
$portInUse = (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) | Select-Object -First 1
if ($portInUse -and (-not $Force)) {
    Write-Host "[ERROR] 포트 $Port 가 이미 사용 중입니다. 다른 PG 가 떠 있을 수 있습니다." -ForegroundColor Red
    Write-Host "        services.msc 에서 기존 postgresql-x64-* 서비스를 정리하거나 -Port 변경 후 재시도." -ForegroundColor Red
    exit 1
}

# ---- 2) 인스톨러 다운로드 --------------------------------------------------
# EDB 공식 인스톨러 URL (버전별)
# 참고: https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
$installerUrls = @{
    "18" = "https://get.enterprisedb.com/postgresql/postgresql-18.0-1-windows-x64.exe"
    "17" = "https://get.enterprisedb.com/postgresql/postgresql-17.2-1-windows-x64.exe"
    "16" = "https://get.enterprisedb.com/postgresql/postgresql-16.6-1-windows-x64.exe"
}
$installerUrl = $installerUrls[$PgVersion]
$installerPath = Join-Path $env:TEMP "postgresql-$PgVersion-installer.exe"

if (-not (Test-Path $installerPath)) {
    Write-Host ""
    Write-Host "[1/3] 인스톨러 다운로드 중 ... (~300MB, 수 분 소요)"
    Write-Host "      $installerUrl"
    try {
        # TLS 1.2 명시 (오래된 Windows 호환)
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        # BITS 우선 (진행률 표시 + 재개 가능)
        if (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue) {
            Start-BitsTransfer -Source $installerUrl -Destination $installerPath -DisplayName "PostgreSQL $PgVersion installer"
        } else {
            Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        }
    } catch {
        Write-Host "[ERROR] 다운로드 실패: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "        수동: $installerUrl 다운로드 후 $installerPath 에 저장하고 재실행." -ForegroundColor Yellow
        exit 2
    }
    Write-Host "[OK] 다운로드 완료: $installerPath"
} else {
    Write-Host "[OK] 인스톨러 이미 다운로드됨: $installerPath"
}

# ---- 3) silent install ----------------------------------------------------
Write-Host ""
Write-Host "[2/3] silent install 실행 중 ... (5~10분, 콘솔 응답 없음)"

# EDB unattended 옵션 참고: https://www.enterprisedb.com/docs/supported-open-source/postgresql/installer/03_using_installer_unattended_mode/
$installerArgs = @(
    "--mode", "unattended",
    "--unattendedmodeui", "none",
    "--superpassword", $PostgresPassword,
    "--servicename", "postgresql-x64-$PgVersion",
    "--serviceaccount", "postgres",
    "--servicepassword", $PostgresPassword,
    "--prefix", "`"$InstallDir`"",
    "--datadir", "`"$dataDir`"",
    "--serverport", "$Port",
    "--locale", "default",
    "--disable-components", "stackbuilder,pgAdmin"
)

$proc = Start-Process -FilePath $installerPath -ArgumentList $installerArgs -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    Write-Host "[ERROR] 인스톨러 종료 코드: $($proc.ExitCode)" -ForegroundColor Red
    Write-Host "        로그: $env:TEMP\install-postgresql.log" -ForegroundColor Yellow
    exit 3
}
Write-Host "[OK] 설치 완료"

# ---- 4) 서비스 시작 + Path 등록 -------------------------------------------
$serviceName = "postgresql-x64-$PgVersion"
$svc = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne "Running") {
        Start-Service $svc.Name
    }
    Set-Service -Name $svc.Name -StartupType Automatic
    Write-Host "[OK] 서비스 '$serviceName' 자동 시작 등록됨"
} else {
    Write-Host "[WARN] 서비스 '$serviceName' 검색 실패 — 수동 확인 필요" -ForegroundColor Yellow
}

# 시스템 Path 에 bin 추가 (이미 있으면 skip)
$binDir = Join-Path $InstallDir "bin"
$currentPath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($currentPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$binDir", "Machine")
    $env:Path = "$env:Path;$binDir"
    Write-Host "[OK] 시스템 Path 에 추가: $binDir"
} else {
    Write-Host "[OK] Path 에 이미 등록됨"
}

# ---- 5) 접속 검증 ----------------------------------------------------------
Write-Host ""
Write-Host "[3/3] psql 접속 검증 ..."
$env:PGPASSWORD = $PostgresPassword
$verify = & $psqlExe -U postgres -h localhost -p $Port -tAc "SELECT version();" 2>&1
$rc = $LASTEXITCODE
Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

if ($rc -ne 0) {
    Write-Host "[ERROR] psql 접속 실패: $verify" -ForegroundColor Red
    exit 4
}
Write-Host "[OK] PostgreSQL 응답:"
Write-Host "     $verify"

Write-Host ""
Write-Host "================================================================"
Write-Host " PostgreSQL $PgVersion 설치 완료"
Write-Host "================================================================"
Write-Host " 다음 단계: install_pgvector_windows.ps1 -PgVersion $PgVersion"
Write-Host "================================================================"

exit 0
