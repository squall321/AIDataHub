# ============================================================
#  install_pgvector_windows.ps1
#  PostgreSQL 16/17/18 (Windows x64) 에 pgvector 확장을 자동 설치한다.
#
#  사용법:
#    1) 관리자 권한 PowerShell 실행
#    2) 실행 정책 임시 허용:
#         Set-ExecutionPolicy -Scope Process Bypass
#    3) 이 스크립트 실행:
#         .\install_pgvector_windows.ps1
#         .\install_pgvector_windows.ps1 -PgVersion 18
#         .\install_pgvector_windows.ps1 -BinaryUrl "https://example.com/pgvector-pg18.zip"
#         .\install_pgvector_windows.ps1 -PostgresPassword "비밀번호"
#         .\install_pgvector_windows.ps1 -SkipExtensionTest
#
#  사전 준비 (자동 다운로드 실패 시):
#    https://github.com/pgvector/pgvector/releases 또는 community 빌드에서
#    PG 버전과 일치하는 Windows x64 binary zip 을 받아 이 스크립트와 같은
#    폴더에 둔다. 파일명 예:
#       pgvector-pg18-windows-x64.zip
#       pgvector-pg17-windows-x64.zip
#       pgvector-pg16-windows-x64.zip
#    스크립트가 위 이름 패턴을 자동 감지한다.
#
#  zip 내부 구조 가정:
#    lib\vector.dll
#    share\extension\vector.control
#    share\extension\vector--*.sql
#  (또는 같은 파일들이 zip 루트에 평탄하게 들어 있어도 자동 분류한다)
# ============================================================

[CmdletBinding()]
param(
    [int]   $PgVersion          = 0,
    [string]$BinaryUrl          = "",
    [string]$PostgresPassword   = "",
    [string]$Database           = "ai_data",
    [switch]$SkipExtensionTest
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
    Write-Host "         .\install_pgvector_windows.ps1"
    exit 1
}
Write-OK "관리자 권한 확인됨"

# ------------------------------------------------------------
# 1) PostgreSQL 설치 위치 자동 감지
# ------------------------------------------------------------
$pgRoot = "C:\Program Files\PostgreSQL"
$candidates = @(18, 17, 16)
$detected = @()
foreach ($v in $candidates) {
    $bin = Join-Path $pgRoot "$v\bin\psql.exe"
    if (Test-Path $bin) { $detected += $v }
}

if ($detected.Count -eq 0) {
    Write-Err "PostgreSQL 16/17/18 을 '$pgRoot\<버전>' 에서 찾지 못했다."
    Write-Host ""
    Write-Host "  먼저 PostgreSQL 을 설치하라:"
    Write-Host "    .\install_postgres_windows.ps1"
    Write-Host "  또는 https://www.postgresql.org/download/windows/ 에서 직접 설치."
    exit 1
}
Write-OK ("감지된 PostgreSQL 버전: " + ($detected -join ", "))

# 버전 결정 (-PgVersion 우선, 없으면 가장 높은 버전)
if ($PgVersion -gt 0) {
    if ($detected -notcontains $PgVersion) {
        Write-Err "지정한 버전 PG $PgVersion 이 설치되어 있지 않다 (감지: $($detected -join ', '))."
        exit 1
    }
} else {
    $PgVersion = $detected[0]   # 가장 높은 버전
    if ($detected.Count -gt 1) {
        Write-Info "여러 버전 감지 → 가장 높은 PG $PgVersion 선택 (다른 버전은 -PgVersion 지정)"
    }
}
$pgHome = Join-Path $pgRoot "$PgVersion"
$pgBin  = Join-Path $pgHome "bin"
$pgLib  = Join-Path $pgHome "lib"
$pgExt  = Join-Path $pgHome "share\extension"
$psql   = Join-Path $pgBin "psql.exe"
Write-OK "대상 PostgreSQL: $pgHome"

# ------------------------------------------------------------
# 2) 작업 디렉터리 + binary 확보 (자동 다운로드 → 폴백 → 로컬 zip)
# ------------------------------------------------------------
$workDir = Join-Path $env:TEMP "pgvector_install"
if (Test-Path $workDir) { Remove-Item $workDir -Recurse -Force }
New-Item -ItemType Directory -Path $workDir | Out-Null

