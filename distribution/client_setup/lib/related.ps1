param(
    [Parameter(Mandatory)][string]$RecordId,
    [string]$Mode = "semantic",
    [int]$Limit = 5
)
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host "[related] record=$RecordId mode=$Mode limit=$Limit" -ForegroundColor Cyan
$path = "/api/records/$RecordId/related?mode=$Mode&limit=$Limit"
$resp = Invoke-Aidh -Path $path
$resp | ConvertTo-Json -Depth 10
