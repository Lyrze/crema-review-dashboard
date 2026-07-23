"""classify_pvoc_intent.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
구매경험 VOC(PVOC) 토픽별 매칭 리뷰의 '그 토픽에 대한' 의도(칭찬/불만)를 AI로 판정해
docs/data/{brand}/{month}/pvoc_intent.json 을 생성한다.

대시보드의 '감성기반' 토글이 이 파일을 사용한다 (없으면 별점기반으로 폴백).

- 패턴 출처: scripts/pvoc_patterns.json (index.html PVOC_TAXONOMY 에서 추출, 동일)
- 매칭 규칙: 대시보드 pvjMatchText 와 동일(짧은 영문은 경계, 한글은 부분문자열)
- 의도 판정: ollama_analysis 의 3단계 ②의도 프롬프트와 동일 취지 (배치)

사용:
    python scripts/classify_pvoc_intent.py --brand 슬룸 --month 2026-05 --model qwen2.5:7b

출력 형식: {"topics": {토픽명: {"pos": [review_id...], "neg": [review_id...]}}, "model":..., "generated_at":...}
  pos = 그 토픽을 칭찬/만족, neg = 그 토픽에 불만/불편/문제제기 (중립·단순언급은 pos 처리)
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def match_text(txt, pats):
    """대시보드 pvjMatchText 와 동일: 짧은 영문 토큰은 경계검사, 한글은 부분문자열."""
    t = (txt or "").lower()
    for p in pats:
        p = str(p).lower()
        if re.match(r"^[a-z][a-z ]{0,3}$", p):
            if re.search(r"(^|[^a-z])" + re.escape(p) + r"($|[^a-z])", t):
                return True
        elif p in t:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--engine", default="ollama", choices=["ollama", "claude"])
    ap.add_argument("--model", "--ollama-model", dest="model", default=None)
    ap.add_argument("--base-url", "--ollama-url", dest="base_url", default="http://localhost:11434")
    args = ap.parse_args()
    model = args.model or ("sonnet" if args.engine == "claude" else "qwen2.5:7b")

    from ollama_analysis import extract_json_from_response  # noqa: E402
    from claude_engine import make_analyzer  # noqa: E402  (ollama/claude 공용 팩토리)
    if args.engine == "claude":
        from classify_unclassified import is_quota  # noqa: E402
    else:
        def is_quota(_): return False  # Ollama 는 한도 개념 없음

    pats_path = ROOT / "scripts" / "pvoc_patterns.json"
    data_dir = ROOT / "docs" / "data" / args.brand / args.month
    rpath = data_dir / "reviews.json"
    if not pats_path.is_file():
        eprint("[ERROR] scripts/pvoc_patterns.json 없음"); sys.exit(1)
    if not rpath.is_file():
        eprint(f"[ERROR] {rpath} 없음"); sys.exit(1)

    columns = json.loads(pats_path.read_text(encoding="utf-8"))
    reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
    eprint(f"  PVOC 의도 분류: {args.brand}/{args.month} · 리뷰 {len(reviews)}건 · 엔진 {args.engine} · 모델 {model}")

    analyzer = make_analyzer(args.engine, model=model, base_url=args.base_url)
    if not analyzer.health_check():
        err = str(getattr(analyzer, "last_error", "") or "")
        if is_quota(err):
            eprint(f"  [STOP] 한도 소진 — 재실행 시 이어집니다. ({err[:200]})"); sys.exit(3)
        eprint(f"[ERROR] AI 응답 없음 — 중단 ({err or 'ollama serve 확인'})"); sys.exit(1)

    # 이전 실행에서 한도로 중단된 진행분(완료된 토픽의 pos/neg) 이어받기.
    # 토픽마다 매칭 리뷰 수가 크게 달라 전체로는 수백 건의 배치 호출이 될 수 있어(신규 발견),
    # 완료 토픽 단위로 저장해 재실행 시 처음부터 다시 돌지 않게 한다.
    prog_path = data_dir / ".pvoc_intent_progress.json"
    done_topics = {}
    if args.engine == "claude" and prog_path.is_file():
        try:
            done_topics = json.loads(prog_path.read_text(encoding="utf-8")).get("done", {})
            if done_topics:
                eprint(f"  [RESUME] 완료 {len(done_topics)}개 토픽은 재호출 없이 적용")
        except Exception:
            done_topics = {}

    def save_progress():
        if args.engine != "claude":
            return
        try:
            prog_path.write_text(json.dumps({"done": done_topics}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            eprint(f"  [WARN] 진행 마커 저장 실패(계속): {exc}")

    rid_list = list(reviews.keys())

    def classify_chunk(topic_name, chunk):
        """chunk: [(rid, text)] → {rid: '긍정'|'부정'} (중립/해당없음은 긍정 처리).
        raises QuotaExhausted(RuntimeError) — is_quota 감지 시 상위로 전파(조용히 '긍정' 처리 금지).
        """
        lines = []
        for j, (_rid, text) in enumerate(chunk):
            lines.append(f"[{j}] {str(text).replace(chr(10), ' ')[:240]}")
        prompt = (
            f'주제: "{topic_name}"\n\n'
            f'아래 각 리뷰가 이 제품의 "{topic_name}" 주제에 대해 어떤 태도인지 분류하세요:\n'
            f"- 긍정: 그 주제를 만족·칭찬하거나 문제없다고 함\n"
            f"- 부정: 그 주제의 불만·불편·문제·하자·요구를 말함\n"
            f"- 중립: 단순 언급일 뿐 평가 없음\n"
            f"별점이 아니라 본문 내용으로 판정하세요.\n\n"
            f"리뷰:\n" + "\n".join(lines) + "\n\n"
            'JSON으로만: {"items":[{"no":0,"s":"긍정"},{"no":1,"s":"부정"}]}'
        )
        try:
            raw = analyzer.client.generate(model=analyzer.model, prompt=prompt,
                                           system="당신은 한국어 리뷰 분류 전문가입니다. JSON으로만 답하세요.",
                                           temperature=0.0)
            parsed = extract_json_from_response(raw)
            items = parsed.get("items") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
        except Exception as exc:
            if is_quota(exc):
                raise  # 한도 소진 — 미판정분을 '긍정'으로 덮어쓰지 않고 상위로 전파해 중단시킨다
            items = None
        out = {}
        if items:
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("no"), int) and 0 <= it["no"] < len(chunk):
                    s = str(it.get("s", "")).replace(" ", "")
                    out[chunk[it["no"]][0]] = "부정" if "부정" in s else "긍정"
        # 미판정분은 긍정(보수적: 불만으로 과표시 방지) — 단, 이건 배치 오류(파싱 실패 등)에만 적용되고
        # 한도 소진은 위에서 raise 로 걸러지므로 여기 도달하지 않는다.
        for rid, _ in chunk:
            out.setdefault(rid, "긍정")
        return out

    result_topics = dict(done_topics)  # 이어받기: 완료 토픽은 저장분 그대로
    for col in columns:
        for tp in col.get("topics", []):
            name = tp["name"]
            if name in done_topics:
                continue
            pats = tp.get("pats", [])
            matched = [(rid, reviews[rid].get("text", "")) for rid in rid_list
                       if match_text(reviews[rid].get("text", ""), pats)]
            pos, neg = [], []
            try:
                for i in range(0, len(matched), 12):
                    verdict = classify_chunk(name, matched[i:i + 12])
                    for rid, _ in matched[i:i + 12]:
                        (neg if verdict.get(rid) == "부정" else pos).append(rid)
            except Exception as exc:
                if is_quota(exc):
                    eprint(f"  [STOP] 한도 소진('{name}' 처리 중) — 완료 {len(done_topics)}개 토픽 저장. 재실행 시 이어집니다.")
                    save_progress()
                    sys.exit(3)
                raise
            result_topics[name] = {"pos": pos, "neg": neg}
            done_topics[name] = result_topics[name]
            save_progress()
            eprint(f"   [{name}] 매칭 {len(matched)} → 긍정 {len(pos)} · 부정 {len(neg)}")

    out_path = data_dir / "pvoc_intent.json"
    out_path.write_text(json.dumps({
        "topics": result_topics, "model": model,
        "generated_at": "", "month": args.month, "brand": args.brand,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if prog_path.is_file():
        try: prog_path.unlink()
        except Exception: pass
    eprint(f"  [OK] 저장: {out_path}")


if __name__ == "__main__":
    main()
