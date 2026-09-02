"""discover_praise_keywords.py <옵션>
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
긍정 감성인데 기존 '칭찬(praise)' 키워드에 안 잡힌 리뷰(미포착 호평)를 AI로 묶어
신규 키워드 후보를 발굴한다. discover_keywords.py(부정 전용)의 긍정판 — 구조는
동일하되 대상 풀과 프롬프트만 praise에 맞춤. 결과는 keyword_candidates_praise.json
에만 저장하고 keywords.json은 건드리지 않는다(검토형).

흐름:
  reviews.json(sentiment) + keywords.json(praise 매칭 id) →
  긍정인데 미포착 리뷰 수집 → AI 클러스터링 → {word, type:"praise", count, review_ids, samples}

사용:
  python scripts/discover_praise_keywords.py --brand 슬룸 --month 2026-06
"""
import argparse, json, sys, time, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-samples", type=int, default=500, help="AI에 넣을 미포착 리뷰 최대 수(상한)")
    ap.add_argument("--batch-size", type=int, default=50, help="배치당 리뷰 수(순차 처리 단위)")
    ap.add_argument("--timeout", type=int, default=150, help="배치 1개당 하드 타임아웃(초)")
    args = ap.parse_args()
    model = args.model or "sonnet"

    from claude_engine import ClaudeAnalyzer, is_quota, extract_json_from_response  # noqa: E402

    d = ROOT / "docs" / "data" / args.brand / args.month
    rpath = d / "reviews.json"
    kpath = d / "keywords.json"
    if not rpath.is_file():
        eprint("[ERROR] reviews.json 없음"); sys.exit(1)
    reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})

    # praise 키워드가 이미 잡은 review_id 집합
    captured = set()
    if kpath.is_file():
        kdata = json.loads(kpath.read_text(encoding="utf-8"))
        bi = kdata.get("by_intent", {}) or {}
        for kw in bi.get("praise", []) or []:
            for rid in (kw.get("all_review_ids") or []):
                captured.add(str(rid))

    # 긍정 감성인데 미포착 + 본문 있는(별점만 남긴 것 제외) 리뷰
    uncap = [(rid, (r.get("text") or "").strip(), r.get("rating"))
             for rid, r in reviews.items()
             if r.get("sentiment") == "positive" and str(rid) not in captured
             and (r.get("text") or "").strip() and "별점만 남기고" not in (r.get("text") or "")]
    eprint(f"  {args.brand}/{args.month}: 긍정 미포착 {len(uncap)}건")

    out = {"brand": args.brand, "month": args.month, "generated_at": "",
           "source": "uncaptured_positive", "uncaptured_total": len(uncap), "candidates": []}
    if len(uncap) < 5:
        eprint("  발굴 대상 적음(5건 미만) — 후보 없음으로 저장")
        (d / "keyword_candidates_praise.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(0)

    analyzer = ClaudeAnalyzer(model=model)
    if not analyzer.health_check():
        err = str(getattr(analyzer, "last_error", "") or "")
        if is_quota(err):
            eprint(f"[STOP] 한도 소진 — 재실행 시 이어집니다. ({err[:200]})"); sys.exit(3)
        eprint(f"[ERROR] Claude 무응답 ({err})"); sys.exit(2)

    pool = uncap[: args.max_samples]
    B = max(10, args.batch_size)
    nb = (len(pool) + B - 1) // B
    eprint(f"  AI 클러스터링({model}) — {len(pool)}건 / {nb}배치(배치당 {B}건) 순차 분석...")

    raw_clusters = []  # 배치별 결과 누적: {"word","review_ids":[id],"texts":[(rt,tx)]}
    ex = ThreadPoolExecutor(max_workers=1)
    t0 = time.time()
    done_batches = 0
    for bi in range(nb):
        batch = pool[bi * B:(bi + 1) * B]
        lines = [f"[{i}] {t.replace(chr(10), ' ')[:160]}" for i, (_rid, t, _rt) in enumerate(batch)]
        prompt = (
            "다음은 '긍정적이지만 기존 칭찬 키워드에 안 잡힌' 제품 리뷰들이다. "
            "반복되는 칭찬·만족 포인트를 5~10개의 키워드로 묶어라.\n"
            "- word: 짧은 한국어 명사구(예: '무선 편의성', '수면 유도 효과', '패키지 구성')\n"
            "- 단순 감탄사('좋아요','최고')만 있고 구체적 이유가 없는 리뷰는 제외하라. "
            "구체적인 제품 경험·기능·효과에 대한 칭찬만 클러스터링 대상이다.\n"
            "- reviews: 그 키워드에 해당하는 위 리뷰 번호 배열\n\n"
            "[리뷰]\n" + "\n".join(lines) + "\n\n"
            'JSON 배열로만 출력: [{"word":"키워드","reviews":[0,2]}]'
        )
        eprint(f"   · 배치 {bi + 1}/{nb} ({len(batch)}건) 분석 중...")
        try:
            fut = ex.submit(analyzer.client.generate, model=analyzer.model, prompt=prompt,
                            system="당신은 한국어 VOC 분석 전문가입니다. JSON으로만 답하세요.", temperature=0.1)
            raw = fut.result(timeout=args.timeout)
        except FTimeout:
            eprint(f"   · [TIMEOUT] 배치 {bi + 1} 건너뜀(재실행 시 이어서)")
            try: ex.shutdown(wait=False, cancel_futures=True)
            except Exception: pass
            ex = ThreadPoolExecutor(max_workers=1)
            continue
        except Exception as e:
            if is_quota(e):
                eprint(f"   · [STOP] 한도 소진(배치 {bi + 1}/{nb}) — 재실행 시 처음부터 다시 돕니다(발굴은 저비용). ({str(e)[:150]})")
                sys.exit(3)
            eprint(f"   · [ERR] 배치 {bi + 1}: {str(e)[:100]}")
            continue
        done_batches += 1
        parsed = extract_json_from_response(raw)
        clusters = parsed if isinstance(parsed, list) else (parsed.get("items") or parsed.get("candidates") if isinstance(parsed, dict) else None)
        if not isinstance(clusters, list):
            continue
        for c in clusters:
            if not isinstance(c, dict):
                continue
            word = str(c.get("word", "")).strip()
            if not word:
                continue
            idxs = [i for i in (c.get("reviews") or []) if isinstance(i, int) and 0 <= i < len(batch)]
            if not idxs:
                continue
            texts = [(batch[i][2], batch[i][1]) for i in idxs]
            raw_clusters.append({
                "word": word, "review_ids": [batch[i][0] for i in idxs], "texts": texts,
            })

    # ── 배치 간 병합: 정규화 단어가 같거나 한쪽이 다른쪽을 포함하면 합침 ──
    def _norm(w):
        return re.sub(r"[^가-힣a-zA-Z0-9]", "", str(w)).lower()

    merged = []  # {"word","keys":set,"rids":set,"texts":[]}
    for rc in raw_clusters:
        k = _norm(rc["word"])
        if not k:
            continue
        hit = None
        for m in merged:
            if any(k == mk or (len(k) >= 2 and len(mk) >= 2 and (k in mk or mk in k)) for mk in m["keys"]):
                hit = m
                break
        if hit is None:
            merged.append({"word": rc["word"], "keys": {k}, "rids": set(rc["review_ids"]), "texts": list(rc["texts"])})
        else:
            hit["keys"].add(k)
            hit["rids"].update(rc["review_ids"])
            hit["texts"].extend(rc["texts"])
            if len(rc["word"]) < len(hit["word"]):
                hit["word"] = rc["word"]

    cands = []
    for m in merged:
        seen, samples = set(), []
        for rt, tx in m["texts"]:
            key = (tx or "")[:60]
            if key in seen:
                continue
            seen.add(key)
            samples.append({"rating": rt, "text": (tx or "")[:160]})
            if len(samples) >= 4:
                break
        cands.append({"word": m["word"], "type": "praise", "count": len(m["rids"]),
                      "review_ids": [str(r) for r in m["rids"]], "samples": samples})
    cands.sort(key=lambda x: x["count"], reverse=True)

    out["analyzed_total"] = len(pool)
    out["batches"] = {"total": nb, "ok": done_batches}
    out["candidates"] = cands
    (d / "keyword_candidates_praise.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  [OK] 신규 칭찬 키워드 후보 {len(cands)}개 발굴(배치 {done_batches}/{nb} 성공, {round(time.time()-t0)}s) → {d / 'keyword_candidates_praise.json'}")
    for c in cands:
        eprint(f"     · {c['word']} ({c['count']})")


if __name__ == "__main__":
    main()
