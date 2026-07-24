"""discover_keywords.py <옵션>
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
부정 감성인데 기존 '불만' 키워드에 안 잡힌 리뷰(미포착 불만)를 AI로 묶어
신규 키워드 후보를 발굴한다. 결과는 keyword_candidates.json 에만 저장하고
keywords.json 은 건드리지 않는다(검토형 — 대시보드에서 사람이 채택/무시).

흐름:
  reviews.json(sentiment) + keywords.json(complaint 매칭 id) →
  부정인데 미포착 리뷰 수집 → AI 클러스터링 → {word, type(complaint|improvement), count, review_ids, samples}

사용:
  python scripts/discover_keywords.py --brand 슬룸 --month 2026-05
"""
import argparse, json, sys, time, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 개선요청 신호어 — 미포착 부정 중 '변화 요구'를 improvement 로 우선 분류하는 힌트
# (덜 엄격하게: '있으면 좋겠다 / 됐으면 / 아쉽다 / 부족하다' 같은 약한 뉘앙스도 포함)
_IMPROVE_HINT = ("했으면", "하면 좋", "되면 좋", "면 좋겠", "으면 좋겠", "었으면", "였으면", "있으면",
                 "개선", "보완", "업그레이드", "강화", "추가", "생겼으면", "나왔으면",
                 "바랍니다", "바래요", "원해요", "주세요", "해주", "지원", "옵션", "선택할 수",
                 "필요", "아쉽", "아쉬", "부족", "없어서", "없네", "없으니", "안 되", "안되",
                 "조절", "기능이 있", "좋겠", "기대", "더 ")
# 강한 개선 신호 — AI가 complaint로 줘도 이 신호가 있으면 improvement 로 승격(과도 전환 방지용 보수적 집합)
_IMPROVE_STRONG = ("있으면 좋", "었으면 좋", "였으면 좋", "면 좋겠", "으면 좋겠", "했으면", "되면 좋",
                   "추가되", "추가해", "개선해", "개선되", "보완", "업그레이드", "지원해", "지원되",
                   "생겼으면", "나왔으면", "주세요", "필요해", "필요할")


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-samples", type=int, default=400, help="AI에 넣을 미포착 리뷰 최대 수(상한)")
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

    # complaint 키워드가 이미 잡은 review_id 집합
    captured = set()
    if kpath.is_file():
        kdata = json.loads(kpath.read_text(encoding="utf-8"))
        bi = kdata.get("by_intent", {}) or {}
        # complaint + improvement 둘 다 이미 잡은 리뷰 → 발굴 대상에서 제외 (중복 후보 방지)
        for grp in ("complaint", "improvement"):
            for kw in bi.get(grp, []) or []:
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

    def _ctype(raw_type, word, texts):
        ct = str(raw_type or "complaint").strip().lower()
        joined = (word + " " + " ".join(t for _, t in texts))
        if ct not in ("complaint", "improvement"):
            return "improvement" if any(h in joined for h in _IMPROVE_HINT) else "complaint"
        if ct == "complaint" and any(h in joined for h in _IMPROVE_STRONG):
            return "improvement"  # AI가 불만으로 줘도 강한 개선신호면 승격(덜 엄격)
        return ct

    raw_clusters = []  # 배치별 결과 누적: {"word","type","review_ids":[id],"texts":[(rt,tx)]}
    ex = ThreadPoolExecutor(max_workers=1)
    t0 = time.time()
    done_batches = 0
    for bi in range(nb):
        batch = pool[bi * B:(bi + 1) * B]
        lines = [f"[{i}] {t.replace(chr(10), ' ')[:160]}" for i, (_rid, t, _rt) in enumerate(batch)]
        prompt = (
            "다음은 '부정적이지만 기존 불만 키워드에 안 잡힌' 제품 리뷰들이다. "
            "반복되는 불만·개선요청을 5~10개의 키워드로 묶어라.\n"
            "- word: 짧은 한국어 명사구(예: '에어튜브 크기', '버튼 위치', '진동 강도')\n"
            "- type: 'complaint'(고장·불량·단순 불만) 또는 'improvement'(개선요청). "
            "improvement는 너그럽게 분류하라 — '~있으면 좋겠다 / ~됐으면 / ~해주세요 / ~추가 / ~지원 / ~옵션 / 아쉽다 / 부족하다' "
            "처럼 변화·추가를 바라는 약한 뉘앙스도 모두 improvement로 본다. 명백한 고장·환불·불량만 complaint.\n"
            "- reviews: 그 키워드에 해당하는 위 리뷰 번호 배열\n\n"
            "[리뷰]\n" + "\n".join(lines) + "\n\n"
            'JSON 배열로만 출력: [{"word":"키워드","type":"complaint","reviews":[0,2]}]'
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
                "word": word, "type": _ctype(c.get("type"), word, texts),
                "review_ids": [batch[i][0] for i in idxs], "texts": texts,
            })

    # ── 배치 간 병합: 정규화 단어가 같거나 한쪽이 다른쪽을 포함하면 합침 ──
    def _norm(w):
        return re.sub(r"[^가-힣a-zA-Z0-9]", "", str(w)).lower()

    merged = []  # {"word","keys":set,"votes":{type:n},"rids":set,"texts":[]}
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
            merged.append({"word": rc["word"], "keys": {k}, "votes": {rc["type"]: 1},
                           "rids": set(rc["review_ids"]), "texts": list(rc["texts"])})
        else:
            hit["keys"].add(k)
            hit["votes"][rc["type"]] = hit["votes"].get(rc["type"], 0) + 1
            hit["rids"].update(rc["review_ids"])
            hit["texts"].extend(rc["texts"])
            if len(rc["word"]) < len(hit["word"]):  # 더 짧고 일반적인 단어를 대표로
                hit["word"] = rc["word"]

    cands = []
    for m in merged:
        # type 다수결, 동점이면 개선요청 우선(과소집계 방지)
        ctype = "improvement" if m["votes"].get("improvement", 0) >= m["votes"].get("complaint", 0) else "complaint"
        seen, samples = set(), []
        for rt, tx in m["texts"]:
            key = (tx or "")[:60]
            if key in seen:
                continue
            seen.add(key)
            samples.append({"rating": rt, "text": (tx or "")[:160]})
            if len(samples) >= 4:
                break
        cands.append({"word": m["word"], "type": ctype, "count": len(m["rids"]),
                      "review_ids": [str(r) for r in m["rids"]], "samples": samples})
    cands.sort(key=lambda x: x["count"], reverse=True)

    out["analyzed_total"] = len(pool)
    out["batches"] = {"total": nb, "ok": done_batches}
    out["candidates"] = cands
    (d / "keyword_candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  [OK] 신규 키워드 후보 {len(cands)}개 발굴(배치 {done_batches}/{nb} 성공, {round(time.time()-t0)}s) → {d / 'keyword_candidates.json'}")
    for c in cands:
        eprint(f"     · [{c['type']}] {c['word']} ({c['count']})")


if __name__ == "__main__":
    main()
