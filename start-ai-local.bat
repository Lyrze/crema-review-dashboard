@echo off
title Claude AI Local Proxy
chcp 65001 >nul
color 0B

python "%~dp0scripts\local_proxy.py"

pause
