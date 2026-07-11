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


def reverify_month(brand: str, month: str, model: str, base_url: str, polarities: list,
                   engine: str = "ollama") -> bool:
    """한 브랜드/월의 keywords.json 을 재검증해 in-place 저장. 성공 시 True."""
    data_dir = ROOT / "docs" / "data" / brand / month
    kpath = data_dir / "keywords.json"
    rpath = data_dir / "reviews.json"
    if not kpath.is_file():
        eprint(f"  [ERROR] keywords.json 없음: {kpath}")
        return False
    if not rpath.is_file():
        eprint(f"  [ERROR] reviews.json 없음: {rpath} (재검증에는 전체 리뷰 인덱스가 필요)")
        return False

    # 이미 이 엔진으로 완전히 끝난 월이면 API 호출 없이 즉시 스킵
    # (과거엔 완료 시 마커 파일을 통째로 지웠는데, 그러면 "완료"와 "미시작"을 구분할 수 없어
    #  --months 를 여러 월 묶어 재실행할 때마다 이미 끝난 월을 처음부터 다시 돌리는 낭비가 있었음
    #  — 2026-07-10, auto_reverify_loop.py 재시도 중 04월을 계속 처음부터 재검증하다 한도만 또 소진)
    prog_path = data_dir / ".reverify_progress.json"
    try:
        prog0 = json.loads(prog_path.read_text(encoding="utf-8")) if prog_path.is_file() else {}
    except Exception:
        prog0 = {}
    # 완료 판정은 '완료한 polarity 집합' 기준. 요청 polarity가 모두 완료돼 있어야 스킵.
    # (엔진 단위 __done__ 만 쓰면 --polarities 축소 실행 후 전체 재실행 시 미완 polarity가 영구 스킵됨)
    done_pol = set((prog0.get("__done_pol__", {}) or {}).get(engine, []))
    if engine in (prog0.get("__done__", []) or []):     # 구버전 마커 하위호환
        done_pol |= set(POLARITY_MAP.keys())
    if done_pol and set(polarities).issubset(done_pol):
        eprint(f"  [SKIP] {month} 은(는) 이미 {engine}로 요청 polarity({','.join(polarities)}) 재검증 완료 — 스킵")
        return True

    # 엔진 선택: claude(구독 CLI) 또는 ollama(로컬)
    if str(engine).lower() == "claude":
        from claude_engine import ClaudeAnalyzer  # type: ignore[import]
        analyzer = ClaudeAnalyzer(model=model or "sonnet")
        eng_label = f"Claude CLI({analyzer.model})"
        fail_msg = "  [ERROR] Claude CLI 응답 없음 (로그인 확인) — 재검증 건너뜀"
    else:
        from ollama_analysis import OllamaAnalyzer  # type: ignore[import]
        analyzer = OllamaAnalyzer(model=model, base_url=base_url)
        eng_label = f"Ollama({model})"
        fail_msg = f"  [ERROR] Ollama 응답 없음 ({base_url}) — 재검증 건너뜀"
    def is_quota(msg):
        m = str(msg).lower()
        return any(k in m for k in ("usage limit", "session limit", "rate limit", "quota",
                                    "limit reached", "too many requests", "429", "overloaded"))

    if not analyzer.health_check():
        err = getattr(analyzer, "last_error", None) or ""
        if is_quota(err):
            # 계정 전체가 한도 소진 상태 — 이 월은 아무 것도 못 했으므로 '건너뜀(False)'이 아니라
            # 반드시 'quota'로 반환해 main()이 exit 3(재시도 필요)로 멈추게 한다.
            # (그냥 False 로 스킵하면 남은 월도 전부 health_check 실패로 스킵되어 결국
            #  아무 것도 처리 못 했는데 exit 0("완료")로 끝나버리는 버그가 있었음 — 2026-07-10)
            eprint(f"  [STOP] 엔진 응답 불가(한도 소진 추정): {err[:120]}")
            return "quota"
        eprint(fail_msg)
        return False
    eprint(f"  [OK] 정밀 보정 엔진={eng_label}")

    kw = json.loads(kpath.read_text(encoding="utf-8"))
    rv_idx = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
    bi = kw.get("by_intent", {})

    # ── 이어받기(resume) 인프라 ──
    prog_path = data_dir / ".reverify_progress.json"
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8")) if prog_path.is_file() else {}
    except Exception:
        prog = {}
    done = set(prog.get(engine, []))            # 이 엔진으로 이미 끝낸 (key::word)
    if done:
        eprint(f"  [RESUME] 이미 완료된 키워드 {len(done)}개는 건너뜁니다")

    def save_kw():
        kpath.write_text(json.dumps(kw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def mark(tag):
        done.add(tag); prog[engine] = sorted(done)
        prog_path.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")

    changed_total = 0
    consec_fail = 0
    for key in polarities:
        polarity = POLARITY_MAP.get(key)
        if not polarity:
            continue
        for item in bi.get(key, []):
            word = str(item.get("word", ""))
            tag = key + "::" + word
            if tag in done:
                continue                        # 이어받기: 이미 처리됨
            members = [str(x) for x in item.get("all_review_ids", [])]
            if not members:
                mark(tag); continue
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
                mark(tag); continue
            before = len(samples)
            f0 = getattr(analyzer.client, "fail_count", 0)   # 이 키워드 검증 전 실패 수
            try:
                kept = analyzer.verify_keyword_reviews(word, polarity, samples, mode="batch")
            except Exception as exc:  # noqa: BLE001
                # 리셋 시각("resets HH:MMpm")이 이 메시지로 auto_reverify_loop 에 전달되므로
                # 절단 폭을 넉넉히(200) — 과거 [:80]은 'resets ...'를 잘라 파싱 실패시켰음.
                if is_quota(exc):
                    save_kw()
                    eprint(f"  [STOP] 한도 소진 — 진행분 저장. 완료 {len(done)}개. "
                           f"같은 명령 재실행 시 이어집니다. ({str(exc)[:200]})")
                    return "quota"
                consec_fail += 1
                eprint(f"  [WARN] '{word}' 재검증 실패, 유지(미완료): {str(exc)[:160]}")
                if consec_fail >= 3:   # 비한도 연속 실패 → 재시도 무의미, 사람 확인 필요
                    save_kw()
                    eprint(f"  [STOP] 비한도 연속 실패 {consec_fail}회 — 진행분 저장, 중단(로그인/환경 확인 필요)")
                    return False
                continue
            # 내부 3단계 게이트는 호출 실패를 '보존'으로 삼키므로, 실패 발생 여부를 카운터로 감지
            df = getattr(analyzer.client, "fail_count", 0) - f0
            if df > 0:
                consec_fail += 1
                quota_seen = bool(getattr(analyzer.client, "_quota_seen", False))
                eprint(f"  [WARN] '{word}' 검증 중 호출 {df}건 실패 → 신뢰불가, 미완료 처리(재실행 시 재검증)")
                if quota_seen:         # 실제 한도 신호 관측 → 리셋 후 이어받기
                    save_kw()
                    eprint(f"  [STOP] 한도 소진 추정 — 진행분 저장. 완료 {len(done)}개. 같은 명령 재실행 시 이어집니다.")
                    return "quota"
                if consec_fail >= 3:   # 비한도 연속 실패 → 중단(사람 확인)
                    save_kw()
                    eprint(f"  [STOP] 비한도 연속 실패 {consec_fail}회 — 진행분 저장, 중단(로그인/환경 확인 필요)")
                    return False
                continue               # write-back/mark 하지 않음 → 원본 유지, 다음 실행 때 재검증
            consec_fail = 0
            kept_ids = [str(s.get("review_id")) for s in kept if s.get("review_id")]

            # ── write-back (reclassify_keyword_full 과 동일 포맷) ──
            # 상품 귀속은 옵션기반 매핑의 다중 멤버십(products 리스트)을 따른다.
            # (과거엔 rv["product"] 단일만 써서 세트/다중귀속 리뷰의 2차 상품 귀속이
            #  by_product 에서 누락돼 patch_product_mapping 모델과 어긋났음 — 2026-07-10)
            def _prod_list(rv):
                pl = rv.get("products")
                if pl:
                    return pl
                p = rv.get("product")
                return [p] if p else []

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
                pl = _prod_list(rv)
                pn = pl[0] if pl else ""       # 대표 상품(샘플 표시용)
                if per_prod.get(pn, 0) >= 8:
                    continue
                per_prod[pn] = per_prod.get(pn, 0) + 1
                new_samples.append({
                    "review_id": rid,
                    "rating": rv.get("rating", 0),
                    "date": rv.get("date", ""),
                    "text": str(rv.get("text", ""))[:300],
                    "product": pn,
                    "is_set": bool(rv.get("is_set")),
                })
            item["review_samples"] = new_samples
            bp = {}
            for rid in kept_ids:
                rv = rv_idx.get(rid)
                if not rv:
                    continue
                for pn in _prod_list(rv):      # 다중귀속: 리뷰 1건이 여러 상품에 카운트
                    bp[pn] = bp.get(pn, 0) + 1
            item["by_product"] = [
                {"product": p, "count": c}
                for p, c in sorted(bp.items(), key=lambda x: -x[1])
            ]
            removed = before - len(kept_ids)
            if removed:
                changed_total += 1
            flag = "  <== 제거" if removed else ""
            eprint(f"  [{key}] {word}: {before} -> {len(kept_ids)} (-{removed}){flag}")
            save_kw()          # 키워드 단위 즉시 저장 (중단돼도 여기까진 보존)
            mark(tag)          # 완료 표시 (재실행 시 건너뜀)

    # 월 전체 완료 → 개별 진행목록은 비우되, "완료한 polarity 집합"을 영구 기록.
    # (마커를 통째로 지우면 "완료"와 "미시작"이 구분 안 돼 재실행 시 처음부터 다시 돎 — 위 주석 참고)
    save_kw()
    try:
        prog[engine] = []
        dp = prog.get("__done_pol__", {}) or {}
        dp[engine] = sorted(set(dp.get(engine, [])) | set(polarities))
        prog["__done_pol__"] = dp
        prog_path.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    eprint(f"  [OK] 저장 완료: {kpath}  (변경된 키워드 {changed_total}개, 월 완료)")
    return True


def main():
    ap = argparse.ArgumentParser(description="의심 키워드 멤버를 더 큰 모델로 재검증(거짓양성 제거)")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", help="단일 월 (YYYY-MM)")
    ap.add_argument("--months", help="여러 월 쉼표구분 (예: 2026-03,2026-04,2026-05) — 순서대로, 이어받기 가능")
    ap.add_argument("--engine", default="ollama", choices=["ollama", "claude"],
                    help="판정 엔진: ollama(로컬) 또는 claude(구독 CLI, API키 불필요). 기본 ollama")
    ap.add_argument("--model", default=None,
                    help="재검증 모델. 미지정 시 엔진별 기본값(ollama=qwen2.5:14b, claude=sonnet)")
    ap.add_argument("--base-url", default="http://localhost:11434")
    ap.add_argument(
        "--polarities",
        default="complaint,improvement,praise",
        help="재검증 대상 의도 (쉼표구분). 기본 complaint,improvement,praise "
             "(praise 포함 — 7b 재분류가 '추천/만족' 등에 넣은 주제이탈 멤버 제거. "
             "시간 단축이 필요하면 --polarities complaint,improvement 로 축소)",
    )
    args = ap.parse_args()
    polarities = [p.strip() for p in args.polarities.split(",") if p.strip()]
    months = [m.strip() for m in (args.months or args.month or "").split(",") if m.strip()]
    if not months:
        eprint("  [ERROR] --month 또는 --months 필요"); sys.exit(1)

    eprint(f"  의심 키워드 정밀 보정: {args.brand}  월={months}  대상={polarities}  엔진={args.engine}")
    any_ok = False       # 하나라도 성공/완료/스킵된 월이 있는가
    any_fail = False     # 비한도 오류로 실패한 월이 있는가
    for i, mo in enumerate(months):
        eprint(f"\n===== [{i+1}/{len(months)}] {mo} =====")
        res = reverify_month(args.brand, mo, args.model, args.base_url, polarities, engine=args.engine)
        if res == "quota":
            eprint(f"\n  [일시중단] 한도 소진 추정 — {mo}까지 부분 완료. "
                   f"한도 회복 후 '동일 명령'을 다시 실행하면 남은 부분부터 이어서 처리합니다.")
            sys.exit(3)          # 3 = 이어받기 필요(리셋 후 재시도)
        if res:
            any_ok = True
        else:
            any_fail = True
            eprint(f"  [WARN] {mo} 실패/건너뜀")
    # 모든 월이 비한도 오류로 실패(한 건도 처리 못 함) → 성공(exit 0)으로 위장하지 않고 중단.
    # (auto_reverify_loop 은 exit 0을 '완료'로 보므로, 로그인/파일 문제를 성공으로 오인하면 안 됨)
    if any_fail and not any_ok:
        eprint("\n  [중단] 모든 월이 비한도 오류로 실패 — 사람 확인 필요(로그인/파일/환경 등)")
        sys.exit(2)          # 2 = 비한도 실패, 자동 재시도 금지
    eprint("\n  [완료] 모든 월 재검증 종료")
    sys.exit(0)


if __name__ == "__main__":
    main()
