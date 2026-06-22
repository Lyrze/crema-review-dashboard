"""backfill_review_meta.py <YYYY-MM> [브랜드]
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
기존 reviews.json 에 세그먼트 분석용 메타(grade=회원등급, option=상품옵션,
channel=리뷰작성경로)를 익명화 CSV에서 조인해 추가한다.

- 리뷰ID 로 조인 (reviews.json 키 == 익명화 CSV '리뷰ID').
- 기존 필드(rating/date/product/text/sentiment)는 건드리지 않고 메타만 덧붙임.
- AI 호출 없음, 빠름, 안전.

사용:
  python scripts/backfill_review_meta.py 2026-05
  python scripts/backfill_review_meta.py 2026-05 슬룸
"""
import sys, json, csv, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["PYTHONUTF8"] = "1"

month = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
brand = sys.argv[2] if len(sys.argv) > 2 else "슬룸"

rpath = ROOT / f"docs/data/{brand}/{month}/reviews.json"
# 원본 raw 우선(리뷰ID 실제값 + 채널 100%), 없으면 익명화 CSV 폴백
raw_path = ROOT / f"data/raw/{brand}/{month}/reviews.csv"
anon_path = ROOT / f"data/anonymized/{brand}/{month}/reviews_anon.csv"
cpath = raw_path if raw_path.is_file() else anon_path
if not rpath.is_file():
    print(f"[ERROR] {rpath} 없음"); sys.exit(1)
if not cpath.is_file():
    print(f"[WARN] CSV 없음({raw_path} / {anon_path}) — 메타 백필 건너뜀")
    sys.exit(0)

doc = json.loads(rpath.read_text(encoding="utf-8"))
reviews = doc.get("reviews", {})

# CSV → {리뷰ID: {grade, option, channel}}   (utf-8-sig 로 BOM 자동 제거)
meta = {}
with open(cpath, encoding="utf-8-sig") as f:
    rd = csv.DictReader(f)
    def col(row, name):
        return row.get(name) or row.get("﻿" + name) or ""
    for row in rd:
        rid = str(col(row, "리뷰ID")).strip()
        if not rid:
            continue
        meta[rid] = {
            "grade": (col(row, "회원등급") or "").strip(),
            "option": (col(row, "상품옵션") or "").strip()[:80],
            "channel": (col(row, "리뷰작성경로") or "").strip(),
        }
print(f"  CSV 소스: {cpath.relative_to(ROOT)}")

added = 0
for rid, rec in reviews.items():
    m = meta.get(str(rid))
    if not m:
        continue
    if m["grade"]:
        rec["grade"] = m["grade"]
    if m["option"]:
        rec["option"] = m["option"]
    if m["channel"]:
        rec["channel"] = m["channel"]
    added += 1

rpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{brand}/{month}: 메타 백필 {added}/{len(reviews)} (grade/option/channel)")
# 분포 미리보기
from collections import Counter
gc = Counter((reviews[r].get("grade") or "(없음)") for r in reviews)
cc = Counter((reviews[r].get("channel") or "(없음)") for r in reviews)
print("  회원등급:", dict(gc.most_common(6)))
print("  채널:", dict(cc.most_common(6)))
