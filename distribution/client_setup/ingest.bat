@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
    echo 사용법: ingest.bat "^<file_path^>"
    echo   지원: .docx .xlsx .pptx .md .pdf .html
    echo   예:   ingest.bat "C:\Users\me\Documents\report.docx"
    exit /b 1
)

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0lib\ingest.ps1" -FilePath "%~1"
