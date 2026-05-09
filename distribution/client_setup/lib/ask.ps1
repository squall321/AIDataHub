param([Parameter(Mandatory)][string]$Query)
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host "[ask] $Query" -ForegroundColor Cyan
$resp = Invoke-Aidh -Method POST -Path "/api/ask" -Body @{ q = $Query }
$resp | ConvertTo-Json -Depth 10
