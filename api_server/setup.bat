@echo off
REM ============================================================
REM  AI Data API Server — Initial Setup (Windows native)
REM
REM  Prerequisites (이 스크립트 실행 전 준비되어 있어야 함):
REM    1) PostgreSQL 16+ 설치 + 서비스 실행 중
REM    2) Python 3.12 venv 가 .venv\ 에 생성되어 있음
REM       (없으면: py -3.12 -m venv .venv)
REM
REM  실행 후 산출물:
REM    - 의존성 설치 (pip install -r requirements.txt)
REM    - .env 생성 (없을 때 .env.example 복사 후 편집 안내)
REM    - ai_data 데이터베이스 생성 (없을 때)
REM    - pgvector 확장 활성화 시도 (실패해도 진행)
REM    - alembic 마이그레이션 적용
REM    - 표준 에이전트 5종 시드
REM    - figures/attachments/output 디렉터리 생성
REM ============================================================

setlocal enabledelayedexpansion
chcp 65001 > nul
cd /d "%~dp0"

echo.
echo ============================================================
echo  AI Data API Server — Setup
echo ============================================================
echo.

REM --- 1) venv 검증 ----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe 가 없습니다.
    echo         먼저 venv 를 만드세요: py -3.12 -m venv .venv
    exit /b 1
)
echo [OK]    venv 확인됨

REM --- 2) psql 위치 확인 -----------------------------------------------
where psql > nul 2>&1
if %errorlevel%==0 (
    set "PSQL=psql"
) else (
    set "PSQL=C:\Program Files\PostgreSQL\16\bin\psql.exe"
    if not exist "!PSQL!" (
        echo [ERROR] psql.exe 를 PATH 또는 기본 설치 경로에서 찾지 못했습니다.
        echo         PostgreSQL 16 을 설치하거나 PATH 에 추가하세요.
        echo         기본 경로: C:\Program Files\PostgreSQL\16\bin
        exit /b 1
    )
)
echo [OK]    psql 위치: !PSQL!

REM --- 3) .env 확인 ----------------------------------------------------
if not exist ".env" (
    if not exist ".env.example" (
        echo [ERROR] .env.example 가 없습니다.
        exit /b 1
    )
    echo [INFO]  .env 파일이 없어 .env.example 에서 복사합니다.
    copy .env.example .env > nul
    echo.
    echo ============================================================
    echo  중요: .env 를 메모장으로 열어 PostgreSQL 비밀번호를 수정하세요.
    echo        DATABASE_URL=postgresql+asyncpg://postgres:비밀번호@localhost:5432/ai_data
    echo  수정 후 setup.bat 을 다시 실행하세요.
    echo ============================================================
    echo.
    notepad .env
    exit /b 0
)
echo [OK]    .env 발견

REM --- 4) .env 에서 DATABASE_URL 추출 ----------------------------------
set "DB_URL="
for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if /i "%%a"=="DATABASE_URL" set "DB_URL=%%b"
)
if "!DB_URL!"=="" (
    echo [ERROR] .env 에 DATABASE_URL 가 없습니다.
    exit /b 1
)
echo [OK]    DATABASE_URL: !DB_URL!

REM --- 5) venv 활성화 + pip 업그레이드 ---------------------------------
call .venv\Scripts\activate.bat
echo [OK]    venv 활성화

echo.
echo --- pip 업그레이드 ---
python -m pip install --upgrade pip > nul
if %errorlevel% neq 0 (
    echo [WARN]  pip 업그레이드 실패 (계속 진행)
)

REM --- 6) 의존성 설치 --------------------------------------------------
echo.
echo --- 의존성 설치 ---
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] pip install 실패
    exit /b 1
)
echo [OK]    의존성 설치 완료

REM --- 7) ai_data 데이터베이스 생성 ------------------------------------
echo.
echo --- 데이터베이스 'ai_data' 확인/생성 ---
"!PSQL!" -U postgres -h localhost -tAc "SELECT 1 FROM pg_database WHERE datname='ai_data'" 2> nul | findstr "1" > nul
if %errorlevel%==0 (
    echo [OK]    데이터베이스 'ai_data' 이미 존재
) else (
    "!PSQL!" -U postgres -h localhost -c "CREATE DATABASE ai_data;"
    if %errorlevel% neq 0 (
        echo [ERROR] 데이터베이스 생성 실패
        echo         PostgreSQL 서비스 실행 여부 + postgres 비밀번호 확인
        echo         서비스 확인: services.msc 에서 postgresql-x64-16
        exit /b 1
    )
    echo [OK]    데이터베이스 'ai_data' 생성됨
)

REM --- 8) pgvector 확장 (선택) -----------------------------------------
echo.
echo --- pgvector 확장 활성화 시도 (선택) ---
"!PSQL!" -U postgres -h localhost -d ai_data -c "CREATE EXTENSION IF NOT EXISTS vector;" 2> nul
if %errorlevel%==0 (
    echo [OK]    pgvector 활성화됨 (시맨틱 검색 사용 가능)
) else (
    echo [WARN]  pgvector 미설치 — 시맨틱 검색은 ILIKE 폴백 사용
    echo         설치 가이드: docs\windows_native_setup.md 3장
)

REM --- 9) alembic 마이그레이션 -----------------------------------------
echo.
echo --- alembic 마이그레이션 (0001~0006) ---
set PYTHONPATH=src
alembic upgrade head
if %errorlevel% neq 0 (
    echo [ERROR] 마이그레이션 실패
    echo         pgvector 미설치이고 0004 가 막혔다면, pgvector 설치 후 재시도
    exit /b 1
)
echo [OK]    마이그레이션 적용됨

REM --- 10) 표준 에이전트 시드 ------------------------------------------
echo.
echo --- 표준 에이전트 5종 시드 ---
python -m api.seed
if %errorlevel% neq 0 (
    echo [WARN]  시드 실패 (이미 존재하면 무시 가능)
) else (
    echo [OK]    표준 에이전트 시드 완료
)

REM --- 11) 디렉터리 생성 ------------------------------------------------
if not exist "figures"     mkdir figures
if not exist "attachments" mkdir attachments
if not exist "output"      mkdir output
echo [OK]    figures/ attachments/ output/ 준비

REM --- 완료 ------------------------------------------------------------
echo.
echo ============================================================
echo  Setup 완료
echo ============================================================
echo.
echo  서버 실행: run.bat
echo  파일 적재: ingest.bat ^<파일경로^>
echo  API 문서: http://localhost:8000/docs (서버 실행 후)
echo.

endlocal
