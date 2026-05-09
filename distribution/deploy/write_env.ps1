# ===========================================================================
# AI Data Hub — api_server\.env 생성 헬퍼
#
# SERVER_QUICK_SETUP.bat 가 호출. 비밀번호는 환경변수 AIDH_PG_PW 로 받음
# (cmd 인자 escape 문제 회피).
# ===========================================================================
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvPath
)

$ErrorActionPreference = "Stop"

$pw = $env:AIDH_PG_PW
if (-not $pw) {
    Write-Host "[ERROR] AIDH_PG_PW 환경변수 없음" -ForegroundColor Red
    exit 1
}

# .env 형식: 값 안의 특수문자(공백, #, =) 가 있어도 문제없음.
# DATABASE_URL 만 비번 노출 — 비번에 @, /, : 가 있으면 URL encoding 필요.
Add-Type -AssemblyName System.Web -ErrorAction SilentlyContinue
$pwEncoded = if ([System.Web.HttpUtility]) {
    [System.Web.HttpUtility]::UrlEncode($pw)
} else {
    [System.Uri]::EscapeDataString($pw)
}

$lines = @(
    "# PostgreSQL connection",
    "DATABASE_URL=postgresql+asyncpg://postgres:${pwEncoded}@localhost:5432/ai_data",
    "",
    "# API server",
    "API_HOST=0.0.0.0",
    "API_PORT=8000",
    "API_RELOAD=false",
    "",
    "# Logging",
    "LOG_LEVEL=INFO",
    "LOG_FORMAT=json",
    "",
    "# Auth (initial: open). 운영 시 true 로 변경.",
    "AUTH_REQUIRED=false",
    "BOOTSTRAP_API_KEY=",
    "",
    "# Embedding (hash | openai)",
    "EMBEDDING_PROVIDER=hash"
)

# UTF-8 BOM 없이 저장 (Python dotenv 파서가 BOM 에 민감할 수 있음)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($EnvPath, ($lines -join "`r`n") + "`r`n", $utf8NoBom)

exit 0
