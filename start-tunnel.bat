@echo off
title AI Cloudflare Tunnel (닫으면 AI 종료)
chcp 65001 >nul
color 0B
setlocal EnableDelayedExpansion
echo.
echo  ============================================================
echo.
echo     AI Cloudflare Tunnel - 외부 PC AI 접속용
echo.
echo  ============================================================
echo.
echo   [중요] 이 창을 닫으면 다른 자리에서 AI를 못 씁니다.
echo          최소화만 하고 자리 켠 동안 그대로 두세요.
echo.
echo  ============================================================
echo   AI 백엔드 선택
echo     1. Ollama (로컬 GPU 필요, 기본)
echo     2. Claude Code CLI (GPU 불필요 - 구독 인증, 미로그인 시 자동 로그인창)
echo  ============================================================
echo.
set /p AIBSEL="  선택 (1~2, Enter=1번): "
if "!AIBSEL!"=="2" (set "AI_BACKEND=claude") else (set "AI_BACKEND=ollama")
echo.
echo  AI_BACKEND=!AI_BACKEND!
echo.

REM 같은 폴더의 start_tunnel.py 실행 (AI_BACKEND 환경변수는 자식 프로세스로 상속됨)
python "%~dp0scripts\start_tunnel.py"

REM Python 종료 후에도 창은 유지
echo.
echo ============================================================
echo  터널이 종료되었습니다. 이 창은 안전하게 닫아도 됩니다.
echo ============================================================
pause
