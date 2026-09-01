"""merge_discovered_keywords.py <옵션>
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
discover_keywords.py가 만든 keyword_candidates.json의 후보 전체를 keywords.json의
by_intent.complaint/improvement에 정식 반영(일괄 채택)한다. 대시보드 kwcandAdopt()는
브라우저 localStorage(Taxonomy)에만 반영돼 다른 사람에게 안 보이므로, 팀 전체가 보는
정적 배포(keywords.json)에 반영하려면 이 스크립트를 쓴다.

- category는 비워둔다 → 대시보드 imGroupKeywordsIntoBlocks()의 폴백 규칙(구버전/발굴 키워드용)이
  이미 이 케이스를 위해 준비돼 있음.
- ai_reclassified=True로 표시(이미 AI 클러스터링으로 생성된 후보라 reverify_suspect 대상 아님).
- source="discovered"로 원본 출처 표시(디버깅용, 대시보드는 안 읽음).

사용:
  python scripts/merge_discovered_keywords.py --brand 슬룸 --months 2026-06,2026-07
"""
import argparse, json, shutil, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def merge_month(brand: str, month: str, max_samples: int = 50) -> bool:
    d = ROOT / "docs" / "data" / brand / month
    rpath = d / "reviews.json"
    kpath = d / "keywords.json"
    cpath = d / "keyword_candidates.json"
    if not cpath.is_file():
        eprint(f"  [{month}] keyword_candidates.json 없음 — 스킵")
        return False
    if not rpath.is_file() or not kpath.is_file():
        eprint(f"  [{month}] reviews.json/keywords.json 없음 — 스킵")
        return False

    reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
    cdata = json.loads(cpath.read_text(encoding="utf-8"))
    candidates = cdata.get("candidates") or []
    if not candidates:
        eprint(f"  [{month}] 후보 0개 — 스킵")
        return False

    kdata = json.loads(kpath.read_text(encoding="utf-8"))
    kdata.setdefault("by_intent", {})
    kdata["by_intent"].setdefault("complaint", [])
    kdata["by_intent"].setdefault("improvement", [])

    # 이미 채택된 word는 중복 추가 방지
    existing_words = {
        (k.get("word") or "") for grp in ("complaint", "improvement")
        for k in kdata["by_intent"].get(grp, [])
    }

    added = {"complaint": 0, "improvement": 0}
    for c in candidates:
        word = (c.get("word") or "").strip()
        ctype = c.get("type") if c.get("type") in ("complaint", "improvement") else "complaint"
        if not word or word in existing_words:
            continue
        ids = [str(rid) for rid in (c.get("review_ids") or []) if str(rid) in reviews]
        if not ids:
            continue

        by_product_cnt = {}
        samples = []
        for rid in ids:
            r = reviews.get(rid) or {}
            prod = r.get("product") or "(상품 미상)"
            by_product_cnt[prod] = by_product_cnt.get(prod, 0) + 1
        by_product = [{"product": p, "count": n}
                      for p, n in sorted(by_product_cnt.items(), key=lambda x: -x[1])]
        for rid in ids[:max_samples]:
            r = reviews.get(rid) or {}
            samples.append({
                "review_id": rid,
                "rating": r.get("rating"),
                "date": r.get("date"),
                "text": r.get("text") or "",
                "product": r.get("product") or "",
                "is_set": bool(r.get("is_set")),
            })

        entry = {
            "word": word,
            "count": len(ids),
            "category": None,          # 대시보드 폴백 그룹핑 규칙이 처리
            "reviews": ids,
            "by_product": by_product,
            "review_samples": samples,
            "all_review_ids": ids,
            "ai_reclassified": True,   # discover_keywords.py의 AI 클러스터링 결과 — 이미 AI 검증됨
            "source": "discovered",
        }
        kdata["by_intent"][ctype].append(entry)
        existing_words.add(word)
        added[ctype] += 1

    if added["complaint"] == 0 and added["improvement"] == 0:
        eprint(f"  [{month}] 신규 채택 0개(이미 반영됐거나 대상 없음)")
        return False

    bak = kpath.with_suffix(".json.bak2")
    if not bak.is_file():
        shutil.copy2(kpath, bak)
    kpath.write_text(json.dumps(kdata, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  [{month}] 채택 완료 — complaint +{added['complaint']}개, improvement +{added['improvement']}개")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--months", required=True, help="콤마 구분, 예: 2026-06,2026-07")
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    eprint(f"[merge_discovered_keywords] {args.brand} — {len(months)}개월 후보 일괄 채택")
    ok = 0
    for m in months:
        if merge_month(args.brand, m):
            ok += 1
    eprint(f"[DONE] {ok}/{len(months)}개월 반영")


if __name__ == "__main__":
    main()
