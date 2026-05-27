@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "REPO_URL=https://github.com/Lyrze/crema-review-dashboard.git"
set "BRANCH=main"

if "%~1"=="" (
    for /f "tokens=1-3 delims=/" %%a in ("%date%") do set "TODAY=%%c-%%a-%%b"
    set "MSG=chore: dashboard update [!TODAY!]"
) else (
    set "MSG=%~1"
)

echo.
echo  ==========================================
echo   Crema Review Dashboard - GitHub Upload
echo  ==========================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git not found.
    echo         Install: https://git-scm.com/download/win
    goto :error
)

cd /d "%~dp0"
echo [1/6] Folder: %~dp0

if not exist ".git" (
    echo [2/6] Initializing Git...
    git init
    git branch -M %BRANCH%
    git remote add origin %REPO_URL%
    echo       Remote: %REPO_URL%
) else (
    echo [2/6] Git repository OK.
    git remote get-url origin >nul 2>&1
    if errorlevel 1 git remote add origin %REPO_URL%
)

echo [3/6] Checking changes...
git status --short

set "HAS_CHANGES="
for /f %%i in ('git status --short 2^>nul') do set HAS_CHANGES=1
if not defined HAS_CHANGES (
    echo  No changes to commit.
    goto :done
)

echo [4/6] Staging files...
git add docs/
git add scripts/
git add .github/ 2>nul
git add CLAUDE.md README.md requirements.txt .gitignore 2>nul
echo  Staged:
git diff --cached --name-only

echo [5/6] Committing...
echo       Message: %MSG%
git commit -m "%MSG%"
if errorlevel 1 goto :error

echo [6/6] Pushing to GitHub...
git push -u origin %BRANCH%
if errorlevel 1 (
    echo.
    echo [ERROR] Push failed. Authentication required:
    echo   Username : Lyrze
    echo   Password : Personal Access Token  ^(NOT GitHub password^)
    echo   Get token: https://github.com/settings/tokens/new
    echo              ^(check "repo" scope, then copy the token^)
    echo.
    goto :error
)

:done
echo.
echo  [OK] Upload complete!
echo  Dashboard : https://lyrze.github.io/crema-review-dashboard/
echo  Repository: https://github.com/Lyrze/crema-review-dashboard
echo.
pause
exit /b 0

:error
echo.
echo  [FAILED] See error messages above.
echo.
pause
exit /b 1
