@echo off
REM ============================================================
REM  AI Data Hub - Windows 원터치 셋업 (Docker Desktop)
REM
REM  사용:
REM    cd deploy
REM    install.bat
REM
REM  동작:
REM    1) Docker / docker compose v2 검증
REM    2) .env 가 없으면 .env.example 복사
REM    3) docker compose up -d --build
REM    4) /api/system/health 응답까지 대기 (최대 60초)
REM    5) 안내 메시지 출력
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo ============================================================
echo  AI Data Hub - install.bat
echo ============================================================
echo  deploy dir : %CD%
echo ============================================================

REM --- 1) Docker 검증 ----------------------------------------------------
where docker > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker 가 PATH 에 없습니다.
    echo         Docker Desktop for Windows 설치 필요:
    echo         https://docs.docker.com/desktop/install/windows-install/
    exit /b 1
)
docker compose version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker compose v2 가 필요합니다.
    exit /b 1
)
echo [OK]    Docker / compose v2 확인됨

REM --- 2) .env 준비 ------------------------------------------------------
if not exist ".env" (
    copy .env.example .env > nul
    echo [INFO]  .env 생성됨 - 운영 전 비밀번호/포트 수정 권장
)

REM .env 에서 API_PORT 추출 (헬스체크 URL 에 사용)
set "API_PORT=8000"
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if /i "%%a"=="API_PORT" set "API_PORT=%%b"
)
echo [OK]    .env 로드됨 (API_PORT=!API_PORT!)

REM --- 3) compose up -----------------------------------------------------
echo.
echo [1/3] PostgreSQL + API 빌드 + 기동...
docker compose up -d --build
if errorlevel 1 (
    echo [ERROR] docker compose up 실패
    exit /b 1
)
echo [OK]    컨테이너 기동 명령 완료

REM --- 4) 헬스체크 -------------------------------------------------------
echo.
echo [2/3] /api/system/health 응답 대기 (최대 60초)...
set "HEALTH_URL=http://localhost:!API_PORT!/api/system/health"
set "SUCCESS=0"
for /l %%i in (1,1,30) do (
    curl -sf -o nul "!HEALTH_URL!" > nul 2>&1
    if !errorlevel! == 0 (
        set "SUCCESS=1"
        goto :health_done
    )
    timeout /t 2 /nobreak > nul
)
:health_done
if "!SUCCESS!"=="1" (
    echo [OK]    API 응답 확인: !HEALTH_URL!
) else (
    echo [WARN]  60초 안에 응답 없음 - 'docker compose logs api' 로 진단 권장
)

REM --- 5) 안내 -----------------------------------------------------------
echo.
echo [3/3] 안내
echo.
echo ============================================================
echo  셋업 완료
echo ============================================================
echo  API        : http://localhost:!API_PORT!
echo  헬스체크   : http://localhost:!API_PORT!/api/system/health
echo  API docs   : http://localhost:!API_PORT!/docs
echo  discover   : http://localhost:!API_PORT!/api/discover
echo.
echo  로그       : docker compose logs -f api
echo  재시작     : docker compose restart api
echo  종료       : docker compose down
echo  데이터삭제 : docker compose down -v
echo ============================================================

endlocal
