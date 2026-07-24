@echo off
title AI Cloudflare Tunnel
chcp 65001 >nul
color 0B

python "%~dp0scripts\start_tunnel.py"

pause
