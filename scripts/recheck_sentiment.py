"""recheck_sentiment.py — 감성 오판 의심 리뷰를 Claude로 재판정해 교정
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Ollama 7b 감성판정 중 '부정어 포함하지만 결말은 긍정'류(예: "아팠는데 풀렸어요")를
부정으로 오판하는 케이스를 Claude가 재판정해 reviews.json 의 sentiment 만 교정한다.

대상(의심 모집단): 기본 = ★4~5인데 sentiment='negative' (오판이 몰린 구간).
  --include-pos3 지정 시 ★3인데 sentiment='positive' 도 포함.

교정 반영: reviews.json sentiment 갱신 → products.json / summary.json 의
  긍정률·부정률·감성 카운트 재계산 (감성 외 필드·키워드 분류는 불변).

종료코드 계약(quota_retry 호환): 0=완료 · 3=한도 소진(이어받기) · 2=비한도 실패
이어받기: docs/data/{brand}/{month}/.sentiment_progress.json (완료 시 삭제)

사용:
  python scripts/quota_retry.py -- python scripts/recheck_sentiment.py --brand 슬룸 --months 2026-03,2026-04,2026-05
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_engine import ClaudeClient  # noqa: E402
from classify_unclassified import is_quota  # noqa: E402

LABELS = {"긍정": "positive", "부정": "negative", "중립": "neutral"}


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def suspects(reviews, include_pos3, full=False):
    # full=True → 별점·라벨 무관 전건 재판정 (본문 있는 리뷰 전부)
    if full:
        return [rid for rid, r in reviews.items() if (r.get("text") or "").strip()]
    out = []
    for rid, r in reviews.items():
        rt = r.get("rating") or 0
        s = r.get("sentiment")
        if rt >= 4 and s == "negative":
            out.append(rid)
        elif include_pos3 and rt == 3 and s == "positive":
            out.append(rid)
    return out


def recompute_products(reviews, products):
    """상품별 sentiment 카운트·긍정률·부정률 재계산 (다중귀속 멤버십 기준)."""
    agg = {}
    for r in reviews.values():
        s = r.get("sentiment", "neutral")
        for pn in (r.get("products") or ([r["product"]] if r.get("product") else [])):
            a = agg.setdefault(pn, Counter())
            a[s] += 1
    for p in products:
        a = agg.get(p["name"])
        if not a:
            continue
        pos, neu, neg = a.get("positive", 0), a.get("neutral", 0), a.get("negative", 0)
        tot = pos + neu + neg
        p["sentiment"] = {"positive": pos, "neutral": neu, "negative": neg}
        p["positive_rate"] = round(pos / tot * 100, 2) if tot else 0
        p["negative_rate"] = round(neg / tot * 100, 2) if tot else 0


def recompute_summary(reviews, summary):
    """summary KPI 의 긍정률·부정률 재계산 (감성 기준)."""
    c = Counter(r.get("sentiment", "neutral") for r in reviews.values())
    tot = sum(c.values())
    if not tot or "kpis" not in summary:
        return
    summary["kpis"]["positive_rate"] = round(c.get("positive", 0) / tot * 100, 2)
    summary["kpis"]["negative_rate"] = round(c.get("negative", 0) / tot * 100, 2)


def recheck_month(brand, month, cli, include_pos3, full=False):
    base = ROOT / "docs" / "data" / brand / month
    rpath = base / "reviews.json"
    if not rpath.is_file():
        eprint(f"  [WARN] {month}: reviews.json 없음 — 건너뜀")
        return None
    rdoc = json.loads(rpath.read_text(encoding="utf-8"))
    reviews = rdoc.get("reviews", {})

    mode = "full" if full else "targeted"
    prog_path = base / ".sentiment_progress.json"
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8")) if prog_path.is_file() else {}
    except Exception:
        prog = {}
    if prog.get("mode") and prog.get("mode") != mode:   # 다른 모드 진행분과 섞이지 않게 초기화
        prog = {}
    prog["mode"] = mode
    if prog.get("done"):
        eprint(f"  [SKIP] {month} 이미 감성 재검증 완료({mode})")
        return 0
    done = set(prog.get("done_ids", []))
    fixes = prog.get("fixes", {})   # rid -> new_sentiment(kr internal english)

    ids = [x for x in suspects(reviews, include_pos3, full=full) if x not in done]
    eprint(f"  {month}: 의심 {len(ids)+len(done)}건 · 남은 {len(ids)}건 (이미 {len(done)})")

    def save_prog():
        prog["done_ids"] = sorted(done)
        prog["fixes"] = fixes
        prog_path.write_text(json.dumps(prog, ensure_ascii=False), encoding="utf-8")

    B = 10
    for i in range(0, len(ids), B):
        chunk = ids[i:i+B]
        lines = []
        for j, rid in enumerate(chunk):
            t = (reviews[rid].get("text") or "")[:220].replace("\n", " ")
            lines.append(f"({j}) {t}")
        prompt = ("각 상품 리뷰의 전반적 감성을 본문 내용 기준으로 판정하라(별점 무시). "
                  "제품 불만/문제 지적이 핵심이면 부정, 만족/호평이 핵심이면 긍정, "
                  "정보성·애매·혼재는 중립. '아팠는데 풀렸다' 처럼 부정어가 있어도 "
                  "결말이 만족이면 긍정이다.\n\n" + "\n".join(lines) +
                  '\n\nJSON 배열로만: [{"i":0,"s":"긍정"}]')
        try:
            raw = cli.generate("sonnet", prompt,
                               system="한국어 리뷰 감성 분석가. JSON으로만 답.", temperature=0.0)
        except Exception as e:  # noqa: BLE001
            if is_quota(e):
                save_prog()
                eprint(f"  [STOP] 한도 소진 — 진행분 저장(교정 {len(fixes)}건). 재실행 시 이어집니다. ({str(e)[:200]})")
                return "quota"
            eprint(f"   [ERR] 배치: {str(e)[:100]}")
            continue
        m = re.search(r"\[[\s\S]*\]", raw or "")
        jm = {}
        if m:
            try:
                for o in json.loads(m.group(0)):
                    jm[int(o["i"])] = LABELS.get(str(o.get("s")).strip())
            except Exception:
                pass
        for j, rid in enumerate(chunk):
            new = jm.get(j)
            done.add(rid)
            if new and new != reviews[rid].get("sentiment"):
                fixes[rid] = new
        save_prog()
        eprint(f"   {min(i+B,len(ids))}/{len(ids)} — 교정 후보 {len(fixes)}건")

    # 교정 적용
    changed = 0
    for rid, new in fixes.items():
        if rid in reviews and reviews[rid].get("sentiment") != new:
            reviews[rid]["sentiment"] = new
            changed += 1
    if changed:
        rpath.write_text(json.dumps(rdoc, ensure_ascii=False, indent=2), encoding="utf-8")
        ppath = base / "products.json"
        if ppath.is_file():
            pdoc = json.loads(ppath.read_text(encoding="utf-8"))
            plist = pdoc.get("products") if isinstance(pdoc, dict) else pdoc
            recompute_products(reviews, plist)
            ppath.write_text(json.dumps(pdoc, ensure_ascii=False, indent=2), encoding="utf-8")
        spath = base / "summary.json"
        if spath.is_file():
            sdoc = json.loads(spath.read_text(encoding="utf-8"))
            recompute_summary(reviews, sdoc)
            spath.write_text(json.dumps(sdoc, ensure_ascii=False, indent=2), encoding="utf-8")
    # 완료 표시를 '보존'한다(삭제 금지). 과거엔 unlink 했다가, 다음 윈도우 재실행 시
    # 완료월이 '미시작'으로 보여 처음부터 재판정 → 감성 thrash(요동) 버그가 있었음(2026-07-14).
    prog["done"] = True
    save_prog()
    eprint(f"  [OK] {month}: {changed}건 감성 교정 · products/summary KPI 재계산")
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--months", required=True)
    ap.add_argument("--include-pos3", action="store_true", help="★3인데 positive 인 것도 재검증")
    ap.add_argument("--full", action="store_true", help="별점·라벨 무관 전건 재판정")
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]

    cli = ClaudeClient()
    try:
        if not (cli.generate("sonnet", "핑. 한 글자로만: ok", temperature=0.0) or "").strip():
            eprint("  [ERROR] Claude 무응답"); sys.exit(2)
    except Exception as e:  # noqa: BLE001
        if is_quota(e):
            eprint(f"  [STOP] 한도 소진 — 재실행 시 이어집니다. ({str(e)[:200]})"); sys.exit(3)
        eprint(f"  [ERROR] Claude 실패: {str(e)[:120]}"); sys.exit(2)

    any_ok = False
    total_fix = 0
    for mo in months:
        eprint(f"\n===== {mo} =====")
        res = recheck_month(args.brand, mo, cli, args.include_pos3, full=args.full)
        if res == "quota":
            sys.exit(3)
        if res is None:
            continue
        any_ok = True
        total_fix += res
    eprint(f"\n  [완료] 총 {total_fix}건 감성 교정")
    sys.exit(0 if any_ok else 2)


if __name__ == "__main__":
    main()