$localZipName = "pgvector-pg$PgVersion-windows-x64.zip"
$localZipPath = Join-Path $PSScriptRoot $localZipName
$downloadedZip = Join-Path $workDir $localZipName

# Community / 알려진 후보 URL — 변동 가능하므로 변수로
# (공식 pgvector 는 source-only 이므로 사용자 제공 binary 또는 community fork 의존)
$urlCandidates = @()
if ($BinaryUrl) { $urlCandidates += $BinaryUrl }
$urlCandidates += @(
    # 공식이 binary 를 발행하지 않으므로, 추후 사용자가 신뢰 fork 를 -BinaryUrl 로 넘긴다.
    # 자리표시자로 두어 자동 시도 → 실패 → 로컬 zip 경로로 폴백.
)

$zipPath = $null

# 2-A) 같은 폴더에 미리 받아둔 zip 우선 사용
if (Test-Path $localZipPath) {
    Write-OK "로컬 zip 발견: $localZipPath"
    $zipPath = $localZipPath
}

# 2-B) URL 시도
if (-not $zipPath -and $urlCandidates.Count -gt 0) {
    foreach ($u in $urlCandidates) {
        try {
            Write-Info "binary 다운로드 시도: $u"
            Invoke-WebRequest -Uri $u -OutFile $downloadedZip -UseBasicParsing -TimeoutSec 60
            if ((Get-Item $downloadedZip).Length -gt 1024) {
                Write-OK "다운로드 완료: $downloadedZip"
                $zipPath = $downloadedZip
                break
            } else {
                Write-Warn2 "다운로드 결과가 너무 작다 (1KB 미만) → 다음 후보 시도"
            }
        } catch {
            Write-Warn2 "다운로드 실패: $($_.Exception.Message)"
        }
    }
}

