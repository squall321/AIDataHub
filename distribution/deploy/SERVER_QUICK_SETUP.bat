@echo off
REM ============================================================
REM  AI Data Hub - Windows 서버 원터치 셋업
REM
REM  사용:
REM    이 파일을 더블클릭. UAC 동의 후 PG 비밀번호 입력 1회.
REM    나머지는 자동.
REM
REM  자동화 항목:
REM    1) UAC elevation
REM    2) Python 3.12 검증
REM    3) PostgreSQL 18 자동 설치 (없을 때)
REM    4) pgvector 자동 설치 (vendor zip)
REM    5) api_server\.env 자동 생성
REM    6) venv + 의존성 + alembic + seed (api_server\setup.bat)
REM    7) uvicorn 백그라운드 기동
REM    8) /api/system/health 검증
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 > nul

REM --- 0) UAC 자동 elevation -------------------------------------------------
net session > nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] 관리자 권한이 필요합니다. UAC 동의 창이 뜹니다...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

cd /d "%~dp0"
set "DEPLOY_DIR=%~dp0"
REM 후행 백슬래시 제거
if "%DEPLOY_DIR:~-1%"=="\" set "DEPLOY_DIR=%DEPLOY_DIR:~0,-1%"

set "DIST_ROOT=%DEPLOY_DIR%\.."
set "API_DIR=%DEPLOY_DIR%\..\api_server"

echo.
echo ============================================================
echo  AI Data Hub - 서버 원터치 셋업
echo ============================================================
echo  deploy   : %DEPLOY_DIR%
echo  api_server : %API_DIR%
echo ============================================================
echo.

REM --- 1) api_server 디렉터리 검증 -------------------------------------------
if not exist "%API_DIR%\setup.bat" (
    echo [ERROR] %API_DIR%\setup.bat 가 없습니다.
    echo         배포 패키지 구조 확인: distribution\deploy + distribution\api_server
    goto :FAIL_PAUSE
)

REM --- 2) Python 3.12 검증 ---------------------------------------------------
where py > nul 2>&1
if errorlevel 1 (
    echo [ERROR] py 런처 없음. Python 3.12 설치 필요.
    echo         https://www.python.org/downloads/release/python-3128/
    echo         설치 시 "Add python.exe to PATH" 체크.
    goto :FAIL_PAUSE
)
py -3.12 --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python 3.12 미설치 (다른 버전만 있음).
    echo         https://www.python.org/downloads/release/python-3128/
    goto :FAIL_PAUSE
)
for /f "tokens=*" %%v in ('py -3.12 --version 2^>^&1') do echo [OK]    %%v

REM --- 3) PG 18 설치 검증 / 설치 ---------------------------------------------
REM 순서: PG 18 → 17 → 16 검색. 하나라도 있으면 그것 사용. 모두 없으면 18 신규 설치.
set "PG_VERSION="
set "PG_DIR="

if exist "C:\Program Files\PostgreSQL\18\bin\psql.exe" (
    set "PG_VERSION=18"
    set "PG_DIR=C:\Program Files\PostgreSQL\18"
    echo [OK]    기존 PostgreSQL 18 사용
    goto :PG_VER_DONE
)
if exist "C:\Program Files\PostgreSQL\17\bin\psql.exe" (
    set "PG_VERSION=17"
    set "PG_DIR=C:\Program Files\PostgreSQL\17"
    echo [OK]    기존 PostgreSQL 17 사용
    goto :PG_VER_DONE
)
if exist "C:\Program Files\PostgreSQL\16\bin\psql.exe" (
    set "PG_VERSION=16"
    set "PG_DIR=C:\Program Files\PostgreSQL\16"
    echo [OK]    기존 PostgreSQL 16 사용
    goto :PG_VER_DONE
)
REM 미설치 — 신규 PG 18 설치 예정
set "PG_VERSION=18"
set "PG_DIR=C:\Program Files\PostgreSQL\18"
:PG_VER_DONE

