@echo off
REM ============================================================
REM  AI Data API Server — Run
REM
REM  setup.bat 실행 후 사용. uvicorn 으로 API 서버 기동.
REM  Ctrl+C 로 종료.
REM ============================================================

setlocal
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 가 없습니다. 먼저 setup.bat 을 실행하세요.
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env 가 없습니다. 먼저 setup.bat 을 실행하세요.
    exit /b 1
)

call .venv\Scripts\activate.bat
set PYTHONPATH=src

echo.
echo ============================================================
echo  AI Data API Server 시작
echo ============================================================
echo  URL: http://localhost:8000
echo  Docs: http://localhost:8000/docs
echo  Metrics: http://localhost:8000/metrics
echo  Stop: Ctrl+C
echo ============================================================
echo.

python -m api.main

endlocal
