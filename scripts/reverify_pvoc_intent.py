"""reverify_pvoc_intent.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
PVOC 토픽 의도(감성) 데이터(pvoc_intent.json)의 'neg'(부정) 분류를 더 큰 모델(14b)로 재검증한다.
  - 7b가 '그 토픽에 대해 부정'으로 본 건 중, 14b가 보기에 실제로는 부정이 아닌 것(거짓 부정)을 pos 로 이동.
  - 제거(완화)만 하고 새로 부정 추가는 안 함 → 부정 과표시(허수) 제거. neg 집합만 보므로 빠름.
  - 배치당 하드 타임아웃으로 Ollama hang 방지(멈춰도 해당 배치만 건너뜀).

키워드용 reverify_suspect.py 와 같은 철학(의심 건만 큰 모델로 재판정, 정밀도↑).
사용:
    python scripts/reverify_pvoc_intent.py --brand 슬룸 --month 2026-05 --model qwen2.5:14b
"""
import argparse
import json
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--engine", default="ollama", choices=["ollama", "claude"])
    ap.add_argument("--model", "--ollama-model", dest="model", default=None)
    ap.add_argument("--base-url", "--ollama-url", dest="base_url", default="http://localhost:11434")
    ap.add_argument("--timeout", type=int, default=90, help="배치당 하드 타임아웃(초)")
    args = ap.parse_args()
    model = args.model or ("sonnet" if args.engine == "claude" else "qwen2.5:14b")

    from ollama_analysis import extract_json_from_response  # noqa: E402
    from claude_engine import make_analyzer  # noqa: E402  (ollama/claude 공용 팩토리)
    if args.engine == "claude":
        from classify_unclassified import is_quota  # noqa: E402
    else:
        def is_quota(_): return False

    d = ROOT / "docs" / "data" / args.brand / args.month
    ipath = d / "pvoc_intent.json"
    rpath = d / "reviews.json"
    if not ipath.is_file():
        eprint("[SKIP] pvoc_intent.json 없음 — classify_pvoc_intent 먼저 실행 필요"); sys.exit(0)
    if not rpath.is_file():
        eprint("[ERROR] reviews.json 없음"); sys.exit(1)

    intent = json.loads(ipath.read_text(encoding="utf-8"))
    reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
    topics = intent.get("topics", {})
    if not topics:
        eprint("[SKIP] 토픽 없음"); sys.exit(0)

    analyzer = make_analyzer(args.engine, model=model, base_url=args.base_url)
    if not analyzer.health_check():
        err = str(getattr(analyzer, "last_error", "") or "")
        if is_quota(err):
            eprint(f"[STOP] 한도 소진 — 재실행 시 이어집니다. ({err[:200]})"); sys.exit(3)
        eprint(f"[ERROR] AI 응답 없음 — 중단 ({err or 'ollama serve 확인'})"); sys.exit(2)

    # 완료 토픽 이어받기(한도 소진 시 처음부터 재검증하지 않도록)
    prog_path = d / ".pvoc_reverify_progress.json"
    done_topics = set()
    if args.engine == "claude" and prog_path.is_file():
        try:
            done_topics = set(json.loads(prog_path.read_text(encoding="utf-8")).get("done", []))
            if done_topics:
                eprint(f"  [RESUME] 완료 {len(done_topics)}개 토픽은 재검증 없이 건너뜀")
        except Exception:
            done_topics = set()

    def save_progress():
        if args.engine != "claude":
            return
        try:
            prog_path.write_text(json.dumps({"done": sorted(done_topics)}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            eprint(f"  [WARN] 진행 마커 저장 실패(계속): {exc}")

    total_neg = sum(len(io.get("neg", [])) for io in topics.values())
    eprint(f"  PVOC 의도 재검증: {args.brand}/{args.month} · 부정 {total_neg}건 · 엔진 {args.engine} · 모델 {model}")
    ex = ThreadPoolExecutor(max_workers=1)

    def classify(name, chunk):
        """chunk=[(rid,text)] → {rid: '부정'|'긍정'}. (그 토픽에 대해 실제 부정인지 엄격 판정)"""
        lines = [f"[{j}] {str(t).replace(chr(10), ' ')[:240]}" for j, (_rid, t) in enumerate(chunk)]
        prompt = (
            f'주제: "{name}"\n\n'
            f'아래 각 리뷰가 이 제품의 "{name}" 주제에 대해 실제로 불만/문제제기인지 엄격히 판정하세요.\n'
            f"- 부정: 그 주제의 불만·불편·문제·하자·요구를 실제로 말함\n"
            f"- 긍정: 그 주제를 만족·칭찬하거나 문제없다고 함, 또는 단순 언급·해당 없음\n"
            f"별점이 아니라 본문 내용으로 판정하세요. 애매하면 긍정으로 분류하세요.\n\n"
            f"리뷰:\n" + "\n".join(lines) + "\n\n"
            'JSON으로만: {"items":[{"no":0,"s":"부정"},{"no":1,"s":"긍정"}]}'
        )
        raw = analyzer.client.generate(
            model=analyzer.model, prompt=prompt,
            system="당신은 한국어 리뷰 분류 전문가입니다. JSON으로만 답하세요.", temperature=0.0)
        parsed = extract_json_from_response(raw)
        items = parsed.get("items") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
        out = {}
        if items:
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("no"), int) and 0 <= it["no"] < len(chunk):
                    s = str(it.get("s", "")).replace(" ", "")
                    out[chunk[it["no"]][0]] = "부정" if "부정" in s else "긍정"
        return out

    t0 = time.time(); total_moved = 0
    for name, io in topics.items():
        if name in done_topics:
            continue
        neg = list(io.get("neg", []))
        pos = list(io.get("pos", []))
        if not neg:
            done_topics.add(name)
            continue
        cand = [(rid, reviews.get(rid, {}).get("text", "")) for rid in neg if reviews.get(rid)]
        moved = []
        quota_hit = False
        for i in range(0, len(cand), 10):
            chunk = cand[i:i + 10]
            fut = ex.submit(classify, name, chunk)
            try:
                verdict = fut.result(timeout=args.timeout)
            except FTimeout:
                eprint(f"  [TIMEOUT] [{name}]@{i} — 이 배치 유지하고 계속")
                try: ex.shutdown(wait=False, cancel_futures=True)
                except Exception: pass
                ex = ThreadPoolExecutor(max_workers=1)
                continue
            except Exception as e:
                if is_quota(e):
                    eprint(f"  [STOP] 한도 소진('{name}' 처리 중) — 완료 {len(done_topics)}개 토픽 저장. 재실행 시 이어집니다.")
                    quota_hit = True
                    break
                eprint(f"  [ERR] [{name}] {str(e)[:100]}"); continue
            for rid, _ in chunk:
                if verdict.get(rid) == "긍정":  # 거짓 부정 → 긍정으로 이동
                    moved.append(rid)
        if moved:
            mv = set(moved)
            io["neg"] = [r for r in neg if r not in mv]
            seen = set(pos)
            io["pos"] = pos + [r for r in moved if r not in seen]
            total_moved += len(moved)
            eprint(f"   [{name}] 부정 {len(neg)} → 거짓부정 {len(moved)} 완화 (잔여 부정 {len(io['neg'])})")
        if quota_hit:
            ipath.write_text(json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")  # 완료분까지 저장
            save_progress()
            sys.exit(3)
        done_topics.add(name)
        save_progress()

    intent["reverified_model"] = model
    ipath.write_text(json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")
    if prog_path.is_file():
        try: prog_path.unlink()
        except Exception: pass
    eprint(f"  [OK] PVOC 의도 재검증 완료 — 거짓부정 {total_moved}건 완화 · {round(time.time()-t0)}s → {ipath}")


if __name__ == "__main__":
    main()
