@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
    echo 사용법: ask.bat "질문 내용"
    echo   예: ask.bat "AI 도입 현황은?"
    exit /b 1
)

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0lib\ask.ps1" -Query "%~1"
