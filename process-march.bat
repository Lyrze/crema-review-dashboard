@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo.
echo  ==========================================
echo   Crema Review Dashboard - 슬룸 2026-03 처리
echo  ==========================================
echo.

cd /d "%~dp0"

:: Python 확인
where python >nul 2>&1
if errorlevel 1 ( echo [ERROR] Python이 설치되어 있지 않습니다. & goto :error )

:: Git 확인
where git >nul 2>&1
if errorlevel 1 ( echo [ERROR] Git이 설치되어 있지 않습니다. & goto :error )

:: CSV 파일 존재 확인
if not exist "data\raw\슬룸\2026-03\reviews.csv" (
  echo [ERROR] 파일 없음: data\raw\슬룸\2026-03\reviews.csv
  goto :error
)

:: 4월 데이터를 이전 월(prev)로 사용
set "PREV_FLAG="
if exist "data\raw\슬룸\2026-04\reviews.csv" (
  echo  이전 월 데이터 발견: 2026-04  [전월 비교에 사용됩니다]
  set "PREV_FLAG=--prev-input "data\raw\슬룸\2026-04\reviews.csv""
) else (
  echo  이전 월 데이터 없음  [전월 비교 생략]
)

echo.
echo  브랜드 : 슬룸
echo  월     : 2026-03
echo  CSV    : data\raw\슬룸\2026-03\reviews.csv
echo  AI     : 건너뜀 (--skip-ai)
echo.

:: ── 데이터 처리 ─────────────────────────────────────────────────────
echo  [1/3] 데이터 처리 중...
echo.

python scripts\process_data.py ^
  --brand "슬룸" ^
  --month "2026-03" ^
  --input "data\raw\슬룸\2026-03\reviews.csv" ^
  !PREV_FLAG! ^
  --skip-ai

if errorlevel 1 ( echo. & echo [ERROR] 데이터 처리 실패. & goto :error )

:: ── 처리 결과 출력 ──────────────────────────────────────────────────
echo.
echo  [2/3] 처리 결과:
python -c "
import json, sys
try:
    p='docs/data/슬룸/2026-03/summary.json'
    d=json.load(open(p,'r',encoding='utf-8'))
    k=d['kpis']
    print(f'  리뷰 수  : {k[chr(34)+\"total_reviews\"+chr(34)]:,}건')
    print(f'  평균 별점: {k[chr(34)+\"avg_rating\"+chr(34)]:.2f}')
    print(f'  긍정률   : {k[chr(34)+\"positive_rate\"+chr(34)]:.1f}%%')
except Exception as e:
    print(f'  (결과 파일 읽기 실패: {e})')
" 2>nul

:: ── GitHub 푸시 ─────────────────────────────────────────────────────
echo.
echo  [3/3] GitHub에 푸시 중...

git add docs\data\
git commit -m "data: 슬룸 2026-03 리뷰 업데이트"
if errorlevel 1 ( echo  커밋할 변경사항 없음. & goto :done )

git push origin main
if errorlevel 1 (
  echo [ERROR] Push 실패.
  echo  인증이 필요하면 upload.bat을 먼저 실행하세요.
  goto :error
)

:done
echo.
echo  ======================================
echo   [완료] 슬룸 2026-03 업데이트 성공
echo  ======================================
echo.
echo  대시보드: https://lyrze.github.io/crema-review-dashboard/
echo.
pause
exit /b 0

:error
echo.
echo  [실패] 위의 오류 메시지를 확인하세요.
echo.
pause
exit /b 1
