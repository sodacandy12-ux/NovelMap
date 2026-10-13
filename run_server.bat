@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 소설 지도 (Novel Map) 서버

rem 이미 8000번 포트를 쓰는 서버가 있으면 중복 실행하지 않는다
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if %errorlevel%==0 (
  echo [알림] 8000번 포트에서 이미 서버가 실행 중입니다. 브라우저만 엽니다.
  start "" http://localhost:8000
  pause
  exit /b 0
)

where python >nul 2>nul
if errorlevel 1 (
  echo [오류] python 을 찾을 수 없습니다. Python 설치 후 PATH 에 추가하세요.
  pause
  exit /b 1
)

rem 필수 패키지가 없으면 설치 (처음 한 번)
python -c "import fastapi, uvicorn, langgraph, chromadb, anthropic" >nul 2>nul
if errorlevel 1 (
  echo [설치] 필요한 패키지를 설치합니다...
  python -m pip install -r requirements.txt
)

echo.
echo 소설 지도 서버를 시작합니다: http://localhost:8000
echo - API 키는 화면 상단 설정 바에서 입력하세요 (서버 메모리에만 저장, 재시작하면 다시 입력)
echo - 환경 변수 ANTHROPIC_API_KEY 또는 .env 파일에 키가 있으면 입력 없이 사용됩니다
echo - 종료하려면 이 창에서 Ctrl+C 를 누르거나 창을 닫으세요
echo.

rem 서버가 뜰 시간을 준 뒤 브라우저를 연다
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start "" http://localhost:8000"

python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
  echo.
  echo [오류] 서버가 비정상 종료되었습니다. 위 메시지를 확인하세요.
)
pause
