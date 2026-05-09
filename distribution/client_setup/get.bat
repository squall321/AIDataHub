@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
    echo 사용법: get.bat ^<record_id^>
    echo   예: get.bat DOC-HE-CAE-2026-001001
    exit /b 1
)

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0lib\get.ps1" -RecordId "%~1"