set "PG_NEEDS_INSTALL=0"
if not exist "%PG_DIR%\bin\psql.exe" set "PG_NEEDS_INSTALL=1"

REM --- 4) postgres 비밀번호 입력 (PG 신규 또는 .env 생성용) ------------------
echo.
echo ============================================================
echo  PostgreSQL postgres 슈퍼유저 비밀번호 입력
echo ============================================================
if "%PG_NEEDS_INSTALL%"=="1" (
    echo  PG 가 새로 설치됩니다. 강한 비밀번호를 정하세요.
) else (
    echo  기존 PG 발견. 기존에 설정한 postgres 비밀번호를 입력하세요.
)
echo ============================================================
echo.

powershell -NoProfile -Command "$p = Read-Host -Prompt 'postgres 비밀번호' -AsSecureString; $b = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($p); $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($b); [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b); $enc = New-Object System.Text.UTF8Encoding($false); [IO.File]::WriteAllText('%TEMP%\aidh_pgpw.tmp', $plain, $enc)"

if not exist "%TEMP%\aidh_pgpw.tmp" (
    echo [ERROR] 비밀번호 입력 실패
    goto :FAIL_PAUSE
)

set /p PG_PASSWORD=<"%TEMP%\aidh_pgpw.tmp"
del /q "%TEMP%\aidh_pgpw.tmp" > nul 2>&1

if "%PG_PASSWORD%"=="" (
    echo [ERROR] 빈 비밀번호 — 중단
    goto :FAIL_PAUSE
)
echo [OK]    비밀번호 입력 받음 (이후 표시되지 않음)

REM 비번을 cmd 인자가 아닌 환경변수로 전달 (특수문자 escape 회피)
set "AIDH_PG_PW=%PG_PASSWORD%"

REM --- 5) PG 설치 (필요 시) --------------------------------------------------
if "%PG_NEEDS_INSTALL%"=="1" (
    echo.
    echo [1/6] PostgreSQL %PG_VERSION% 자동 설치 (5~10분) ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%DEPLOY_DIR%\install_postgres_windows.ps1' -PostgresPassword $env:AIDH_PG_PW -PgVersion %PG_VERSION%"
    if errorlevel 1 (
        echo [ERROR] PostgreSQL 설치 실패
        set "AIDH_PG_PW="
        goto :FAIL_PAUSE
    )
) else (
    echo [SKIP]  PostgreSQL %PG_VERSION% 이미 설치됨
)

REM --- 6) pgvector 설치 ------------------------------------------------------
echo.
echo [2/6] pgvector 설치 ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%DEPLOY_DIR%\install_pgvector_windows.ps1' -PgVersion %PG_VERSION% -PostgresPassword $env:AIDH_PG_PW"
set "PGVECTOR_RC=%errorlevel%"
if not "%PGVECTOR_RC%"=="0" (
    if "%PGVECTOR_RC%"=="4" (
        echo [WARN]  pgvector 검증 실패 — ABI 불일치 가능. 시맨틱 검색은 ILIKE 폴백 사용.
    ) else (
        echo [ERROR] pgvector 설치 실패 (rc=%PGVECTOR_RC%)
        goto :FAIL_PAUSE
    )
)

REM --- 7) api_server\.env 자동 생성 ------------------------------------------
echo.
echo [3/6] api_server\.env 생성 ...

if exist "%API_DIR%\.env" (
    echo [SKIP]  %API_DIR%\.env 이미 존재 — 건너뜀
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%DEPLOY_DIR%\write_env.ps1" -EnvPath "%API_DIR%\.env"
    if errorlevel 1 (
        echo [ERROR] .env 생성 실패
        set "AIDH_PG_PW="
        set "PG_PASSWORD="
        goto :FAIL_PAUSE
    )
    echo [OK]    %API_DIR%\.env 생성됨
)

REM 비번 환경변수 즉시 정리 (이후 단계에서 더 필요 없음)
set "AIDH_PG_PW="
set "PG_PASSWORD="

