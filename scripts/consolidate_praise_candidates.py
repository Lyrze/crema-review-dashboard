"""consolidate_praise_candidates.py <옵션>
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
discover_praise_keywords.py가 뽑은 후보(같은 주제가 다른 표현으로 쪼개져 있음)를
AI 한 번 호출로 비슷한 것끼리 묶어 대표 이름으로 통합한다. keyword_candidates_praise.json을
그대로 덮어써서 merge_discovered_keywords.py가 바로 쓸 수 있게 만든다.

사용:
  python scripts/consolidate_praise_candidates.py --brand 슬룸 --month 2026-06
"""
import argparse, json, sys
from pathlib import Path

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
    args = ap.parse_args()
    model = args.model or "sonnet"

    from claude_engine import ClaudeAnalyzer, is_quota, extract_json_from_response  # noqa: E402

    d = ROOT / "docs" / "data" / args.brand / args.month
    cpath = d / "keyword_candidates_praise.json"
    if not cpath.is_file():
        eprint(f"[ERROR] {cpath} 없음"); sys.exit(1)

    cdata = json.loads(cpath.read_text(encoding="utf-8"))
    cands = cdata.get("candidates") or []
    if len(cands) < 3:
        eprint(f"  후보 {len(cands)}개 — 통합 불필요, 그대로 둠")
        return

    eprint(f"  후보 {len(cands)}개 → AI로 유사 항목 통합 중...")
    lines = [f"[{i}] {c['word']} ({c['count']}건)" for i, c in enumerate(cands)]
    prompt = (
        "다음은 제품 리뷰에서 뽑은 '칭찬 키워드 후보' 목록이다. 같은 주제인데 표현만 다르게 "
        "쪼개진 항목들을 하나의 그룹으로 묶어라(예: '허리 통증 완화'/'허리/골반 통증 완화'/"
        "'허리 통증 완화 및 편안한 자세' → 한 그룹).\n"
        "- 그룹당 대표 이름(canonical)은 가장 짧고 명확한 한국어 명사구로 새로 지어라.\n"
        "- 완전히 다른 주제는 절대 합치지 마라(부위가 다르거나 기능이 다르면 별도 그룹).\n"
        "- 최종적으로 15~25개 그룹 정도로 정리하라(너무 잘게 쪼개지 않되 억지로 합치지도 마라).\n\n"
        "[후보 목록]\n" + "\n".join(lines) + "\n\n"
        'JSON 배열로만 출력: [{"canonical":"통합된 이름","members":[0,3,7]}]  '
        "(members는 위 후보 번호 배열, 모든 후보가 정확히 하나의 그룹에 한 번씩만 들어가야 한다)"
    )

    analyzer = ClaudeAnalyzer(model=model)
    if not analyzer.health_check():
        err = str(getattr(analyzer, "last_error", "") or "")
        if is_quota(err):
            eprint(f"[STOP] 한도 소진 — 재실행 시 이어집니다. ({err[:200]})"); sys.exit(3)
        eprint(f"[ERROR] Claude 무응답 ({err})"); sys.exit(2)

    try:
        raw = analyzer.client.generate(model=analyzer.model, prompt=prompt,
                                        system="당신은 한국어 VOC 분석 전문가입니다. JSON으로만 답하세요.",
                                        temperature=0.1)
    except Exception as e:
        if is_quota(e):
            eprint(f"[STOP] 한도 소진 — 재실행 시 이어집니다. ({str(e)[:200]})"); sys.exit(3)
        eprint(f"[ERROR] {e}"); sys.exit(2)

    groups = extract_json_from_response(raw)
    if not isinstance(groups, list):
        eprint("[ERROR] AI 응답 파싱 실패 — 원본 그대로 둠"); sys.exit(2)

    used = set()
    merged_cands = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        name = str(g.get("canonical", "")).strip()
        idxs = [i for i in (g.get("members") or []) if isinstance(i, int) and 0 <= i < len(cands) and i not in used]
        if not name or not idxs:
            continue
        used.update(idxs)
        rid_set, samples, total = set(), [], 0
        for i in idxs:
            c = cands[i]
            total += c.get("count", 0)
            rid_set.update(c.get("review_ids") or [])
            samples.extend(c.get("samples") or [])
        merged_cands.append({
            "word": name, "type": "praise", "count": len(rid_set),
            "review_ids": list(rid_set), "samples": samples[:4],
        })
    # 그룹 안 된 나머지는 원본 그대로 유지(누락 방지)
    for i, c in enumerate(cands):
        if i not in used:
            merged_cands.append(c)

    merged_cands.sort(key=lambda x: x["count"], reverse=True)
    cdata["candidates"] = merged_cands
    cdata["consolidated_from"] = len(cands)
    cpath.write_text(json.dumps(cdata, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  [OK] {len(cands)}개 -> {len(merged_cands)}개로 통합 완료")
    for c in merged_cands:
        eprint(f"     · {c['word']} ({c['count']})")


if __name__ == "__main__":
    main()
