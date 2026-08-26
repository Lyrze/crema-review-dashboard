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


def rebuild_month(brand: str, month: str, top_n: int = 30, only: list = None) -> bool:
    only = only or ["complaint", "improvement"]
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

    # complaint/improvement 중 --only로 지정된 것만 교체 — praise·나머지는 항상 보존.
    # (기본은 둘 다. 한쪽 패턴만 바뀐 경우 --only complaint 로 좁혀서, 이미 AI 재검증
    #  끝난 반대쪽을 raw 상태로 되돌리는 낭비를 피한다.)
    kdata.setdefault("by_intent", {})
    for key in only:
        kdata["by_intent"][key] = result["by_intent"][key]

    # 백업(멱등 — 이미 있으면 최초 원본만 유지)
    bak = kpath.with_suffix(".json.bak")
    if not bak.is_file():
        shutil.copy2(kpath, bak)

    kpath.write_text(json.dumps(kdata, ensure_ascii=False, indent=2), encoding="utf-8")

    # 재계산한 polarity의 reverify 완료 마커를 무효화 — 안 그러면 reverify_suspect.py가
    # "이미 완료(__done_pol__)"로 오판해 방금 리셋된 raw 데이터를 검증 없이 건너뛴다
    # (실제로 겪은 버그: rebuild 후 __done_pol__이 그대로 남아 재검증이 통째로 스킵됨).
    prog_path = d / ".reverify_progress.json"
    if prog_path.is_file():
        try:
            prog = json.loads(prog_path.read_text(encoding="utf-8"))
        except Exception:
            prog = {}
        changed = False
        dp = prog.get("__done_pol__", {}) or {}
        for engine, pols in list(dp.items()):
            new_pols = [p for p in pols if p not in only]
            if new_pols != pols:
                dp[engine] = new_pols
                changed = True
        prog["__done_pol__"] = dp
        for engine in list(prog.keys()):
            if engine == "__done_pol__":
                continue
            items = prog.get(engine) or []
            new_items = [t for t in items if not any(t.startswith(f"{k}::") for k in only)]
            if new_items != items:
                prog[engine] = new_items
                changed = True
        if changed:
            prog_path.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")
            eprint(f"  [{month}] .reverify_progress.json에서 {only} 완료마커 무효화(재검증 필요 상태로 리셋)")

    parts = []
    if "complaint" in only:
        n = len(result["by_intent"]["complaint"])
        c = sum(x.get("count", 0) for x in result["by_intent"]["complaint"])
        parts.append(f"complaint {old_complaint_n}개/{old_complaint_cnt}건 → {n}개/{c}건")
    if "improvement" in only:
        n = len(result["by_intent"]["improvement"])
        c = sum(x.get("count", 0) for x in result["by_intent"]["improvement"])
        parts.append(f"improvement {old_improve_n}개/{old_improve_cnt}건 → {n}개/{c}건")
    eprint(f"  [{month}] " + "  |  ".join(parts))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--months", required=True, help="쉼표구분 YYYY-MM 목록")
    ap.add_argument("--top-n", type=int, default=30)
    ap.add_argument("--only", default="complaint,improvement",
                     help="재계산할 대상만 쉼표구분(complaint / improvement / 둘 다). "
                          "한쪽 패턴만 바뀐 경우 반대쪽의 기완료 AI 재검증을 보존하려면 좁혀서 지정.")
    args = ap.parse_args()

    months = [m.strip() for m in args.months.split(",") if m.strip()]
    only = [o.strip() for o in args.only.split(",") if o.strip()]
    eprint(f"[rebuild_keywords] {args.brand} — {len(months)}개월 재추출 시작 (대상: {only})")
    ok = 0
    for month in months:
        if rebuild_month(args.brand, month, top_n=args.top_n, only=only):
            ok += 1
    eprint(f"[DONE] {ok}/{len(months)}개월 완료")
    if ok < len(months):
        sys.exit(1)


if __name__ == "__main__":
    main()