REM --- 8) venv 사전 생성 (setup.bat 사전조건) --------------------------------
if not exist "%API_DIR%\.venv\Scripts\python.exe" (
    echo.
    echo [4/6] Python venv 생성 ...
    pushd "%API_DIR%"
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo [ERROR] venv 생성 실패
        popd
        goto :FAIL_PAUSE
    )
    popd
    echo [OK]    .venv 생성됨
) else (
    echo [SKIP]  .venv 이미 존재
)

REM --- 9) api_server\setup.bat 호출 (PG Path 환경변수 적용) -----------------
echo.
echo [5/6] api_server\setup.bat 실행 (의존성 + 마이그레이션 + 시드) ...

REM 현재 세션 Path 에 PG bin 추가 (install_postgres_windows.ps1 가 Machine 단계에 등록 했지만 현재 cmd 세션은 갱신 안 됨)
set "PATH=%PATH%;%PG_DIR%\bin"

pushd "%API_DIR%"
call setup.bat
set "SETUP_RC=%errorlevel%"
popd
if not "%SETUP_RC%"=="0" (
    echo [ERROR] setup.bat 실패 (rc=%SETUP_RC%)
    goto :FAIL_PAUSE
)

REM --- 10) uvicorn 백그라운드 기동 ------------------------------------------
echo.
echo [6/6] API 서버 백그라운드 기동 ...

REM 기존 8000 포트 점유 검증
netstat -ano | findstr ":8000" | findstr "LISTENING" > nul 2>&1
if not errorlevel 1 (
    echo [WARN]  포트 8000 이미 점유 중 — 기존 서버가 떠 있다면 재기동 불필요
) else (
    REM 새 콘솔에서 run.bat 기동 (api_server\run.bat 직접 호출, /D 로 시작 디렉터리 지정)
    start "AI Data Hub API" /MIN /D "%API_DIR%" cmd /c "run.bat"
    echo [OK]    백그라운드 기동 명령 보냄 — 헬스체크 대기 ...
)

REM --- 11) 헬스체크 -----------------------------------------------------------
echo.
set "HEALTH_URL=http://localhost:8000/api/system/health"
set "SUCCESS=0"
for /l %%i in (1,1,30) do (
    curl -sf -o nul "%HEALTH_URL%" > nul 2>&1
    if not errorlevel 1 (
        set "SUCCESS=1"
        goto :HEALTH_DONE
    )
    timeout /t 2 /nobreak > nul
)
:HEALTH_DONE

if "%SUCCESS%"=="1" (
    echo [OK]    API 응답 확인: %HEALTH_URL%
) else (
    echo [WARN]  60초 안에 응답 없음
    echo         로그 확인: %API_DIR%\run.bat 콘솔 창
)

REM --- 12) 완료 안내 ---------------------------------------------------------
echo.
echo ============================================================
echo   서버 셋업 완료
echo ============================================================
echo   API         : http://localhost:8000
echo   헬스체크    : http://localhost:8000/api/system/health
echo   API docs    : http://localhost:8000/docs
echo   discover    : http://localhost:8000/api/discover
echo.
echo   재시작      : cd "%API_DIR%" ^&^& run.bat
echo   종료        : 작업관리자에서 python.exe 종료
echo                 또는 작업표시줄의 "AI Data Hub API" 창 닫기
echo.
echo   다음 단계:
echo     - 클라이언트 셋업: SERVER_SETUP_GUIDE.md "다음 단계" 참조
echo     - 인증 활성화 (운영): %API_DIR%\.env 의 AUTH_REQUIRED=true
echo ============================================================
echo.

REM 비번 변수 비우기 (메모리 잔존 최소화)
set "PG_PASSWORD="

pause
exit /b 0

:FAIL_PAUSE
echo.
echo ============================================================
echo   셋업 중단 — 위 에러를 확인 후 재시도 하세요.
echo ============================================================
set "PG_PASSWORD="
pause
exit /b 1
