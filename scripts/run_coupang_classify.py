"""run_coupang_classify.py — 쿠팡 리뷰 분류 (별점 감성 + 본문 있는 것만 Claude 내용 체크)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
data/raw/쿠팡_변환/reviews_cp_{월}.csv 를 브랜드 '쿠팡'으로 분류(자사몰 미접촉, push 안 함).

단계(월별):
  1. process_data — 별점기반 감성(CSV sentiment 컬럼) + 정규식 키워드. Claude 아님.
  2. 별점만 리뷰('별점만 남기고..')를 감성 진행마커 done 에 사전주입 → recheck 가 건너뜀.
  3. recheck_sentiment --full — **본문 있는 리뷰만** Claude로 재판정
     (별점 높은데 내용이 부정인 경우 등 별점↔내용 불일치 교정).

각 단계 quota_retry 래핑. 별점만은 별점감성 그대로 유지.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# cp949 콘솔에서 유니코드(—, ✓ 등) 출력 시 크래시 방지
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
QR = str(ROOT / "scripts" / "quota_retry.py")
CSV_DIR = ROOT / "data" / "raw" / "쿠팡_변환"
BRAND = "쿠팡"
STARONLY_TEXT = "별점만 남기고 별도 리뷰작성하지 않음"


def log(m):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)


def run(name, cmd, hard=True):
    log(f"▶ {name}")
    r = subprocess.run([PY, QR, "--"] + cmd)
    if r.returncode != 0:
        log(f"  ✗ {name} 실패(exit {r.returncode})")
        return not hard
    log(f"  ✓ {name}")
    return True


def preseed_staronly(month):
    """별점만 리뷰ID를 감성 진행마커 done_ids 에 넣어 recheck 가 건너뛰게 한다."""
    d = ROOT / "docs" / "data" / BRAND / month
    rp = d / "reviews.json"
    if not rp.is_file():
        return 0
    reviews = json.loads(rp.read_text(encoding="utf-8")).get("reviews", {})
    star_only = [rid for rid, r in reviews.items() if (r.get("text") or "").strip() == STARONLY_TEXT]
    marker = d / ".sentiment_progress.json"
    marker.write_text(json.dumps({"mode": "full", "done_ids": sorted(star_only), "fixes": {}},
                                 ensure_ascii=False), encoding="utf-8")
    return len(star_only)


def do_month(month):
    csv = CSV_DIR / f"reviews_cp_{month}.csv"
    if not csv.is_file():
        log(f"[SKIP] {month}: {csv.name} 없음"); return
    log(f"===== {month} 쿠팡 분류 시작 =====")
    if not run("process_data(별점감성+정규식키워드)",
               ["python", str(ROOT/"scripts"/"process_data.py"),
                "--brand", BRAND, "--month", month, "--input", str(csv)], hard=True):
        return
    n = preseed_staronly(month)
    log(f"  별점만 {n}건은 감성 체크 제외(별점감성 유지) — 본문 리뷰만 Claude 검토")
    run("recheck_sentiment(본문만·불일치교정)",
        ["python", str(ROOT/"scripts"/"recheck_sentiment.py"),
         "--brand", BRAND, "--months", month, "--full"], hard=False)
    log(f"===== {month} 종료 =====")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="2026-03,2026-04,2026-05,2026-06,2026-07")
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    log(f"쿠팡 분류 시작 — {months} (자사몰 미접촉, push 안 함)")
    for m in months:
        do_month(m)
    log("전체 완료. docs/data/쿠팡/{월} 확인 → merge 준비.")


if __name__ == "__main__":
    main()
