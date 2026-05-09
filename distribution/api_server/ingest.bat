@echo off
REM ============================================================
REM  AI Data API Server — Convert + Ingest a single file
REM
REM  사용법:
REM    ingest.bat <파일경로> [division] [team] [year] [seq]
REM
REM  예:
REM    ingest.bat d:\tmp\iga_guide.docx
REM    ingest.bat data.xlsx HE CAE 2026 5
REM
REM  지원 확장자: .docx .xlsx .pptx .md .markdown .pdf
REM  결과는 output\ 에 저장 후 자동으로 DB 적재.
REM ============================================================

setlocal enabledelayedexpansion
chcp 65001 > nul
cd /d "%~dp0"

if "%~1"=="" (
    echo Usage: ingest.bat ^<file^> [division] [team] [year] [seq]
    echo Default: division=HE team=CAE year=2026 seq=1
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 가 없습니다. 먼저 setup.bat 을 실행하세요.
    exit /b 1
)

set "FILE=%~1"
if not exist "%FILE%" (
    echo [ERROR] 파일을 찾을 수 없음: %FILE%
    exit /b 1
)

REM --- 인자 파싱 (기본값) ----------------------------------------------
set "DIV=%~2"
if "!DIV!"=="" set "DIV=HE"
set "TEAM=%~3"
if "!TEAM!"=="" set "TEAM=CAE"
set "YEAR=%~4"
if "!YEAR!"=="" set "YEAR=2026"
set "SEQ=%~5"
if "!SEQ!"=="" set "SEQ=1"

REM --- 확장자 확인 -----------------------------------------------------
set "EXT=%~x1"
if /i "!EXT!"==".docx" (
    set "CONVERTER=converter"
    set "DATA_TYPE=DOC"
) else if /i "!EXT!"==".xlsx" (
    set "CONVERTER=excel_converter"
    set "DATA_TYPE=DATA"
) else if /i "!EXT!"==".pptx" (
    set "CONVERTER=ppt_converter"
    set "DATA_TYPE=DOC"
) else if /i "!EXT!"==".md" (
    set "CONVERTER=md_converter"
    set "DATA_TYPE=DOC"
) else if /i "!EXT!"==".markdown" (
    set "CONVERTER=md_converter"
    set "DATA_TYPE=DOC"
) else if /i "!EXT!"==".pdf" (
    set "CONVERTER=pdf_converter"
    set "DATA_TYPE=DOC"
) else (
    echo [ERROR] 지원하지 않는 확장자: !EXT!
    echo         지원: .docx .xlsx .pptx .md .markdown .pdf
    exit /b 1
)

call .venv\Scripts\activate.bat
set PYTHONPATH=src

echo.
echo ============================================================
echo  Convert + Ingest
echo ============================================================
echo  File:     %FILE%
echo  Format:   !EXT! → !CONVERTER!
echo  ID:       !DATA_TYPE!-!DIV!-!TEAM!-!YEAR!-%SEQ:0=%
echo ============================================================
echo.

REM --- 변환 ------------------------------------------------------------
echo [1/2] 변환 중...
python -m !CONVERTER! "%FILE%" --division !DIV! --team !TEAM! --year !YEAR! --seq !SEQ! --output-dir output
if %errorlevel% neq 0 (
    echo [ERROR] 변환 실패
    exit /b 1
)

REM --- 산출 JSON 경로 조립 (예: output\DOC-HE-CAE-2026-000001.json) ---
set /a SEQ_NUM=!SEQ! 2>nul
if errorlevel 1 set "SEQ_NUM=!SEQ!"
set "PADDED_SEQ=00000!SEQ_NUM!"
set "PADDED_SEQ=!PADDED_SEQ:~-6!"
set "JSON_PATH=output\!DATA_TYPE!-!DIV!-!TEAM!-!YEAR!-!PADDED_SEQ!.json"

if not exist "!JSON_PATH!" (
    echo [WARN]  예상 경로에 JSON 없음: !JSON_PATH!
    echo         output\ 에서 가장 최근 .json 을 찾습니다.
    for /f "delims=" %%f in ('dir /b /od /a-d "output\*.json" 2^>nul') do set "JSON_PATH=output\%%f"
)

if not exist "!JSON_PATH!" (
    echo [ERROR] 변환 결과 JSON 을 찾지 못했습니다.
    exit /b 1
)

echo.
echo [2/2] DB 적재 중: !JSON_PATH!
python -m api.ingest "!JSON_PATH!"
if %errorlevel% neq 0 (
    echo [ERROR] 적재 실패
    exit /b 1
)

echo.
echo ============================================================
echo  완료 — 적재된 record: !DATA_TYPE!-!DIV!-!TEAM!-!YEAR!-!PADDED_SEQ!
echo  확인: psql -U postgres -d ai_data -c "SELECT id, title FROM records ORDER BY created_at DESC LIMIT 5;"
echo ============================================================

endlocal
