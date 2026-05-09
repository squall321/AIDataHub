@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%~2"=="" (
    echo 사용법: search.bat ^<mode^> "키워드" [limit]
    echo   mode: semantic ^| fts ^| tag ^| keyword
    echo   예:  search.bat semantic "stress strain"
    echo        search.bat fts "낙하 시뮬레이션" 10
    echo        search.bat tag "IGA,NURBS"
    exit /b 1
)

set "limit=%~3"
if "%limit%"=="" set "limit=5"

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0lib\search.ps1" -Mode "%~1" -Query "%~2" -Limit %limit%