# 2-C) 모두 실패 → 안내 후 종료
if (-not $zipPath) {
    Write-Err "pgvector binary 를 확보하지 못했다."
    Write-Host ""
    Write-Host "  해결 방법 (택 1):"
    Write-Host "    A) 신뢰할 수 있는 source 에서 PG $PgVersion 용 Windows x64 binary zip 을"
    Write-Host "       이 스크립트와 같은 폴더에 다음 이름으로 둔 뒤 재실행:"
    Write-Host "         $localZipName"
    Write-Host "       (zip 안에 vector.dll, vector.control, vector--*.sql 포함)"
    Write-Host ""
    Write-Host "    B) URL 을 직접 지정해 재실행:"
    Write-Host "         .\install_pgvector_windows.ps1 -BinaryUrl 'https://.../pgvector.zip'"
    Write-Host ""
    Write-Host "    C) Visual Studio Build Tools 설치 후 source 빌드 (고급):"
    Write-Host "         git clone https://github.com/pgvector/pgvector.git"
    Write-Host "         cd pgvector"
    Write-Host "         set `"PGROOT=$pgHome`""
    Write-Host "         nmake /F Makefile.win"
    Write-Host "         nmake /F Makefile.win install"
    Write-Host ""
    Write-Host "  참고: https://github.com/pgvector/pgvector#windows"
    exit 1
}

# ------------------------------------------------------------
# 3) zip 압축 해제
# ------------------------------------------------------------
$extractDir = Join-Path $workDir "extracted"
New-Item -ItemType Directory -Path $extractDir | Out-Null
try {
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
    Write-OK "압축 해제: $extractDir"
} catch {
    Write-Err "zip 압축 해제 실패: $($_.Exception.Message)"
    exit 1
}

# zip 내부 구조 자동 탐지
$dllFile = Get-ChildItem -Path $extractDir -Recurse -Filter "vector.dll"        | Select-Object -First 1
$ctlFile = Get-ChildItem -Path $extractDir -Recurse -Filter "vector.control"    | Select-Object -First 1
$sqlFiles = Get-ChildItem -Path $extractDir -Recurse -Filter "vector--*.sql"

if (-not $dllFile -or -not $ctlFile -or $sqlFiles.Count -eq 0) {
    Write-Err "zip 안에서 필요한 파일을 찾지 못했다."
    Write-Host "  필요 파일: vector.dll, vector.control, vector--*.sql (1개 이상)"
    Write-Host "  발견: vector.dll=$($null -ne $dllFile), vector.control=$($null -ne $ctlFile), sql=$($sqlFiles.Count)개"
    exit 1
}
Write-OK "binary 구성 확인: dll 1개, control 1개, sql $($sqlFiles.Count)개"

# ------------------------------------------------------------
# 4) 파일 복사 (관리자 권한 필요)
# ------------------------------------------------------------
try {
    Copy-Item $dllFile.FullName -Destination $pgLib -Force
    Write-OK "복사: vector.dll → $pgLib"

    Copy-Item $ctlFile.FullName -Destination $pgExt -Force
    Write-OK "복사: vector.control → $pgExt"

    foreach ($s in $sqlFiles) {
        Copy-Item $s.FullName -Destination $pgExt -Force
    }
    Write-OK "복사: vector--*.sql ($($sqlFiles.Count)개) → $pgExt"
} catch {
    Write-Err "파일 복사 실패: $($_.Exception.Message)"
    Write-Host "  관리자 권한으로 다시 실행하라."
    exit 1
}

# ------------------------------------------------------------
# 5) PostgreSQL 서비스 재시작
# ------------------------------------------------------------
$svcName = "postgresql-x64-$PgVersion"
$svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
if (-not $svc) {
    # 최신 installer 가 다른 이름을 쓰는 경우가 있어 부분 매칭 한 번 더 시도
    $svc = Get-Service -Name "postgresql*$PgVersion*" -ErrorAction SilentlyContinue | Select-Object -First 1
}

if (-not $svc) {
    Write-Warn2 "서비스 '$svcName' 을 찾지 못했다. 수동 재시작 필요."
} else {
    try {
        Restart-Service -Name $svc.Name -Force
        Write-OK "PostgreSQL 서비스 재시작: $($svc.Name)"
        Start-Sleep -Seconds 2
    } catch {
        Write-Warn2 "서비스 재시작 실패: $($_.Exception.Message)"
        Write-Host "  수동: services.msc → $($svc.Name) 재시작"
    }
}

# ------------------------------------------------------------
# 6) CREATE EXTENSION 검증
# ------------------------------------------------------------
if ($SkipExtensionTest) {
    Write-Info "CREATE EXTENSION 검증 생략 (-SkipExtensionTest)"
} else {
    if ($PostgresPassword) { $env:PGPASSWORD = $PostgresPassword }

    Write-Info "CREATE EXTENSION 검증 (DB: $Database)"
    Write-Host "  비밀번호 입력 창이 뜨면 postgres 사용자 비밀번호 입력 (또는 -PostgresPassword 옵션 사용)"

    # 1단계: ai_data DB 가 있는지 확인. 없으면 postgres DB 에 시도.
    $targetDb = $Database
    try {
        $exists = & $psql -U postgres -h localhost -tAc "SELECT 1 FROM pg_database WHERE datname='$Database'" 2>$null
        if (-not ($exists -match "1")) {
            Write-Warn2 "DB '$Database' 가 없어 'postgres' DB 에서 검증한다."
            $targetDb = "postgres"
        }
    } catch {
        $targetDb = "postgres"
    }

    & $psql -U postgres -h localhost -d $targetDb -c "CREATE EXTENSION IF NOT EXISTS vector;"
    if ($LASTEXITCODE -eq 0) {
        Write-OK "pgvector 확장 활성화 성공 (DB: $targetDb)"
        & $psql -U postgres -h localhost -d $targetDb -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
    } else {
        Write-Err "CREATE EXTENSION 실패 (rc=$LASTEXITCODE)"
        Write-Host "  원인 후보:"
        Write-Host "    - postgres 비밀번호 불일치"
        Write-Host "    - 서비스 재시작이 안 됐다 (services.msc 확인)"
        Write-Host "    - vector.dll 이 PG 버전과 ABI 비호환"
        exit 1
    }

    if ($PostgresPassword) { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
}

# ------------------------------------------------------------
# 7) 마무리
# ------------------------------------------------------------
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " pgvector 설치 완료 (PostgreSQL $PgVersion)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host " 다음 단계:"
Write-Host "   cd ..\api_server"
Write-Host "   .\setup.bat"
Write-Host ""
