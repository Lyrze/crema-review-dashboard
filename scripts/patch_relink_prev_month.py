"""patch_relink_prev_month.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
process_data.py는 최초 처리 시점에만 prev_review_count/prev_avg_rating을
"이번 달 상품명 == 전월 상품명" 완전일치로 계산한다(calc_products의 prev_lookup).
이후 어떤 패치 스크립트(리네임/컨솔리데이트/채널 병합)로 상품명이 바뀌면, 그 뒤로는
아무도 이 필드를 다시 계산하지 않아서 실제로는 전월 데이터가 있는 상품인데도
"new"(전월 없음)로 잘못 표시된다.

발견 경위(2026-09-10): "프리미엄 엘보케어 팔꿈치 마사지기"(8월)가 SKU 변화 테이블에서
계속 "new"로 뜬다는 사용자 리포트 → 5~7월엔 "팔꿈치 마사지기"라는 이름으로 존재하다
8월부터 실제로 상품명이 바뀐 것으로 확인. 같은 세션에서 처리했던 "허리편한케어 마스터
→ 복부 순환 마스터 프리미엄 복부 마사지기" 건도 동일하게 prev_review_count가
깨져 있었음(0으로 잘못 고정, 실제는 전월 2건).

이 스크립트는 대상월의 products.json을 (이미 리네임 등이 반영된) 전월 products.json과
다시 이름 매칭해서 prev_review_count/prev_avg_rating을 갱신한다. 전월 항목의
review_count가 0(다른 사유로 비워진 스텁)이면 실제 "전월 데이터 있음"이 아니므로
None(전월 없음)으로 취급한다.

summary.json 전월 KPI(2026-10-21 추가): process_data.py 는 summary.json 의
mom_review_change / mom_review_change_pct / mom_rating_change 를 --prev-input 원본
CSV(가짜 리뷰 제거·스마트스토어·쿠팡 병합 전) 기준으로 계산해, 실제 대시보드의 전월
summary.json 과 어긋난 값이 남았다(예: 2026-08 = +1029, 141.5% — 실제 7월은 1416건).
그래서 이 스크립트가 상품 전월값과 함께 summary.json kpis 의 세 필드도 전월
summary.json(total_reviews, avg_rating) 기준으로 다시 계산한다. 전월 summary.json 이
없으면 세 필드는 None(전월 없음).

사용:
  python scripts/patch_relink_prev_month.py --month 2026-08 --dry-run
  python scripts/patch_relink_prev_month.py --month 2026-08 --apply
"""
import argparse, json, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from purge_bulk_reviews import backup  # noqa: E402


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def _prev_month(m):
    y, mo = [int(x) for x in str(m).split("-")[:2]]
    mo -= 1
    if mo == 0:
        y, mo = y - 1, 12
    return f"{y:04d}-{mo:02d}"


def _next_month(m):
    y, mo = [int(x) for x in str(m).split("-")[:2]]
    mo += 1
    if mo == 13:
        y, mo = y + 1, 1
    return f"{y:04d}-{mo:02d}"


MOM_FIELDS = ("mom_review_change", "mom_review_change_pct", "mom_rating_change")


def calc_summary_mom(kpis, prev_kpis):
    """전월 summary.json kpis 기준 MoM 세 필드를 계산한다(전월 없으면 전부 None).
    validate_data.py 도 같은 함수로 정합성을 점검한다."""
    out = dict.fromkeys(MOM_FIELDS)
    if not prev_kpis:
        return out
    total, prev_total = kpis.get("total_reviews"), prev_kpis.get("total_reviews")
    avg, prev_avg = kpis.get("avg_rating"), prev_kpis.get("avg_rating")
    if isinstance(total, int) and isinstance(prev_total, int) and prev_total > 0:
        out["mom_review_change"] = total - prev_total
        out["mom_review_change_pct"] = round((total - prev_total) / prev_total * 100, 1)
    if avg is not None and prev_avg is not None:
        out["mom_rating_change"] = round(avg - prev_avg, 2)
    return out


def load_prev_kpis(brand, month):
    """전월 summary.json 의 kpis(없으면 None)."""
    prev_spath = ROOT / "docs" / "data" / brand / _prev_month(month) / "summary.json"
    if not prev_spath.is_file():
        return None
    return json.loads(prev_spath.read_text(encoding="utf-8")).get("kpis") or None


