"""patch_improvement.py <YYYY-MM> [브랜드]
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
keywords.json 의 by_intent.improvement(개선요청 버킷)만 '안전하게' 재계산한다.

- reviews.json 본문에 확장된 IMPROVEMENT_PATTERNS(정규식)를 매칭 → 개선요청 키워드 갱신.
- AI 호출 없음(정규식만) → 빠르고 hang 위험 없음.
- 감성/불만(complaint)/칭찬(praise)/요약/상품 등 다른 데이터는 일절 건드리지 않음.
- by_product · review_samples · all_review_ids 까지 대시보드 포맷 그대로 채운다.

사용:
  python scripts/patch_improvement.py 2026-05
  python scripts/patch_improvement.py 2026-05 슬룸
"""
import sys, json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["PYTHONUTF8"] = "1"
sys.path.insert(0, str(ROOT / "scripts"))
from process_data import _COMPILED_IMPROVEMENT, _match_compiled_patterns  # noqa: E402

month = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
brand = sys.argv[2] if len(sys.argv) > 2 else "슬룸"
TOP_N = 30
PER_PRODUCT = 8
MAX_SAMPLES = 50

d = ROOT / f"docs/data/{brand}/{month}"
rpath, kpath = d / "reviews.json", d / "keywords.json"
if not rpath.is_file() or not kpath.is_file():
    print(f"[ERROR] {rpath} 또는 {kpath} 없음"); sys.exit(1)

reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
kdata = json.loads(kpath.read_text(encoding="utf-8"))

# '부정 감성 또는 별점<=3'만 대상 — ★5인데 감성 neutral인 칭찬 리뷰까지 새는 것 차단
# 안정 순서 — texts/ids 의 위치 인덱스가 _matched_indices 와 1:1 대응
def _imp_target(r):
    if (r.get("sentiment") or "") == "negative":
        return True
    try:
        return int(r.get("rating") or 0) <= 3
    except (TypeError, ValueError):
        return False
ids = [rid for rid, r in reviews.items() if _imp_target(r)]
recs = [reviews[i] for i in ids]
texts = [(r.get("text") or "") for r in recs]
print(f"  개선요청 대상(부정 또는 별점<=3) {len(ids)} / 전체 {len(reviews)}")

items = _match_compiled_patterns(_COMPILED_IMPROVEMENT, texts, ids)

for it in items:
    midx = it.get("_matched_indices", [])
    allids = [str(x) for x in it.get("_all_review_ids", [])]
    # by_product 분포
    bp = {}
    for rid in allids:
        p = (reviews.get(rid, {}) or {}).get("product")
        if p:
            bp[p] = bp.get(p, 0) + 1
    it["by_product"] = [{"product": p, "count": c} for p, c in sorted(bp.items(), key=lambda x: -x[1])]
    # review_samples (상품당 최대 PER_PRODUCT, 전체 최대 MAX_SAMPLES)
    samples, seenp = [], {}
    for i in midx:
        if len(samples) >= MAX_SAMPLES:
            break
        r = recs[i]; p = r.get("product", "") or ""
        if seenp.get(p, 0) >= PER_PRODUCT:
            continue
        seenp[p] = seenp.get(p, 0) + 1
        samples.append({
            "review_id": ids[i],
            "rating": int(r.get("rating") or 0),
            "date": r.get("date") or "",
            "text": (r.get("text") or "")[:300],
            "product": p,
        })
    it["review_samples"] = samples
    it["all_review_ids"] = allids
    for k in ("_matched_indices", "_all_review_ids", "_pattern"):
        it.pop(k, None)

items = items[:TOP_N]
kdata.setdefault("by_intent", {})["improvement"] = items
kpath.write_text(json.dumps(kdata, ensure_ascii=False, indent=2), encoding="utf-8")

total = sum(it["count"] for it in items)
print(f"{brand}/{month}: 개선요청 {len(items)}개 재계산 (총 매칭 {total}건)")
for it in items:
    print(f"   · {it['word']} ({it['count']})")
