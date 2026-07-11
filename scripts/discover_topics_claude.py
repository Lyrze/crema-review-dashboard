"""discover_topics_claude.py — 잔여 미분류에서 '신규 Topic 후보' 발굴 + 기존 Topic 키워드 확장 제안
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
classify_unclassified(기존 Topic 배정)까지 끝난 뒤에도 남는 미분류는 대부분
"맞는 서랍(Topic)이 없는" 리뷰다. 이 스크립트는 Claude 로 그 잔여를 읽어:

  A) 기존 Topic 에 내용상 속하는데 키워드가 없어 안 잡힌 경우
       → 해당 Topic 의 키워드 확장 제안 (규칙이 좋아지면 이후 AI 호출 없이도 매칭)
  B) 기존 어떤 Topic 에도 없는 반복 주제
       → 신규 Topic 후보 (이름 + 추천 키워드 + 소속 리뷰) 로 통합 제안

산출: docs/data/{brand}/tx_topic_suggestions.json  (검토형 — 원본 Taxonomy 는 절대 수정 안 함)
담당자가 대시보드 Taxonomy 에서 Topic/키워드를 직접 추가할 때 참고하는 제안서다.

종료코드 계약(quota_retry 호환): 0=완료 · 3=한도 소진(이어받기 마커 저장) · 2=비한도 실패
이어받기: docs/data/{brand}/.topic_progress.json (배치 단위, 완료 시 삭제)

사용:
  python scripts/quota_retry.py -- python scripts/discover_topics_claude.py --brand 슬룸 --months 2026-03,2026-04,2026-05
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from classify_unclassified import (  # noqa: E402 — 동일 매칭 규칙/스냅샷 로더 재사용
    latest_snapshot, load_taxonomies, match_topic, is_quota,
)


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def collect_residual(brand: str, months: list, topics: list, taxonomies: list, min_len: int):
    """잔여 미분류 수집 = 규칙 미매칭 · 수동분류 아님 · classify 제안(auto/검토)에도 없음."""
    included = set()
    for t in taxonomies:
        for rid, cls in (t.get("manualClassifications") or {}).items():
            if cls and cls.get("include"):
                included.add(str(rid))
    residual = []
    for m in months:
        base = ROOT / "docs" / "data" / brand / m
        rpath = base / "reviews.json"
        if not rpath.is_file():
            eprint(f"  [WARN] {m}: reviews.json 없음 — 건너뜀")
            continue
        reviews = json.loads(rpath.read_text(encoding="utf-8")).get("reviews", {})
        assigned = set()
        spath = base / "tx_suggestions.json"
        if spath.is_file():
            sug = json.loads(spath.read_text(encoding="utf-8"))
            assigned = {str(s["review_id"]) for s in (sug.get("auto") or []) + (sug.get("suggestions") or [])}
        n0 = 0
        for rid, r in reviews.items():
            text = (r.get("text") or "").strip()
            if len(text) < min_len or str(rid) in included or str(rid) in assigned:
                continue
            lt = text.lower()
            if any(match_topic(tp["topic"], lt) for tp in topics):
                continue
            residual.append({"key": f"{m}::{rid}", "month": m, "rid": str(rid),
                             "text": text, "rating": r.get("rating")})
            n0 += 1
        eprint(f"  {m}: 잔여 미분류(내용 {min_len}자+) {n0}건")
    return residual


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--months", required=True, help="쉼표구분 (예: 2026-03,2026-04,2026-05)")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--min-len", type=int, default=25, help="분석 대상 최소 본문 길이")
    ap.add_argument("--batch", type=int, default=12)
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]

    snap = latest_snapshot(args.brand)
    if not snap:
        eprint("  [SKIP] Taxonomy 스냅샷 없음"); sys.exit(0)
    taxonomies = load_taxonomies(snap)
    topics = []
    for t in taxonomies:
        for tp in (t.get("topics") or []):
            topics.append({"taxId": t.get("id"), "taxName": t.get("name"),
                           "topicId": tp.get("id"), "name": tp.get("name"),
                           "keywords": (tp.get("keywords") or [])[:6], "topic": tp})
    eprint(f"  스냅샷: {snap.relative_to(ROOT)} (topic {len(topics)})")

    residual = collect_residual(args.brand, months, topics, taxonomies, args.min_len)
    out_path = ROOT / "docs" / "data" / args.brand / "tx_topic_suggestions.json"
    if not residual:
        eprint("  잔여 미분류 없음 — 종료"); sys.exit(0)
    eprint(f"  분석 대상 총 {len(residual)}건")

    from claude_engine import ClaudeAnalyzer  # noqa: E402
    analyzer = ClaudeAnalyzer(model=args.model)
    if not analyzer.health_check():
        err = getattr(analyzer, "last_error", "") or ""
        if is_quota(err):
            eprint(f"  [STOP] 한도 소진 — 리셋 후 재실행 시 이어집니다. ({err[:200]})")
            sys.exit(3)
        eprint("  [ERROR] Claude CLI 무응답 — 중단"); sys.exit(2)

    # ── 이어받기 마커 ──
    prog_path = ROOT / "docs" / "data" / args.brand / ".topic_progress.json"
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8")) if prog_path.is_file() else {}
    except Exception:
        prog = {}
    processed = set(prog.get("processed", []))
    expansions = prog.get("expansions", [])   # [{topic_idx, keyword, key}]
    themes = prog.get("themes", [])           # [{theme, key}]

    def save_prog(**kw):
        prog.update(kw)
        prog["processed"] = sorted(processed)
        prog["expansions"] = expansions
        prog["themes"] = themes
        prog_path.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")

    topic_lines = "\n".join(f"[{i+1}] {o['taxName']} > {o['name']} (키워드: {', '.join(map(str, o['keywords']))})"
                            for i, o in enumerate(topics))
    by_key = {r["key"]: r for r in residual}
    target = [r for r in residual if r["key"] not in processed]
    if processed:
        eprint(f"  [RESUME] 이미 분석 {len(processed)}건 건너뜀 — 남은 {len(target)}건 "
               f"(확장 {len(expansions)} · 새주제 {len(themes)} 누적)")

    # ── Pass A: 배치 판정 (기존Topic 키워드확장 / 새 주제 / 스킵) ──
    B = max(6, args.batch)
    for bi in range(0, len(target), B):
        batch = target[bi:bi + B]
        rev_lines = "\n".join(f"({i}) *{r['rating']} {r['text'][:160]}".replace("\n", " ")
                              for i, r in enumerate(batch))
        prompt = (
            "아래는 기존 Topic 규칙에 안 잡힌 '미분류' 상품 리뷰다. 각 리뷰를 판정하라:\n"
            "1) 내용상 기존 Topic에 속하는데 키워드가 없어 안 잡힌 경우 → "
            '{"review":0,"fit":3,"add_keyword":"수면"} (fit=Topic번호, add_keyword=그 Topic 키워드에 '
            "추가하면 이 리뷰가 잡힐 핵심 단어 1개, 리뷰 본문에 실제로 있는 단어)\n"
            "2) 기존 어떤 Topic에도 없는 새로운 주제 → "
            '{"review":1,"theme":"수면 개선"} (2~8자 일반화된 주제명)\n'
            '3) 실질 내용이 없거나 판단 불가 → {"review":2,"skip":true}\n\n'
            f"[기존 Topic 목록]\n{topic_lines}\n\n[리뷰]\n{rev_lines}\n\n"
            "JSON 배열로만 답하라."
        )
        try:
            raw = analyzer.client.generate(model=analyzer.model, prompt=prompt,
                                           system="당신은 한국어 VOC 분류 체계 설계 전문가입니다. JSON으로만 답하세요.",
                                           temperature=0.0)
        except Exception as e:  # noqa: BLE001
            if is_quota(e):
                save_prog()
                eprint(f"  [STOP] 한도 소진 — 진행분 저장(분석 {len(processed)}건). "
                       f"재실행 시 이어집니다. ({str(e)[:200]})")
                sys.exit(3)
            eprint(f"   [ERR] 배치 {bi//B+1}: {str(e)[:100]}")
            continue
        for r in batch:
            processed.add(r["key"])
        m = re.search(r"\[[\s\S]*\]", str(raw or ""))
        if m:
            try:
                arr = json.loads(m.group(0))
            except Exception:
                arr = []
            for o in arr if isinstance(arr, list) else []:
                try:
                    ri = int(o.get("review"))
                except Exception:
                    continue
                if not (0 <= ri < len(batch)):
                    continue
                key = batch[ri]["key"]
                if o.get("skip"):
                    continue
                if o.get("theme"):
                    themes.append({"theme": str(o["theme"]).strip()[:20], "key": key})
                elif o.get("fit") and o.get("add_keyword"):
                    try:
                        ti = int(o["fit"])
                    except Exception:
                        continue
                    if 1 <= ti <= len(topics):
                        expansions.append({"topic_idx": ti - 1,
                                           "keyword": str(o["add_keyword"]).strip().lower()[:20],
                                           "key": key})
        save_prog()
        eprint(f"   배치 {bi//B+1}/{(len(target)+B-1)//B} — 확장 {len(expansions)} · 새주제 {len(themes)}")

    save_prog(passA_done=True)

    # ── Pass B: 새 주제 통합 (유사 주제 병합 → 최종 Topic 후보) ──
    final_topics = prog.get("final_topics")
    if themes and not final_topics:
        cnt = Counter(t["theme"] for t in themes)
        sample_by_theme = {}
        for t in themes:
            sample_by_theme.setdefault(t["theme"], [])
            if len(sample_by_theme[t["theme"]]) < 2:
                sample_by_theme[t["theme"]].append(by_key.get(t["key"], {}).get("text", "")[:70].replace("\n", " "))
        theme_lines = "\n".join(f'- "{th}" ({c}건) 예: ' + " / ".join(f'"{s}"' for s in sample_by_theme.get(th, []))
                                for th, c in cnt.most_common(40))
        bprompt = (
            "아래는 미분류 리뷰에서 발견된 새 주제 후보와 빈도, 샘플이다. "
            "유사/중복 주제를 병합해 최종 신규 Topic 후보로 정리하라 (최대 8개, 2건 이상 주제 위주).\n\n"
            f"{theme_lines}\n\n"
            'JSON 배열로만: [{"name":"수면 개선","keywords":["수면","잠","숙면"],"merge":["수면 개선","잠 잘옴"]}]\n'
            "(keywords=리뷰 매칭용 핵심 단어 3~6개, merge=이 Topic으로 통합할 위 주제명 전부)"
        )
        try:
            raw = analyzer.client.generate(model=analyzer.model, prompt=bprompt,
                                           system="당신은 한국어 VOC 분류 체계 설계 전문가입니다. JSON으로만 답하세요.",
                                           temperature=0.0)
        except Exception as e:  # noqa: BLE001
            if is_quota(e):
                save_prog()
                eprint(f"  [STOP] 한도 소진(통합 단계) — 재실행 시 이어집니다. ({str(e)[:200]})")
                sys.exit(3)
            eprint(f"  [ERR] 통합 실패: {str(e)[:100]} — 원시 주제 그대로 출력")
            raw = ""
        final_topics = []
        m = re.search(r"\[[\s\S]*\]", str(raw or ""))
        if m:
            try:
                for o in json.loads(m.group(0)):
                    if isinstance(o, dict) and o.get("name"):
                        final_topics.append({"name": str(o["name"])[:30],
                                             "keywords": [str(k).strip() for k in (o.get("keywords") or [])][:8],
                                             "merge": [str(x).strip() for x in (o.get("merge") or [])]})
            except Exception:
                pass
        save_prog(final_topics=final_topics)

    # ── 산출물 구성 ──
    # 신규 Topic 후보: merge 매핑으로 소속 리뷰 연결
    new_topics_out = []
    for ft in (final_topics or []):
        merged = set(ft.get("merge") or []) | {ft["name"]}
        members = [t["key"] for t in themes if t["theme"] in merged]
        if not members:
            continue
        mids = []
        for k in members:
            r = by_key.get(k)
            if r:
                mids.append({"month": r["month"], "review_id": r["rid"],
                             "text": r["text"][:120]})
        new_topics_out.append({"name": ft["name"], "keywords": ft["keywords"],
                               "count": len(mids), "reviews": mids})
    new_topics_out.sort(key=lambda x: -x["count"])

    # 키워드 확장 제안: (topic, keyword) 집계
    exp_cnt = Counter((e["topic_idx"], e["keyword"]) for e in expansions)
    exp_out = []
    for (ti, kw), c in exp_cnt.most_common():
        t = topics[ti]
        keys = [e["key"] for e in expansions if e["topic_idx"] == ti and e["keyword"] == kw]
        exp_out.append({"tax_name": t["taxName"], "topic_name": t["name"], "topic_id": t["topicId"],
                        "add_keyword": kw, "count": c,
                        "reviews": [{"month": by_key[k]["month"], "review_id": by_key[k]["rid"],
                                     "text": by_key[k]["text"][:120]} for k in keys if k in by_key]})

    out = {"brand": args.brand, "months": months,
           "source_snapshot": str(snap.relative_to(ROOT)),
           "analyzed": len(processed), "residual_total": len(residual),
           "new_topic_candidates": new_topics_out,
           "keyword_expansions": exp_out}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        if prog_path.is_file():
            prog_path.unlink()
    except Exception:
        pass

    eprint(f"\n  [OK] 신규 Topic 후보 {len(new_topics_out)}개 · 키워드 확장 제안 {len(exp_out)}건 → {out_path.relative_to(ROOT)}")
    for nt in new_topics_out[:8]:
        eprint(f"    · [신규] {nt['name']} ({nt['count']}건) 키워드: {', '.join(nt['keywords'])}")
    for e in exp_out[:8]:
        eprint(f"    · [확장] {e['topic_name']} += '{e['add_keyword']}' ({e['count']}건)")
    sys.exit(0)


if __name__ == "__main__":
    main()
