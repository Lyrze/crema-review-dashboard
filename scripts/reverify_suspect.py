"""reverify_suspect.py
~~~~~~~~~~~~~~~~~~~~~~~
7b 전체 재분류(--reclassify-full) 이후, 의심 키워드의 **현재 멤버만**
더 큰 모델(기본 qwen2.5:14b)로 재검증해 거짓양성(긍정·희망·무관 누수)을 제거한다.

특징
----
- 후보 재확장 없음: keywords.json 의 현재 all_review_ids 멤버만 재판정 → 제거만 발생(추가 X).
  따라서 안전하고 빠르다(키워드당 수 건 수준).
- 검증 로직은 ollama_analysis.verify_keyword_reviews 의 3단계 게이트를 그대로 사용.
- write-back 포맷은 process_data.reclassify_keyword_full 과 동일
  (all_review_ids / count / review_samples / by_product / ai_reclassified).

사용
----
    python scripts/reverify_suspect.py --brand 슬룸 --month 2026-04 --model qwen2.5:14b

update-data.bat 의 [3.5/4] 단계에서 자동 호출된다.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # ollama_analysis 임포트용

POLARITY_MAP = {"complaint": "부정", "improvement": "개선", "praise": "긍정"}


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def reverify_month(brand: str, month: str, model: str, base_url: str, polarities: list) -> bool:
    """한 브랜드/월의 keywords.json 을 재검증해 in-place 저장. 성공 시 True."""
    from ollama_analysis import OllamaAnalyzer  # type: ignore[import]

    data_dir = ROOT / "docs" / "data" / brand / month
    kpath = data_dir / "keywords.json"
    rpath = data_dir / "reviews.json"
    if not kpath.is_file():
        eprint(f"  [ERROR] keywords.json 없음: {kpath}")
        return False
    if not rpath.is_file():
        eprint(f"  [ERROR] reviews.json 없음: {rpath} (재검증에는 전체 리뷰 인덱스가 필요)")
        return False

    analyzer = OllamaAnalyzer(model=model, base_url=base_url)
    if not analyzer.health_check():
        eprint(f"  [ERROR] Ollama 응답 없음 ({base_url}) — 재검증 건너뜀")
        return False
    eprint(f"  [OK] Ollama 정상, 정밀 보정 모델={model}")

    kw = json.loads(kpath.read_text(encoding="utf-8"))
    rv_idx = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
    bi = kw.get("by_intent", {})

    changed_total = 0
    for key in polarities:
        polarity = POLARITY_MAP.get(key)
        if not polarity:
            continue
        for item in bi.get(key, []):
            word = str(item.get("word", ""))
            members = [str(x) for x in item.get("all_review_ids", [])]
            if not members:
                continue
            samples = []
            for rid in members:
                rv = rv_idx.get(rid)
                if not rv:
                    continue
                samples.append({
                    "review_id": rid,
                    "text": rv.get("text", ""),
                    "rating": rv.get("rating", 0),
                })
            if not samples:
                continue
            before = len(samples)
            try:
                kept = analyzer.verify_keyword_reviews(word, polarity, samples, mode="batch")
            except Exception as exc:  # noqa: BLE001
                eprint(f"  [WARN] '{word}' 재검증 실패, 유지: {exc}")
                continue
            kept_ids = [str(s.get("review_id")) for s in kept if s.get("review_id")]

            # ── write-back (reclassify_keyword_full 과 동일 포맷) ──
            item["all_review_ids"] = kept_ids
            item["count"] = len(kept_ids)
            item["ai_reclassified"] = True
            new_samples = []
            per_prod = {}
            for rid in kept_ids:
                if len(new_samples) >= 50:
                    break
                rv = rv_idx.get(rid)
                if not rv:
                    continue
                pn = rv.get("product", "")
                if per_prod.get(pn, 0) >= 8:
                    continue
                per_prod[pn] = per_prod.get(pn, 0) + 1
                new_samples.append({
                    "review_id": rid,
                    "rating": rv.get("rating", 0),
                    "date": rv.get("date", ""),
                    "text": str(rv.get("text", ""))[:300],
                    "product": pn,
                })
            item["review_samples"] = new_samples
            bp = {}
            for rid in kept_ids:
                rv = rv_idx.get(rid)
                if rv and rv.get("product"):
                    bp[rv["product"]] = bp.get(rv["product"], 0) + 1
            item["by_product"] = [
                {"product": p, "count": c}
                for p, c in sorted(bp.items(), key=lambda x: -x[1])
            ]
            removed = before - len(kept_ids)
            if removed:
                changed_total += 1
            flag = "  <== 제거" if removed else ""
            eprint(f"  [{key}] {word}: {before} -> {len(kept_ids)} (-{removed}){flag}")

    kpath.write_text(
        json.dumps(kw, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    eprint(f"  [OK] 저장 완료: {kpath}  (변경된 키워드 {changed_total}개)")
    return True


def main():
    ap = argparse.ArgumentParser(description="의심 키워드 멤버를 더 큰 모델로 재검증(거짓양성 제거)")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--model", default="qwen2.5:14b", help="재검증 모델 (기본 qwen2.5:14b)")
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument(
        "--polarities",
        default="complaint,improvement",
        help="재검증 대상 의도 (쉼표구분: complaint,improvement,praise). 기본 complaint,improvement",
    )
    args = ap.parse_args()
    polarities = [p.strip() for p in args.polarities.split(",") if p.strip()]

    eprint(f"  의심 키워드 정밀 보정: {args.brand} / {args.month}  대상={polarities}")
    ok = reverify_month(args.brand, args.month, args.model, args.base_url, polarities)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
