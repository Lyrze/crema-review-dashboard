@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo [1/4] Git lock 파일 정리 중...
if exist ".git\index.lock" del /f /q ".git\index.lock"
if exist ".git\HEAD.lock" del /f /q ".git\HEAD.lock"

echo [2/4] 변경 파일 스테이징...
git add -A
if %errorlevel% neq 0 (
    echo [오류] git add 실패
    pause
    exit /b 1
)

echo [3/4] 커밋 메시지를 입력하세요 (엔터 시 기본값 사용):
set /p MSG="커밋 메시지: "
if "%MSG%"=="" set MSG=update: 데이터 및 대시보드 업데이트

git commit -m "%MSG%"
if %errorlevel% neq 0 (
    echo [알림] 커밋할 변경사항이 없거나 오류 발생
    pause
    exit /b 0
)

echo [4/4] GitHub에 푸시 중...
git push origin main
if %errorlevel% neq 0 (
    echo [오류] 푸시 실패 - 인터넷 연결 및 GitHub 인증 확인
    pause
    exit /b 1
)

echo.
echo [완료] GitHub Pages에 배포됐습니다!
echo   확인: https://lyrze.github.io/crema-review-dashboard/
timeout /t 3
