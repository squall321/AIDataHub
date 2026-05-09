@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist config.ini (
    echo config.ini 가 없다. config.example.ini 를 복사해서 만든다...
    copy /Y config.example.ini config.ini >nul
    echo.
    echo === config.ini 가 생성되었다 ===
    echo 메모장 또는 VS Code 로 config.ini 를 열어 다음을 채워라:
    echo   [server] base_url   ^<-- API 서버 주소
    echo   [server] api_key    ^<-- 발급받은 X-API-Key
    echo   [model]  size       ^<-- tiny / small / medium / large
    echo.
    echo 수정 후 setup.bat 를 다시 실행하라.
    pause
    exit /b 0
)

powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0lib\setup.ps1"
echo.
pause
