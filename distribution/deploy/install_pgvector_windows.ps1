# ===========================================================================
# AI Data Hub — pgvector 자동 설치 (Windows, PG 16/17/18)
#
# 사용:
#   .\install_pgvector_windows.ps1 -PgVersion 18
#
# 옵션:
#   -PgVersion 18 | 17 | 16     기본 18
#   -PostgresPassword "..."     CREATE EXTENSION 검증용 (옵션)
#   -VendorZip "<경로>"         기본 .\vendor\pgvector-pg18-windows-x64.zip
#
# 동작:
#   1) PG 설치 경로 확인 (C:\Program Files\PostgreSQL\<ver>)
#   2) vendor zip 압축 해제 (lib\vector.dll, share\extension\*)
#   3) 두 파일군을 PG 디렉터리에 복사 (관리자 권한 필요)
#   4) 비번 주어졌으면 ai_data DB 에 CREATE EXTENSION vector 시도
#
# 종료 코드:
#   0 성공
#   1 파라미터/검증 실패 (PG 미설치 등)
#   2 vendor zip 미존재 / 손상
#   3 파일 복사 실패
#   4 CREATE EXTENSION 실패 (비번 주어진 경우만)
#
# 참고:
#   현재 vendor zip 은 PG 18 용 사전 빌드 binary (배포 패키지에 포함됨).
#   PG 16/17 사용 시:
#     - 같은 zip 으로 보통 동작하지만 ABI 불일치 가능
#     - 운영자가 PG 16/17 환경에서 별도로 빌드한 vendor zip 을 준비해서
#       deploy/vendor/ 폴더에 두고 -VendorZip 옵션으로 지정.
# ===========================================================================
[CmdletBinding()]
param(
    [ValidateSet("16", "17", "18")]
    [string]$PgVersion = "18",

    [string]$PostgresPassword = "",

    [string]$VendorZip = ""
)

$ErrorActionPreference = "Stop"

# ---- 관리자 권한 체크 ------------------------------------------------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] 관리자 권한이 필요합니다 (PG 디렉터리 쓰기)." -ForegroundColor Red
    exit 1
}

$pgDir = "C:\Program Files\PostgreSQL\$PgVersion"
if (-not (Test-Path $pgDir)) {
    Write-Host "[ERROR] PostgreSQL $PgVersion 미설치: $pgDir" -ForegroundColor Red
    Write-Host "        먼저 install_postgres_windows.ps1 실행." -ForegroundColor Yellow
    exit 1
}

if (-not $VendorZip) {
    $VendorZip = Join-Path $PSScriptRoot "vendor\pgvector-pg18-windows-x64.zip"
}
if (-not (Test-Path $VendorZip)) {
    Write-Host "[ERROR] vendor zip 미존재: $VendorZip" -ForegroundColor Red
    exit 2
}

if ($PgVersion -ne "18") {
    Write-Host "[WARN] vendor zip 은 PG 18 사전 빌드 — PG $PgVersion 에 적용합니다." -ForegroundColor Yellow
    Write-Host "       ABI 불일치로 CREATE EXTENSION 실패 시 PG $PgVersion 환경의 별도 vendor zip 필요." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "================================================================"
Write-Host " pgvector 설치 (PG $PgVersion)"
Write-Host "================================================================"
Write-Host " PG 경로     : $pgDir"
Write-Host " Vendor zip  : $VendorZip"
Write-Host "================================================================"

# ---- 1) 압축 해제 ----------------------------------------------------------
$tempExtract = Join-Path $env:TEMP "pgvector-extract-$([System.Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $tempExtract -Force | Out-Null

try {
    Write-Host ""
    Write-Host "[1/3] 압축 해제 중 ..."
    Expand-Archive -Path $VendorZip -DestinationPath $tempExtract -Force
} catch {
    Write-Host "[ERROR] 압축 해제 실패: $($_.Exception.Message)" -ForegroundColor Red
    Remove-Item -Recurse -Force $tempExtract -ErrorAction SilentlyContinue
    exit 2
}

$srcDll = Join-Path $tempExtract "lib\vector.dll"
$srcExt = Join-Path $tempExtract "share\extension"

if (-not (Test-Path $srcDll)) {
    Write-Host "[ERROR] vector.dll 누락 ($srcDll)" -ForegroundColor Red
    Remove-Item -Recurse -Force $tempExtract -ErrorAction SilentlyContinue
    exit 2
}
if (-not (Test-Path $srcExt)) {
    Write-Host "[ERROR] share\extension 누락 ($srcExt)" -ForegroundColor Red
    Remove-Item -Recurse -Force $tempExtract -ErrorAction SilentlyContinue
    exit 2
}
Write-Host "[OK] 압축 해제 완료"

# ---- 2) PG 디렉터리에 복사 -------------------------------------------------
$dstLib = Join-Path $pgDir "lib"
$dstExt = Join-Path $pgDir "share\extension"

Write-Host ""
Write-Host "[2/3] 파일 복사 ..."
try {
    Copy-Item -Path $srcDll -Destination $dstLib -Force
    Get-ChildItem $srcExt -File | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $dstExt -Force
    }
} catch {
    Write-Host "[ERROR] 복사 실패: $($_.Exception.Message)" -ForegroundColor Red
    Remove-Item -Recurse -Force $tempExtract -ErrorAction SilentlyContinue
    exit 3
}
Remove-Item -Recurse -Force $tempExtract -ErrorAction SilentlyContinue
Write-Host "[OK] vector.dll → $dstLib"
Write-Host "[OK] vector.control + sql → $dstExt"

# ---- 3) CREATE EXTENSION 검증 (옵션) --------------------------------------
$psqlExe = Join-Path $pgDir "bin\psql.exe"
if ($PostgresPassword -and (Test-Path $psqlExe)) {
    Write-Host ""
    Write-Host "[3/3] CREATE EXTENSION 검증 ..."
    $env:PGPASSWORD = $PostgresPassword

    # ai_data DB 가 있으면 거기에, 없으면 postgres DB 에서 검증
    $hasAiData = & $psqlExe -U postgres -h localhost -tAc "SELECT 1 FROM pg_database WHERE datname='ai_data'" 2>$null
    $targetDb = if ($hasAiData -match "1") { "ai_data" } else { "postgres" }

    $out = & $psqlExe -U postgres -h localhost -d $targetDb -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1
    $rc = $LASTEXITCODE
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue

    if ($rc -ne 0) {
        Write-Host "[ERROR] CREATE EXTENSION 실패 (db=$targetDb): $out" -ForegroundColor Red
        Write-Host "        ABI 불일치 가능 — PG 18 이외 버전이라면 nmake 빌드 필요." -ForegroundColor Yellow
        exit 4
    }
    Write-Host "[OK] vector extension 활성화됨 (db=$targetDb)"
} else {
    Write-Host ""
    Write-Host "[3/3] (-PostgresPassword 미지정) CREATE EXTENSION 은 skip"
    Write-Host "      나중에 수동: psql -U postgres -d ai_data -c `"CREATE EXTENSION vector;`""
}

Write-Host ""
Write-Host "================================================================"
Write-Host " pgvector 설치 완료"
Write-Host "================================================================"

exit 0
