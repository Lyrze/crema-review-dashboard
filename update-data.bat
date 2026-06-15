@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 ( echo [ERROR] Python not found. & pause & exit /b 1 )
where git >nul 2>&1
if errorlevel 1 ( echo [ERROR] Git not found. & pause & exit /b 1 )

:: Python이 대화형 메뉴 + 선택 처리 전담
:: 메뉴/입력 = stderr(화면), 결과 KEY=VALUE = stdout(파일)
python scripts\interactive_select.py > "%TEMP%\crema_sel.tmp"
if errorlevel 1 ( echo. & echo [FAILED] & pause & exit /b 1 )

:: 결과 파싱
set "BRAND=" & set "MONTH=" & set "CSV=" & set "PREV_FLAG=" & set "AI_FLAG=" & set "RECLASS_FLAG=" & set "REVERIFY_FLAG=" & set "ANON_OUT="

for /f "usebackq tokens=1,* delims==" %%A in ("%TEMP%\crema_sel.tmp") do (
  if "%%A"=="BRAND"         set "BRAND=%%B"
  if "%%A"=="MONTH"         set "MONTH=%%B"
  if "%%A"=="CSV"           set "CSV=%%B"
  if "%%A"=="PREV_FLAG"     set "PREV_FLAG=%%B"
  if "%%A"=="AI_FLAG"       set "AI_FLAG=%%B"
  if "%%A"=="RECLASS_FLAG"  set "RECLASS_FLAG=%%B"
  if "%%A"=="REVERIFY_FLAG" set "REVERIFY_FLAG=%%B"
  if "%%A"=="ANON_OUT"      set "ANON_OUT=%%B"
)

if "!BRAND!"=="" ( echo [ERROR] 선택 값 없음. & pause & exit /b 1 )

:: 데이터 처리 (RECLASS_FLAG 있으면 AI 정밀 분류 포함 - 오래 걸림)
python scripts\process_data.py ^
  --brand "!BRAND!" ^
  --month "!MONTH!" ^
  --input "!CSV!" ^
  !PREV_FLAG! ^
  !AI_FLAG! ^
  !RECLASS_FLAG!

if errorlevel 1 ( echo. & echo [ERROR] 데이터 처리 실패. & pause & exit /b 1 )

:: [3.5/4] AI 정밀 보정 (의심 키워드를 더 큰 모델로 재검증) - REVERIFY_FLAG 있을 때만
if not "!REVERIFY_FLAG!"=="" (
  echo.
  echo  [3.5/4] AI 정밀 보정 중 - 의심 키워드 재검증...
  python scripts\reverify_suspect.py --brand "!BRAND!" --month "!MONTH!" !REVERIFY_FLAG!
)

:: 처리 결과
python scripts\show_result.py "!BRAND!" "!MONTH!" 2>nul

:: 익명화 CSV
echo.
echo  익명화 CSV 생성 중...
python scripts\anonymize_csv.py --input "!CSV!" --output "!ANON_OUT!"
if errorlevel 1 ( echo  [WARNING] 익명화 실패. 계속 진행합니다. )

:: GitHub 푸시
echo.
echo  GitHub 푸시 중...
git add docs\data\ data\anonymized\ .gitignore scripts\
git commit -m "data: !BRAND! !MONTH! review update"
if errorlevel 1 ( echo  커밋할 변경 없음. & goto :done )

git push origin main
if errorlevel 1 ( echo [ERROR] Push 실패. upload.bat 먼저 실행하세요. & pause & exit /b 1 )

:done
echo.
echo  =============================================
echo   [OK] !BRAND! !MONTH! 업데이트 완료!
echo  =============================================
echo.
echo  Dashboard: https://lyrze.github.io/crema-review-dashboard/
echo.
pause
exit /b 0
