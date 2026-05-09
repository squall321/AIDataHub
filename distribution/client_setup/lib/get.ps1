param([Parameter(Mandatory)][string]$RecordId)
. (Join-Path $PSScriptRoot "_common.ps1")

Write-Host "[get] $RecordId" -ForegroundColor Cyan
$resp = Invoke-Aidh -Path "/api/records/$RecordId"
$resp | ConvertTo-Json -Depth 10
