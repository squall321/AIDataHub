param(
    [Parameter(Mandatory)][string]$Mode,
    [Parameter(Mandatory)][string]$Query,
    [int]$Limit = 5
)
. (Join-Path $PSScriptRoot "_common.ps1")

$validModes = @("semantic", "fts", "tag", "keyword")
if ($validModes -notcontains $Mode) {
    Write-Host "ERROR: mode 는 semantic / fts / tag / keyword 중 하나" -ForegroundColor Red
    exit 1
}

Add-Type -AssemblyName System.Web
$enc = [System.Web.HttpUtility]::UrlEncode($Query)
$path = "/api/search?mode=$Mode&q=$enc&limit=$Limit"

Write-Host "[search] mode=$Mode q='$Query' limit=$Limit" -ForegroundColor Cyan
$resp = Invoke-Aidh -Path $path
$resp | ConvertTo-Json -Depth 10
