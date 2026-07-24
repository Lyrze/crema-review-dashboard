"""classify_unclassified.py --brand 슬룸 --month 2026-06
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
업로드 파이프라인에서 Taxonomy 미분류 리뷰를 AI로 분류 '제안'한다 (검토형).

동작:
  1. repo의 Taxonomy 스냅샷(docs/data/{브랜드}/*/taxonomy/*.json 중 최신)을 읽어
     Topic 목록 + 키워드 규칙 + 수동분류를 확보. (스냅샷 없으면 건너뜀 — exit 0)
  2. 대시보드와 동일한 매칭 규칙(any/all/regex + include)으로 미분류 리뷰 산출.
  3. Claude가 미분류를 배치(10건)로 읽고 명확한 Topic 배정만 제안.
  4. docs/data/{브랜드}/{월}/tx_suggestions.json 저장 —
     대시보드 미분류 화면에서 '검토 후 적용'(자동 반영 없음).

주의: keywords.json/Taxonomy 원본은 절대 수정하지 않음. 제안 파일만 생성.

사용:
  python scripts/classify_unclassified.py --brand 슬룸 --month 2026-05
"""
import argparse, json, re, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def is_quota(msg) -> bool:
    """한도 소진성 오류 판정 (reverify_suspect 와 동일 기준)."""
    m = str(msg).lower()
    return any(k in m for k in ("usage limit", "session limit", "rate limit", "quota",
                                "limit reached", "too many requests", "429", "overloaded"))


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
    ap.add_argument("--model", default=None, help="제안 모델 (기본: sonnet)")
    ap.add_argument("--verify-model", default=None,
                    help="합의 검증 모델 (기본: sonnet). 통과분만 '자동 배정' 등급")
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
    # + few-shot: 운영자가 수동 분류한 실제 리뷰를 topic 예시로 주입(분류 기준 학습)
    included = set()
    topic_examples = {}  # topicId -> [본문]
    for t in taxonomies:
        for rid, cls in (t.get("manualClassifications") or {}).items():
            if not cls or not cls.get("include"):
                continue
            included.add(str(rid))
            rtext = (reviews.get(str(rid), {}) or {}).get("text", "")
            if rtext:
                for tid in cls["include"]:
                    ex = topic_examples.setdefault(tid, [])
                    if len(ex) < 2:
                        ex.append(rtext[:90].replace("\n", " "))
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

    from claude_engine import ClaudeAnalyzer  # noqa: E402
    prop_model = args.model or "sonnet"
    analyzer = ClaudeAnalyzer(model=prop_model)
    eprint(f"  제안 모델={analyzer.model}")
    if not analyzer.health_check():
        err = getattr(analyzer, "last_error", "") or ""
        if is_quota(err):
            # 한도 소진 — quota_retry 루프가 리셋시각 파싱 후 재시도할 수 있게 exit 3
            eprint(f"  [STOP] 한도 소진 — 리셋 후 재실행 시 이어집니다. ({err[:200]})")
            sys.exit(3)
        eprint("  [SKIP] AI 엔진 무응답 — 제안 생성 건너뜀"); sys.exit(0)

    # ── 이어받기(resume) 마커: 한도 중단 시 배치 진행상태 보존 ──
    prog_path = out_path.parent / ".tx_progress.json"
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8")) if prog_path.is_file() else {}
    except Exception:
        prog = {}
    processed = set(prog.get("processed_ids", []))
    sugs_saved = prog.get("sugs", [])
    propose_done = bool(prog.get("propose_done"))
    v_state = prog.get("verify", {}) or {}

    def save_prog(**kw):
        prog.update(kw)
        prog["processed_ids"] = sorted(processed)
        prog["sugs"] = sugs
        prog_path.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")

    def topic_line(i, o):
        line = f"[{i+1}] {o['name']} (키워드: {', '.join(map(str, o['keywords']))})"
        exs = topic_examples.get(o["topicId"]) or []
        if exs:
            line += "\n     예시리뷰: " + " / ".join(f'"{e}"' for e in exs)
        return line
    topic_lines = "\n".join(topic_line(i, o) for i, o in enumerate(topics))
    target = unclassified[: args.cap]
    if len(unclassified) > args.cap:
        eprint(f"  (상한 {args.cap}건만 분석 — 나머지 {len(unclassified)-args.cap}건은 다음 실행에서)")
    sugs = list(sugs_saved)          # 이어받기: 이전 실행의 제안 누적분 복원
    if processed:
        before_n = len(target)
        target = [x for x in target if x[0] not in processed]
        eprint(f"  [RESUME] 이미 분석한 {before_n - len(target)}건 건너뜀 (남은 {len(target)}건, 누적 제안 {len(sugs)}건)")
    if propose_done:
        target = []
        eprint(f"  [RESUME] 제안 단계 완료 상태 — 합의 검증부터 재개 (제안 {len(sugs)}건)")
    B = max(5, args.batch)
    ex = ThreadPoolExecutor(max_workers=1)
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
            if is_quota(e):
                save_prog()
                eprint(f"  [STOP] 한도 소진 — 진행분 저장(분석 {len(processed)}건·제안 {len(sugs)}건). "
                       f"재실행 시 이어집니다. ({str(e)[:200]})")
                sys.exit(3)
            eprint(f"   [ERR] 배치 {bi//B+1}: {str(e)[:100]}"); continue
        # 모델이 응답한 배치는 처리됨으로 기록 (재실행 시 중복 호출 방지)
        for _rid, _t, _rt in batch:
            processed.add(_rid)
        m = re.search(r"\[[\s\S]*\]", str(raw or ""))
        if not m:
            save_prog()
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
        save_prog()
        eprint(f"   배치 {bi//B+1}/{(len(target)+B-1)//B} — 누적 제안 {len(sugs)}건")

    save_prog(propose_done=True)     # 제안 단계 완료 — 이후 중단 시 검증부터 재개

    # ── 합의 검증: 제안을 verify-model이 반대신문 → 통과=자동배정, 거부/불확실=검토 ──
    auto, review_tier = [], list(sugs)
    verify_model = args.verify_model or "sonnet"
    # 동일 sonnet이라도 엄격 프롬프트로 2차 검증을 항상 수행한다
    do_verify = bool(sugs and verify_model)
    if do_verify:
        v = ClaudeAnalyzer(model=verify_model)
        if not v.health_check():
            verr = getattr(v, "last_error", "") or ""
            if is_quota(verr):
                save_prog()
                eprint(f"  [STOP] 한도 소진(검증 단계 진입 전) — 재실행 시 검증부터 재개. ({verr[:200]})")
                sys.exit(3)
            eprint(f"  [INFO] 검증 모델({verify_model}) 무응답 — 전부 검토 등급으로 저장")
        else:
            eprint(f"  합의 검증({verify_model}) — 제안 {len(sugs)}건 재판정...")
            # 이어받기: 이전 실행의 검증 진행분 복원
            idx = int(v_state.get("idx", 0) or 0)
            auto = list(v_state.get("auto", []))
            review_tier = list(v_state.get("review", []))
            if idx:
                eprint(f"  [RESUME] 검증 {idx}/{len(sugs)}부터 재개 (자동 {len(auto)} · 검토 {len(review_tier)})")
            VB = 8
            ex2 = ThreadPoolExecutor(max_workers=1)
            consec = 0
            while idx < len(sugs):
                chunk = sugs[idx:idx + VB]
                lines = []
                for i, s in enumerate(chunk):
                    txt = (reviews.get(str(s["review_id"]), {}) or {}).get("text", "")[:150].replace("\n", " ")
                    lines.append(f'({i}) Topic="{s["topic_name"]}" / 리뷰="{txt}"')
                vprompt = ("각 리뷰가 해당 Topic 주제를 실제로 담고 있는지 엄격히 판정하라. "
                           "주제와 무관하거나 애매하면 false.\n\n" + "\n".join(lines) +
                           '\n\nJSON 배열로만 답: [{"i":0,"ok":true}]')
                try:
                    fut = ex2.submit(v.client.generate, model=v.model, prompt=vprompt,
                                     system="당신은 한국어 VOC 분류 검증 전문가입니다. JSON으로만 답하세요.",
                                     temperature=0.0)
                    raw2 = fut.result(timeout=args.timeout)
                    consec = 0
                except FTimeout:
                    consec += 1
                    eprint(f"   [TIMEOUT] 검증 배치 — 해당 배치는 검토 등급으로 (연속 {consec})")
                    try:
                        ex2.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    ex2 = ThreadPoolExecutor(max_workers=1)
                    review_tier.extend(chunk)
                    idx += VB
                    if consec >= 3:
                        eprint("   연속 타임아웃 3회 — 남은 제안 전부 검토 등급으로")
                        review_tier.extend(sugs[idx:])
                        break
                    continue
                except Exception as e:
                    if is_quota(e):
                        save_prog(verify={"idx": idx, "auto": auto, "review": review_tier})
                        eprint(f"  [STOP] 한도 소진 — 검증 {idx}/{len(sugs)}에서 저장. "
                               f"재실행 시 이어집니다. ({str(e)[:200]})")
                        sys.exit(3)
                    eprint(f"   [ERR] 검증: {str(e)[:80]} — 검토 등급으로")
                    review_tier.extend(chunk)
                    idx += VB
                    continue
                okmap = {}
                m2 = re.search(r"\[[\s\S]*\]", str(raw2 or ""))
                if m2:
                    try:
                        for o in json.loads(m2.group(0)):
                            okmap[int(o.get("i"))] = bool(o.get("ok"))
                    except Exception:
                        pass
                for i, s in enumerate(chunk):
                    (auto if okmap.get(i) else review_tier).append(s)
                idx += VB
                save_prog(verify={"idx": idx, "auto": auto, "review": review_tier})
                eprint(f"   검증 {min(idx, len(sugs))}/{len(sugs)} — 자동 {len(auto)} · 검토 {len(review_tier)}")

    out["auto"] = auto            # 2단계 합의(제안+검증) → 대시보드가 자동 배정 (↩ 취소 가능)
    out["suggestions"] = review_tier  # 제안 단독(검증 미통과) → 검토 패널
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        if prog_path.is_file():
            prog_path.unlink()      # 월 완료 — 진행 마커 제거 (산출물은 tx_suggestions.json)
    except Exception:
        pass
    eprint(f"  [OK] 자동배정(2모델 합의) {len(auto)}건 · 검토 제안 {len(review_tier)}건 → {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
