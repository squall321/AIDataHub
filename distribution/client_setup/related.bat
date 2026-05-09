@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
    echo 사용법: related.bat ^<record_id^> [mode] [limit]
    echo   mode: semantic ^| tag ^(default semantic^)
    echo   예: related.bat DOC-HE-CAE-2026-001001
    exit /b 1
)

set "mode=%~2"
if "%mode%"=="" set "mode=semantic"
set "limit=%~3"
if "%limit%"=="" set "limit=5"

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0lib\related.ps1" -RecordId "%~1" -Mode "%mode%" -Limit %limit%
