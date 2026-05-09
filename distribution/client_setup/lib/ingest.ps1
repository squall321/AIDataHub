param([Parameter(Mandatory)][string]$FilePath)
. (Join-Path $PSScriptRoot "_common.ps1")

if (-not (Test-Path $FilePath)) {
    Write-Host "ERROR: 파일 없음 — $FilePath" -ForegroundColor Red
    exit 1
}

$cfg = Get-Config
$url = (Get-BaseUrl) + "/api/convert/ingest"
$headers = Get-AuthHeaders

Write-Host "[ingest] $FilePath" -ForegroundColor Cyan
Write-Host "         division=$($cfg.upload.division), team=$($cfg.upload.team), year=$($cfg.upload.year), seq=$($cfg.upload.seq)"

try {
    $form = @{
        file     = Get-Item $FilePath
        division = $cfg.upload.division
        team     = $cfg.upload.team
        year     = $cfg.upload.year
        seq      = $cfg.upload.seq
    }
    $resp = Invoke-RestMethod -Method POST -Uri $url -Headers $headers -Form $form
    $resp | ConvertTo-Json -Depth 10
} catch {
    $code = 0
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
        $code = [int]$_.Exception.Response.StatusCode
    }
    Write-Host "ERROR $code : $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
