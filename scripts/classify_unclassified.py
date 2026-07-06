"""classify_unclassified.py --brand 슬룸 --month 2026-06
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
업로드 파이프라인에서 Taxonomy 미분류 리뷰를 AI로 분류 '제안'한다 (검토형).

동작:
  1. repo의 Taxonomy 스냅샷(docs/data/{브랜드}/*/taxonomy/*.json 중 최신)을 읽어
     Topic 목록 + 키워드 규칙 + 수동분류를 확보. (스냅샷 없으면 건너뜀 — exit 0)
  2. 대시보드와 동일한 매칭 규칙(any/all/regex + include)으로 미분류 리뷰 산출.
  3. Ollama가 미분류를 배치(10건)로 읽고 명확한 Topic 배정만 제안.
  4. docs/data/{브랜드}/{월}/tx_suggestions.json 저장 —
     대시보드 미분류 화면에서 '검토 후 적용'(자동 반영 없음).

주의: keywords.json/Taxonomy 원본은 절대 수정하지 않음. 제안 파일만 생성.

사용:
  python scripts/classify_unclassified.py --brand 슬룸 --month 2026-05 --model qwen2.5:7b
"""
import argparse, json, re, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def latest_snapshot(brand: str):
    """브랜드의 모든 월에서 가장 최신 Taxonomy 스냅샷 경로."""
    files = sorted((ROOT / "docs" / "data" / brand).glob("*/taxonomy/*.json"),
                   key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def load_taxonomies(path: Path):
    j = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(j, list):
        return j
    return j.get("data") or j.get("taxonomies") or []


def match_topic(topic: dict, text_lower: str) -> bool:
    """대시보드 txMatchTopic 과 동일한 키워드 매칭 (any/all/regex)."""
    kws = [str(k).strip().lower() for k in (topic.get("keywords") or []) if str(k).strip()]
    if not kws:
        return False
    mode = topic.get("mode") or "any"
    if mode == "regex":
        try:
            return bool(re.search("|".join(kws), text_lower, re.IGNORECASE))
        except re.error:
            return False
    if mode == "all":
        return all(k in text_lower for k in kws)
    return any(k in text_lower for k in kws)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--model", "--ollama-model", dest="model", default="qwen2.5:7b")
    ap.add_argument("--base-url", "--ollama-url", dest="base_url", default="http://localhost:11434")
    ap.add_argument("--cap", type=int, default=300, help="AI에 넣을 미분류 최대 수")
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=90, help="배치당 하드 타임아웃(초)")
    args = ap.parse_args()

    out_path = ROOT / "docs" / "data" / args.brand / args.month / "tx_suggestions.json"

    snap = latest_snapshot(args.brand)
    if not snap:
        eprint("  [SKIP] Taxonomy 스냅샷 없음 — 대시보드에서 '☁️ 저장소 업로드'를 한 번 하면 다음 달부터 자동 제안됩니다.")
        sys.exit(0)
    taxonomies = load_taxonomies(snap)
    topics = []
    for t in taxonomies:
        for tp in (t.get("topics") or []):
            topics.append({"taxId": t.get("id"), "taxName": t.get("name"),
                           "topicId": tp.get("id"), "name": tp.get("name"),
                           "keywords": (tp.get("keywords") or [])[:6], "topic": tp})
    if not topics:
        eprint("  [SKIP] 스냅샷에 Topic 없음"); sys.exit(0)
    eprint(f"  스냅샷: {snap.relative_to(ROOT)} (taxonomy {len(taxonomies)} · topic {len(topics)})")

    rpath = ROOT / "docs" / "data" / args.brand / args.month / "reviews.json"
    if not rpath.is_file():
        eprint("  [ERROR] reviews.json 없음"); sys.exit(1)
    reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})

    # 이미 분류된 리뷰: 키워드 매칭(모든 taxonomy) 또는 수동 include
    included = set()
    for t in taxonomies:
        for rid, cls in (t.get("manualClassifications") or {}).items():
            if cls and cls.get("include"):
                included.add(str(rid))
    unclassified = []
    for rid, r in reviews.items():
        text = (r.get("text") or "").strip()
        if not text:
            continue
        if str(rid) in included:
            continue
        lt = text.lower()
        if any(match_topic(o["topic"], lt) for o in topics):
            continue
        unclassified.append((str(rid), text, r.get("rating")))
    eprint(f"  {args.brand}/{args.month}: 미분류 {len(unclassified)}건 (스냅샷 규칙 기준)")

    out = {"brand": args.brand, "month": args.month, "generated_at": "",
           "source_snapshot": str(snap.relative_to(ROOT)),
           "unclassified_total": len(unclassified), "suggestions": []}
    if not unclassified:
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        eprint("  미분류 없음 — 빈 제안 저장"); sys.exit(0)

    from ollama_analysis import OllamaAnalyzer  # noqa: E402
    analyzer = OllamaAnalyzer(model=args.model, base_url=args.base_url)
    if not analyzer.health_check():
        eprint("  [SKIP] Ollama 무응답 — 제안 생성 건너뜀"); sys.exit(0)

    topic_lines = "\n".join(f"[{i+1}] {o['name']} (키워드: {', '.join(map(str, o['keywords']))})"
                            for i, o in enumerate(topics))
    target = unclassified[: args.cap]
    if len(unclassified) > args.cap:
        eprint(f"  (상한 {args.cap}건만 분석 — 나머지 {len(unclassified)-args.cap}건은 다음 실행에서)")
    B = max(5, args.batch)
    ex = ThreadPoolExecutor(max_workers=1)
    sugs = []
    consec_to = 0
    for bi in range(0, len(target), B):
        batch = target[bi:bi + B]
        rev_lines = "\n".join(f"({i}) *{rt} {t[:150].replace(chr(10), ' ')}"
                              for i, (_rid, t, rt) in enumerate(batch))
        prompt = ("다음 상품 리뷰를 아래 Topic 중 가장 맞는 하나에 분류하라. "
                  "명확히 해당할 때만 배정하고, 애매하면 0(미분류 유지).\n\n"
                  f"[Topic 목록]\n{topic_lines}\n\n[리뷰]\n{rev_lines}\n\n"
                  'JSON 배열로만 답: [{"review":0,"topic":3}] (topic은 Topic 번호, 해당없으면 0)')
        try:
            fut = ex.submit(analyzer.client.generate, model=analyzer.model, prompt=prompt,
                            system="당신은 한국어 VOC 분류 전문가입니다. JSON으로만 답하세요.", temperature=0.0)
            raw = fut.result(timeout=args.timeout)
            consec_to = 0
        except FTimeout:
            consec_to += 1
            eprint(f"   [TIMEOUT] 배치 {bi//B+1} 건너뜀 (연속 {consec_to})")
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            ex = ThreadPoolExecutor(max_workers=1)
            if consec_to >= 3:
                eprint("   연속 타임아웃 3회 — 지금까지 제안만 저장하고 종료")
                break
            continue
        except Exception as e:
            eprint(f"   [ERR] 배치 {bi//B+1}: {str(e)[:100]}"); continue
        m = re.search(r"\[[\s\S]*\]", str(raw or ""))
        if not m:
            continue
        try:
            arr = json.loads(m.group(0))
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        for o in arr:
            try:
                ri, ti = int(o.get("review")), int(o.get("topic"))
            except Exception:
                continue
            if 0 <= ri < len(batch) and 1 <= ti <= len(topics):
                t = topics[ti - 1]
                sugs.append({"review_id": batch[ri][0], "tax_id": t["taxId"],
                             "topic_id": t["topicId"], "topic_name": t["name"],
                             "tax_name": t["taxName"]})
        eprint(f"   배치 {bi//B+1}/{(len(target)+B-1)//B} — 누적 제안 {len(sugs)}건")

    out["suggestions"] = sugs
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  [OK] AI 분류 제안 {len(sugs)}건 → {out_path.relative_to(ROOT)} (대시보드 미분류 화면에서 검토·적용)")


if __name__ == "__main__":
    main()
