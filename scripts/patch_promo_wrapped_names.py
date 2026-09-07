"""patch_promo_wrapped_names.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
8월 자사몰 CSV에 남아있던 프로모션 래핑 상품명(신금동/힐러쌤/마리코리아/은주언니)을
사용자 확인 답변에 따라 정식 캐노니컬 상품명으로 재배정한다(2026-09-07).

규칙:
  [신금동x슬룸] 최저가 마켓 OPEN
    - 옵션에 "V2" 포함           → 목 마사지 베개 V2
    - 옵션에 "V1" 포함           → 목베개 플러스
  [힐러쌤x슬룸] 최저가 마켓 OPEN  → 목 마사지 베개 V2 (전부 V2 옵션)
  [마리코리아x슬룸] 구독자 전용 ★초특가★ 비밀링크 → 목 마사지 베개 V2
  [은주언니X슬룸] ★뱃살 돌려깎기★ 바디풀고컷 경락 디바이스 → 바디 풀고컷

사용:
  python scripts/patch_promo_wrapped_names.py --dry-run
  python scripts/patch_promo_wrapped_names.py --apply
"""
import argparse, json, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from purge_bulk_reviews import recompute_products, recompute_summary, recompute_keywords, backup  # noqa: E402

DATA_ROOT = ROOT / "docs" / "data" / "슬룸"
MONTH = "2026-08"


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def resolve(product: str, option: str):
    opt = option or ""
    if product == "[신금동x슬룸] 최저가 마켓 OPEN":
        if "V2" in opt:
            return "목 마사지 베개 V2"
        if "V1" in opt:
            return "목베개 플러스"
        return None
    if product == "[힐러쌤x슬룸] 최저가 마켓 OPEN":
        return "목 마사지 베개 V2"
    if product == "[마리코리아x슬룸] 구독자 전용 ★초특가★ 비밀링크":
        return "목 마사지 베개 V2"
    if product == "[은주언니X슬룸] ★뱃살 돌려깎기★ 바디풀고컷 경락 디바이스":
        return "바디 풀고컷"
    return None


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rpath = DATA_ROOT / MONTH / "reviews.json"
    rdata = json.loads(rpath.read_text(encoding="utf-8"))
    reviews = rdata["reviews"]

    renames = {}
    for rid, rv in reviews.items():
        canon = resolve(rv.get("product") or "", rv.get("option") or "")
        if canon and canon != rv.get("product"):
            renames[rid] = (rv.get("product"), canon)

    by_target = {}
    for rid, (old, new) in renames.items():
        by_target.setdefault(new, 0)
        by_target[new] += 1
    eprint(f"[{MONTH}] 재매핑 대상: {len(renames)}건")
    for t, n in sorted(by_target.items(), key=lambda x: -x[1]):
        eprint(f"  -> {t}: {n}건")

    if args.dry_run:
        eprint("\n[dry-run] 실제 변경 없음.")
        return

    for p in (rpath, DATA_ROOT / MONTH / "products.json", DATA_ROOT / MONTH / "summary.json",
              DATA_ROOT / MONTH / "keywords.json"):
        if p.is_file():
            backup(p)

    for rid, (old, new) in renames.items():
        reviews[rid]["product"] = new
        if isinstance(reviews[rid].get("products"), list):
            reviews[rid]["products"] = [new]
    rpath.write_text(json.dumps(rdata, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  reviews.json 저장 완료")

    recompute_products(MONTH, set())
    eprint(f"  products.json 재계산 완료")

    ppath = DATA_ROOT / MONTH / "products.json"
    pdata = json.loads(ppath.read_text(encoding="utf-8"))
    before_n = len(pdata["products"])
    pdata["products"] = [p for p in pdata["products"] if p.get("review_count", 0) > 0]
    after_n = len(pdata["products"])
    ppath.write_text(json.dumps(pdata, ensure_ascii=False, indent=2), encoding="utf-8")
    if before_n != after_n:
        eprint(f"  0건 된 상품 스텁 {before_n - after_n}개 정리 (상품 {before_n} -> {after_n}개)")

    recompute_summary(MONTH)
    eprint(f"  summary.json 재계산 완료")
    recompute_keywords(MONTH, set())
    eprint(f"  keywords.json 재계산 완료")
    eprint("\n[DONE]")


if __name__ == "__main__":
    main()
