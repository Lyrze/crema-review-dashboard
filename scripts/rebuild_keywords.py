"""rebuild_keywords.py <옵션>
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
process_data.py의 COMPLAINT_PATTERNS/IMPROVEMENT_PATTERNS를 수정한 뒤,
이미 3채널(자사몰+스마트스토어+쿠팡)이 병합된 기존 월의 keywords.json에
새 패턴을 반영하기 위한 스크립트.

⚠️ process_data.py를 원본 CSV로 재실행하면 안 되는 이유:
   merge_smartstore.py / convert_coupang.py 가 process_data.py 실행 "이후"에
   별도로 스마트스토어·쿠팡 리뷰를 JSON에 병합한다. process_data.py를 다시 돌리면
   자사몰 CSV만으로 새로 만들어져 이미 병합된 두 채널 리뷰가 통째로 사라진다.

그래서 이 스크립트는 원본 CSV가 아니라 "이미 병합된" docs/data/{브랜드}/{월}/reviews.json
을 입력으로 삼아, by_intent.complaint / by_intent.improvement 두 필드만 재계산해
덮어쓴다(praise·sentiment·상품통계·다른 파일은 전혀 건드리지 않음).

패턴 로직은 process_data.py의 extract_keywords_basic()을 그대로 import해서 재사용한다
(로직 이중관리 방지 — 패턴 수정은 process_data.py 한 곳에서만).

사용:
  python scripts/rebuild_keywords.py --brand 슬룸 --months 2026-03,2026-04,2026-05,2026-06,2026-07
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from process_data import extract_keywords_basic  # noqa: E402


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def build_dataframe(reviews: dict) -> pd.DataFrame:
    """reviews.json의 {review_id: {rating,date,product,text,sentiment}} →
    extract_keywords_basic()이 기대하는 컬럼(body/review_id/rating/sentiment/
    product_name/review_date)의 DataFrame으로 변환."""
    rows = []
    for rid, r in reviews.items():
        rows.append({
            "review_id": str(rid),
            "body": r.get("text") or "",
            "rating": r.get("rating") or 0,
            "sentiment": r.get("sentiment") or "",
            "product_name": r.get("product") or "",
            "review_date": r.get("date") or "",
        })
    return pd.DataFrame(rows)


def rebuild_month(brand: str, month: str, top_n: int = 30) -> bool:
    d = ROOT / "docs" / "data" / brand / month
    rpath = d / "reviews.json"
    kpath = d / "keywords.json"
    if not rpath.is_file():
        eprint(f"  [{month}] reviews.json 없음 — 건너뜀")
        return False
    if not kpath.is_file():
        eprint(f"  [{month}] keywords.json 없음 — 건너뜀")
        return False

    reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
    if not reviews:
        eprint(f"  [{month}] reviews.json에 리뷰 없음 — 건너뜀")
        return False

    df = build_dataframe(reviews)
    result = extract_keywords_basic(df, top_n=top_n)

    kdata = json.loads(kpath.read_text(encoding="utf-8"))
    old_bi = kdata.get("by_intent", {}) or {}
    old_complaint_n = len(old_bi.get("complaint", []) or [])
    old_improve_n = len(old_bi.get("improvement", []) or [])
    old_complaint_cnt = sum(x.get("count", 0) for x in (old_bi.get("complaint") or []))
    old_improve_cnt = sum(x.get("count", 0) for x in (old_bi.get("improvement") or []))

    # complaint/improvement만 교체 — praise·negative_keywords 등 나머지는 그대로 보존
    kdata.setdefault("by_intent", {})
    kdata["by_intent"]["complaint"] = result["by_intent"]["complaint"]
    kdata["by_intent"]["improvement"] = result["by_intent"]["improvement"]

    # 백업(멱등 — 이미 있으면 최초 원본만 유지)
    bak = kpath.with_suffix(".json.bak")
    if not bak.is_file():
        shutil.copy2(kpath, bak)

    kpath.write_text(json.dumps(kdata, ensure_ascii=False, indent=2), encoding="utf-8")

    new_complaint_n = len(result["by_intent"]["complaint"])
    new_improve_n = len(result["by_intent"]["improvement"])
    new_complaint_cnt = sum(x.get("count", 0) for x in result["by_intent"]["complaint"])
    new_improve_cnt = sum(x.get("count", 0) for x in result["by_intent"]["improvement"])

    eprint(
        f"  [{month}] complaint {old_complaint_n}개/{old_complaint_cnt}건 → "
        f"{new_complaint_n}개/{new_complaint_cnt}건  |  "
        f"improvement {old_improve_n}개/{old_improve_cnt}건 → "
        f"{new_improve_n}개/{new_improve_cnt}건"
    )
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--months", required=True, help="쉼표구분 YYYY-MM 목록")
    ap.add_argument("--top-n", type=int, default=30)
    args = ap.parse_args()

    months = [m.strip() for m in args.months.split(",") if m.strip()]
    eprint(f"[rebuild_keywords] {args.brand} — {len(months)}개월 재추출 시작")
    ok = 0
    for month in months:
        if rebuild_month(args.brand, month, top_n=args.top_n):
            ok += 1
    eprint(f"[DONE] {ok}/{len(months)}개월 완료")
    if ok < len(months):
        sys.exit(1)


if __name__ == "__main__":
    main()
