@echo off
title Ollama Cloudflare Tunnel (닫으면 AI 종료)
chcp 65001 >/dev/null
color 0B
echo.
echo  ============================================================
echo                                                            
echo     Ollama Cloudflare Tunnel - 외부 PC AI 접속용             
echo                                                            
echo  ============================================================
echo.
echo   [중요] 이 창을 닫으면 다른 자리에서 AI를 못 씁니다.        
echo          최소화만 하고 자리 켠 동안 그대로 두세요.            
echo.
echo  ============================================================
echo.

REM 같은 폴더의 start_tunnel.py 실행
python "%~dp0scripts\start_tunnel.py"

REM Python 종료 후에도 창은 유지
echo.
echo ============================================================
echo  터널이 종료되었습니다. 이 창은 안전하게 닫아도 됩니다.
echo ============================================================
pause
