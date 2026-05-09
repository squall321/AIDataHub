# =============================================================================
# AI Data Hub 클라이언트 공용 헬퍼
#   - config.ini 읽기
#   - REST 호출 (X-API-Key 자동 첨부, 에러 코드별 메시지)
# =============================================================================

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# config.ini 위치 — lib/ 의 부모 폴더
$script:ROOT_DIR = Split-Path -Parent $PSScriptRoot
$script:CONFIG_PATH = Join-Path $script:ROOT_DIR "config.ini"

function Get-Config {
    if (-not (Test-Path $script:CONFIG_PATH)) {
        Write-Host "ERROR: config.ini 가 없다." -ForegroundColor Red
        Write-Host "  config.example.ini 를 config.ini 로 복사한 뒤 base_url / api_key 를 채워라." -ForegroundColor Yellow
        exit 1
    }

    $cfg = @{}
    $section = ""
    Get-Content $script:CONFIG_PATH -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line.StartsWith(";") -or $line.StartsWith("#") -or $line -eq "") {
            return
        }
        if ($line -match "^\[(.+)\]$") {
            $section = $matches[1]
            $cfg[$section] = @{}
        } elseif ($section -and ($line -match "^([^=]+)=(.*)$")) {
            $k = $matches[1].Trim()
            $v = $matches[2].Trim()
            $cfg[$section][$k] = $v
        }
    }

    # 필수 필드 검증
    if (-not $cfg.server -or -not $cfg.server.base_url -or -not $cfg.server.api_key) {
        Write-Host "ERROR: config.ini 에 [server] base_url / api_key 가 비어있다." -ForegroundColor Red
        exit 1
    }
    if ($cfg.server.api_key -eq "paste-your-api-key-here") {
        Write-Host "ERROR: config.ini 의 api_key 를 실제 발급받은 키로 바꿔라." -ForegroundColor Red
        exit 1
    }
    return $cfg
}

function Get-AuthHeaders {
    $cfg = Get-Config
    return @{ "X-API-Key" = $cfg.server.api_key }
}

function Get-BaseUrl {
    $cfg = Get-Config
    return $cfg.server.base_url.TrimEnd("/")
}

function Invoke-Aidh {
    param(
        [string]$Method = "GET",
        [Parameter(Mandatory)][string]$Path,
        [hashtable]$Body = $null,
        [string]$OutFile = $null
    )

    $url = (Get-BaseUrl) + $Path
    $headers = Get-AuthHeaders

    try {
        $params = @{
            Method  = $Method
            Uri     = $url
            Headers = $headers
        }
        if ($Body) {
            $params.ContentType = "application/json; charset=utf-8"
            $params.Body = ($Body | ConvertTo-Json -Depth 10 -Compress)
        }
        if ($OutFile) {
            $params.OutFile = $OutFile
        }
        return Invoke-RestMethod @params
    } catch {
        $err = $_.Exception
        $code = 0
        if ($err.Response -and $err.Response.StatusCode) {
            $code = [int]$err.Response.StatusCode
        }
        switch ($code) {
            401 { Write-Host "ERROR 401: 인증 실패 — config.ini 의 api_key 확인" -ForegroundColor Red }
            403 { Write-Host "ERROR 403: 권한 부족 — 운영자에게 scope 확인 요청" -ForegroundColor Red }
            404 { Write-Host "ERROR 404: 자원 없음 — id / 경로 확인" -ForegroundColor Yellow }
            422 { Write-Host "ERROR 422: 검증 실패 — 응답의 detail 확인" -ForegroundColor Yellow }
            429 { Write-Host "ERROR 429: 너무 많은 요청 — 잠시 후 재시도" -ForegroundColor Yellow }
            500 { Write-Host "ERROR 500: 서버 오류 — 운영자에게 로그 확인 요청" -ForegroundColor Red }
            503 { Write-Host "ERROR 503: 서비스 미준비 — embedding 백필 진행 중일 수 있음. fts 모드로 폴백" -ForegroundColor Yellow }
            default {
                Write-Host "ERROR: $($err.Message)" -ForegroundColor Red
                Write-Host "  URL: $url" -ForegroundColor DarkGray
            }
        }
        exit 1
    }
}

function Show-ResponseSummary {
    param([Parameter(ValueFromPipeline)]$Resp)
    process {
        if ($null -eq $Resp) { return }
        $Resp | ConvertTo-Json -Depth 10
    }
}
