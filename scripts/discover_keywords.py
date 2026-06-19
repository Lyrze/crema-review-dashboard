"""discover_keywords.py <옵션>
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
부정 감성인데 기존 '불만' 키워드에 안 잡힌 리뷰(미포착 불만)를 AI로 묶어
신규 키워드 후보를 발굴한다. 결과는 keyword_candidates.json 에만 저장하고
keywords.json 은 건드리지 않는다(검토형 — 대시보드에서 사람이 채택/무시).

흐름:
  reviews.json(sentiment) + keywords.json(complaint 매칭 id) →
  부정인데 미포착 리뷰 수집 → AI 클러스터링 → {word, type(complaint|improvement), count, review_ids, samples}

사용:
  python scripts/discover_keywords.py --brand 슬룸 --month 2026-05 --model qwen2.5:7b
"""
import argparse, json, sys, time, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 개선요청 신호어 — 미포착 부정 중 '변화 요구'를 improvement 로 우선 분류하는 힌트
_IMPROVE_HINT = ("했으면", "하면 좋", "되면 좋", "개선", "추가", "바랍니다", "원해요", "있으면",
                 "좋겠", "였으면", "더 ", "조절", "기능이 있")


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--model", "--ollama-model", dest="model", default="qwen2.5:7b")
    ap.add_argument("--base-url", "--ollama-url", dest="base_url", default="http://localhost:11434")
    ap.add_argument("--max-samples", type=int, default=60, help="AI에 넣을 미포착 리뷰 최대 수")
    ap.add_argument("--timeout", type=int, default=150)
    args = ap.parse_args()

    from ollama_analysis import OllamaAnalyzer, extract_json_from_response  # noqa: E402

    d = ROOT / "docs" / "data" / args.brand / args.month
    rpath = d / "reviews.json"
    kpath = d / "keywords.json"
    if not rpath.is_file():
        eprint("[ERROR] reviews.json 없음"); sys.exit(1)
    reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})

    # complaint 키워드가 이미 잡은 review_id 집합
    captured = set()
    if kpath.is_file():
        kdata = json.loads(kpath.read_text(encoding="utf-8"))
        for kw in (kdata.get("by_intent", {}) or {}).get("complaint", []):
            for rid in (kw.get("all_review_ids") or []):
                captured.add(str(rid))

    # 부정 감성인데 미포착 + 본문 있는 리뷰
    uncap = [(rid, (r.get("text") or "").strip(), r.get("rating"))
             for rid, r in reviews.items()
             if r.get("sentiment") == "negative" and str(rid) not in captured and (r.get("text") or "").strip()]
    eprint(f"  {args.brand}/{args.month}: 부정 미포착 {len(uncap)}건")

    out = {"brand": args.brand, "month": args.month, "generated_at": "",
           "source": "uncaptured_negative", "uncaptured_total": len(uncap), "candidates": []}
    if len(uncap) < 5:
        eprint("  발굴 대상 적음(5건 미만) — 후보 없음으로 저장")
        (d / "keyword_candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(0)

    analyzer = OllamaAnalyzer(model=args.model, base_url=args.base_url)
    if not analyzer.health_check():
        eprint("[ERROR] Ollama 무응답"); sys.exit(2)

    sample = uncap[: args.max_samples]
    lines = [f"[{i}] {t.replace(chr(10), ' ')[:160]}" for i, (_rid, t, _rt) in enumerate(sample)]
    prompt = (
        "다음은 '부정적이지만 기존 불만 키워드에 안 잡힌' 제품 리뷰들이다. "
        "반복되는 불만·개선요청을 5~10개의 키워드로 묶어라.\n"
        "- word: 짧은 한국어 명사구(예: '에어튜브 크기', '버튼 위치', '진동 강도')\n"
        "- type: 'complaint'(불만·문제) 또는 'improvement'(개선요청 — ~했으면/추가/개선/조절 등 변화 요구)\n"
        "- reviews: 그 키워드에 해당하는 위 리뷰 번호 배열\n\n"
        "[리뷰]\n" + "\n".join(lines) + "\n\n"
        'JSON 배열로만 출력: [{"word":"키워드","type":"complaint","reviews":[0,2]}]'
    )
    eprint(f"  AI 클러스터링({args.model}) — {len(sample)}건 분석...")
    ex = ThreadPoolExecutor(max_workers=1)
    raw = ""
    try:
        fut = ex.submit(analyzer.client.generate, model=analyzer.model, prompt=prompt,
                        system="당신은 한국어 VOC 분석 전문가입니다. JSON으로만 답하세요.", temperature=0.1)
        raw = fut.result(timeout=args.timeout)
    except FTimeout:
        eprint("  [TIMEOUT] 클러스터링 — 후보 없이 저장(나중에 재실행)")
        (d / "keyword_candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(3)
    except Exception as e:
        eprint(f"  [ERR] {str(e)[:120]}")
        (d / "keyword_candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(3)

    parsed = extract_json_from_response(raw)
    clusters = parsed if isinstance(parsed, list) else (parsed.get("items") or parsed.get("candidates") if isinstance(parsed, dict) else None)
    cands = []
    if isinstance(clusters, list):
        for c in clusters:
            if not isinstance(c, dict):
                continue
            word = str(c.get("word", "")).strip()
            if not word:
                continue
            idxs = [i for i in (c.get("reviews") or []) if isinstance(i, int) and 0 <= i < len(sample)]
            if not idxs:
                continue
            rids = [sample[i][0] for i in idxs]
            texts = [(sample[i][2], sample[i][1]) for i in idxs]
            ctype = str(c.get("type", "complaint")).strip().lower()
            if ctype not in ("complaint", "improvement"):
                # 힌트로 보정
                joined = " ".join(t for _, t in texts)
                ctype = "improvement" if any(h in joined for h in _IMPROVE_HINT) else "complaint"
            cands.append({
                "word": word, "type": ctype, "count": len(rids),
                "review_ids": [str(r) for r in rids],
                "samples": [{"rating": rt, "text": tx[:160]} for rt, tx in texts[:4]],
            })
    cands.sort(key=lambda x: x["count"], reverse=True)
    out["candidates"] = cands
    (d / "keyword_candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  [OK] 신규 키워드 후보 {len(cands)}개 발굴 → {d / 'keyword_candidates.json'}")
    for c in cands:
        eprint(f"     · [{c['type']}] {c['word']} ({c['count']})")


if __name__ == "__main__":
    main()