def relink_summary_mom(brand, month, apply=False, quiet=False):
    """대상월 summary.json kpis 의 MoM 세 필드를 전월 summary.json 기준으로 재계산한다.
    반환값: 변경된 필드 수(summary.json 없으면 0)."""
    log = (lambda *a, **k: None) if quiet else eprint
    spath = ROOT / "docs" / "data" / brand / month / "summary.json"
    if not spath.is_file():
        log(f"[{month}] summary.json 없음 — MoM 재계산 건너뜀")
        return 0

    sdata = json.loads(spath.read_text(encoding="utf-8"))
    kpis = sdata.setdefault("kpis", {})
    prev_kpis = load_prev_kpis(brand, month)
    want = calc_summary_mom(kpis, prev_kpis)

    changes = [(k, kpis.get(k), v) for k, v in want.items() if kpis.get(k) != v]
    log(f"[{month}] summary MoM(전월={_prev_month(month)}{'' if prev_kpis else ' 없음'}) 재계산 대상 {len(changes)}건")
    for k, old, new in changes:
        log(f"  {k}: {old} -> {new}")
    if not apply or not changes:
        return len(changes)

    kpis.update(want)
    backup(spath)
    spath.write_text(json.dumps(sdata, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[DONE] {spath} 저장 완료")
    return len(changes)


def relink_month(brand, month, apply=False, quiet=False):
    """대상월 products.json의 prev_review_count/prev_avg_rating을 전월 products.json과
    상품명 완전일치로 다시 계산한다. 리네임/컨솔리데이트/채널 병합으로 상품명이나 전월
    데이터가 바뀐 뒤에도 호출 가능하도록 재사용 가능한 함수로 분리했다(consolidate_product.py
    등에서 병합 직후 자동으로 다시 연결시키는 데 사용).

    summary.json kpis 의 MoM 세 필드도 relink_summary_mom() 으로 함께 재계산한다.

    반환값: 변경된 항목 수(상품 + summary 필드). 전월 products.json이 없으면 상품
    재연결은 건너뛴다.
    """
    log = (lambda *a, **k: None) if quiet else eprint
    n_summary = relink_summary_mom(brand, month, apply=apply, quiet=quiet)
    data_root = ROOT / "docs" / "data" / brand
    prev_month = _prev_month(month)
    ppath = data_root / month / "products.json"
    prev_ppath = data_root / prev_month / "products.json"

    if not ppath.is_file():
        log(f"[{month}] products.json 없음 — 건너뜀")
        return n_summary
    if not prev_ppath.is_file():
        log(f"[{month}] 전월({prev_month}) products.json 없음 — 건너뜀")
        return n_summary

    pdata = json.loads(ppath.read_text(encoding="utf-8"))
    prev_data = json.loads(prev_ppath.read_text(encoding="utf-8"))
    prev_lookup = {
        p["name"]: {"rc": p.get("review_count", 0), "avg": p.get("avg_rating")}
        for p in prev_data["products"]
    }

    changes = []
    for p in pdata["products"]:
        prev = prev_lookup.get(p["name"])
        has_real_prev = bool(prev) and (prev["rc"] or 0) > 0
        want_rc = prev["rc"] if has_real_prev else None
        want_avg = prev["avg"] if has_real_prev else None
        cur_rc, cur_avg = p.get("prev_review_count"), p.get("prev_avg_rating")
        if cur_rc != want_rc or cur_avg != want_avg:
            changes.append((p["name"], cur_rc, want_rc, cur_avg, want_avg))
            p["prev_review_count"] = want_rc
            p["prev_avg_rating"] = want_avg

    log(f"[{month}] (전월={prev_month}) 재연결 대상 {len(changes)}건")
    for name, old_rc, new_rc, old_avg, new_avg in changes:
        log(f"  {name}: prev_review_count {old_rc} -> {new_rc}, prev_avg_rating {old_avg} -> {new_avg}")

    if not apply:
        log("\n[dry-run] 실제 변경 없음.")
        return len(changes) + n_summary

    if changes:
        backup(ppath)
        ppath.write_text(json.dumps(pdata, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"\n[DONE] {ppath} 저장 완료")
    else:
        log("\n[DONE] 변경 없음")
    return len(changes) + n_summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="슬룸")
    ap.add_argument("--month", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    relink_month(args.brand, args.month, apply=args.apply)


if __name__ == "__main__":
    main()
