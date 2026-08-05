"""run_ss_classify.py — 스마트스토어 리뷰만 별도 분류 (자사몰 미접촉, 로컬 전용, push 안 함)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
data/raw/스마트스토어_변환/reviews_ss_{월}.csv 를 입력으로, 브랜드 '스마트스토어' 하위에
SS 리뷰만 분류한다(자사몰 슬룸 데이터는 전혀 건드리지 않음). 나중에 merge_smartstore.py 가
이 결과를 담당자 업로드 시 슬룸 각 월에 주입한다.

단계(자사몰과 동일 취지, SS 물량만):
  1. process_data (정규식 키워드 + 별점 폴백 감성) — Claude 아님, 빠름
  2. recheck_sentiment --full (Claude 본문 감성 판정) — 핵심 분류
  3. classify_pvoc_intent (Claude PVOC 토픽 긍/부) — 구매경험 VOC 감성

각 단계 quota_retry 래핑(세션 한도 자동 대기·재개). git push 안 함.
독립 프로세스 백그라운드 실행 권장:
  Start-Process -WindowStyle Hidden python -ArgumentList 'scripts/run_ss_classify.py --months 2026-03,2026-04,2026-05,2026-06,2026-07' ...
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
QR = str(ROOT / "scripts" / "quota_retry.py")
SS_CSV_DIR = ROOT / "data" / "raw" / "스마트스토어_변환"
BRAND = "스마트스토어"


def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


def run(name, cmd, hard=True):
    log(f"▶ {name}")
    r = subprocess.run([PY, QR, "--"] + cmd)
    if r.returncode != 0:
        log(f"  ✗ {name} 실패 (exit {r.returncode})")
        return not hard
    log(f"  ✓ {name}")
    return True


def do_month(month):
    csv = SS_CSV_DIR / f"reviews_ss_{month}.csv"
    if not csv.is_file():
        log(f"[SKIP] {month}: {csv.name} 없음")
        return
    log(f"===== {month} 스마트스토어 분류 시작 =====")
    ok = run("process_data(정규식)",
             ["python", str(ROOT/"scripts"/"process_data.py"),
              "--brand", BRAND, "--month", month, "--input", str(csv)], hard=True)
    if not ok:
        return
    run("recheck_sentiment(--full)",
        ["python", str(ROOT/"scripts"/"recheck_sentiment.py"),
         "--brand", BRAND, "--months", month, "--full"], hard=False)
    run("classify_pvoc_intent",
        ["python", str(ROOT/"scripts"/"classify_pvoc_intent.py"),
         "--brand", BRAND, "--month", month], hard=False)
    log(f"===== {month} 종료 =====")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-03,2026-04,2026-05,2026-06,2026-07")
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    log(f"스마트스토어 분류 시작 — {months} (자사몰 미접촉, push 안 함)")
    for m in months:
        do_month(m)
    log("전체 완료. docs/data/스마트스토어/{월} 확인 → merge_smartstore.py 로 병합 준비.")


if __name__ == "__main__":
    main()
