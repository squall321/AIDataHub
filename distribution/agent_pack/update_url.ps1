# Update the canonical API URL across the entire agent_pack folder.
# Replaces every occurrence of the current URL (stored in .api_url)
# with the new URL, then updates .api_url. Idempotent.
#
# Usage:
#   .\update_url.ps1 http://new-server:8000
#   .\update_url.ps1 -DryRun http://new-server:8000
#
# Pure PowerShell — no external dependencies.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$NewUrl,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$PackDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Marker    = Join-Path $PackDir ".api_url"
$Exts      = @(".md", ".py", ".sh", ".ts", ".js", ".json", ".txt", ".yaml", ".yml")
$Exclude   = @("update_url.py", "update_url.ps1", ".api_url")

if (-not (Test-Path $Marker)) {
    Write-Error "marker file missing: $Marker  (create it with the current URL)"
    exit 1
}

$OldUrl = (Get-Content $Marker -Raw -Encoding UTF8).Trim()
$NewUrl = $NewUrl.TrimEnd("/")

if ($OldUrl -eq $NewUrl) {
    Write-Host "Already at $NewUrl — nothing to do."
    exit 0
}

Write-Host "OLD: $OldUrl"
Write-Host "NEW: $NewUrl"
if ($DryRun) { Write-Host "(dry-run — no writes)" }
Write-Host ""

$Changed = @()
$Total   = 0

Get-ChildItem $PackDir -Recurse -File | ForEach-Object {
    if ($Exclude -contains $_.Name) { return }
    if ($Exts -notcontains $_.Extension.ToLower()) { return }

    try {
        $text = Get-Content $_.FullName -Raw -Encoding UTF8
    } catch {
        return
    }
    if ($null -eq $text) { return }

    # Count occurrences (PowerShell strings don't have a count method,
    # use IndexOf in a loop or Regex.Matches for accuracy).
    $count = ([regex]::Matches($text, [regex]::Escape($OldUrl))).Count
    if ($count -eq 0) { return }

    if (-not $DryRun) {
        $newText = $text.Replace($OldUrl, $NewUrl)
        Set-Content -Path $_.FullName -Value $newText -Encoding UTF8 -NoNewline
    }
    $rel = $_.FullName.Substring($PackDir.Length + 1)
    $Changed += [pscustomobject]@{ Count = $count; Path = $rel }
    $Total += $count
}

if (-not $DryRun) {
    Set-Content -Path $Marker -Value ($NewUrl + "`n") -Encoding UTF8 -NoNewline
}

if ($Changed.Count -eq 0) {
    Write-Host "No files contained the old URL."
    exit 0
}

Write-Host "Replaced $Total occurrences across $($Changed.Count) file(s):"
$Changed | ForEach-Object {
    Write-Host ("  {0,3}× {1}" -f $_.Count, $_.Path)
}

if ($DryRun) {
    Write-Host ""
    Write-Host "[dry-run] no writes performed. Run without -DryRun to apply."
}
