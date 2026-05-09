. (Join-Path $PSScriptRoot "_common.ps1")

$cfg = Get-Config
$size = $cfg.model.size.ToUpper()
$guideFile = Join-Path $script:ROOT_DIR (Join-Path $cfg.output.guide_dir ("AGENT_API_GUIDE_$size.md"))

if (-not (Test-Path $guideFile)) {
    Write-Host "가이드 파일이 없다 — setup.bat 를 먼저 실행하라." -ForegroundColor Yellow
    Write-Host "  찾는 위치: $guideFile" -ForegroundColor DarkGray
    exit 1
}

Get-Content $guideFile -Encoding UTF8
