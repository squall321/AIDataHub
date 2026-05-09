@echo off
REM ============================================================
REM  AI Data Hub - Windows native 설치 wrapper
REM
REM  api_server\setup.bat 을 호출한다 (PostgreSQL 별도 설치 가정).
REM  Docker 사용 시에는 install.bat 사용.
REM ============================================================
setlocal
chcp 65001 > nul
cd /d "%~dp0"

set "API_DIR=%~dp0..\api_server"

if not exist "%API_DIR%\setup.bat" (
    echo [ERROR] %API_DIR%\setup.bat 가 없다.
    exit /b 1
)

REM venv 가 없으면 미리 생성 (setup.bat 의 사전조건)
if not exist "%API_DIR%\.venv\Scripts\python.exe" (
    echo [INFO] .venv 가 없어 새로 생성한다.
    where py > nul 2>&1
    if errorlevel 1 (
        echo [ERROR] py 런처가 없다. Python 3.12 설치 후 재시도.
        exit /b 1
    )
    pushd "%API_DIR%"
    py -3.12 -m venv .venv
    popd
)

echo [INFO] api_server\setup.bat 위임 실행...
pushd "%API_DIR%"
call setup.bat
set "RC=%errorlevel%"
popd

if not "%RC%"=="0" (
    echo [ERROR] setup.bat 실패 (rc=%RC%)
    exit /b %RC%
)

echo.
echo ============================================================
echo  Native 셋업 완료
echo ============================================================
echo  서버 실행: cd ..\api_server ^&^& run.bat
echo ============================================================

endlocal
