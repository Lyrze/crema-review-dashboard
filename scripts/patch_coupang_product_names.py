"""patch_coupang_product_names.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
쿠팡 리뷰(cp_ 접두사)는 convert_coupang.py가 "상품명 = CSV 파일명"으로 넣기 때문에,
쿠팡 판매 페이지 제목 그대로가 상품명이 돼서 기존 21개 캐노니컬 상품과 분리된다.
consolidate_product.py의 product_consolidate.json은 옛 매핑 4건뿐이라 이번(2026-08)
신규 쿠팡 CSV 18개는 대부분 안 잡혔고, 그중 하나("프리미엄 엘보케어 팔꿈치 마사지기"
→ "팔꿈치 마사지기")는 오히려 기존 캐노니컬을 깨는 역방향 매핑이었다(2026-09-07 확인).

파일명(=리뷰 product 필드) → 캐노니컬 직접 매핑.

사용:
  python scripts/patch_coupang_product_names.py --dry-run
  python scripts/patch_coupang_product_names.py --apply
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

RENAME_MAP = {
    # consolidate_product.py가 거꾸로 바꿔놓은 것 원복
    "팔꿈치 마사지기": "프리미엄 엘보케어 팔꿈치 마사지기",
    # 쿠팡 CSV 파일명(=상품명) → 캐노니컬
    "슬룸 허리편한케어 공기압 에어리프트 EMS 온열 허리 마사지기": "허리편한케어 V1",  # 옵션 SL23EQ02 = V1 (process_data.py 기존 동의어)
    "슬룸 넥숄더 힐링케어 V2 무선 목 어깨 마사지기 안마기 승모근 온열 찜질": "넥숄더 힐링케어 V2",
    "슬룸 팔꿈치 마사지기 안마기 EMS TENS 온열 엘보 전완근 무선 휴대용": "프리미엄 엘보케어 팔꿈치 마사지기",
    "슬룸 목 마사지 베개 V2 4세대 무선 마사지기 안마기 목베개 경추베개": "목 마사지 베개 V2",
    "슬룸 허리베개 디스크 기능성 8포인트 지압 공법 요추 인체공학 메모리폼 베개": "허리베개",
    "슬룸 목베개 메모리폼 편한 베개": "목베개",   # 사용자 확정(2026-09-07): 버전 미표기 목베개는 별도 캐노니컬
    "슬룸 손편한케어 마사지기": "손편한케어",
    "슬룸 원적외선 온열 안마 베개 목 어깨 승모근 안마기 목편한 케어, SL-001, 1개": "목편한케어",
    "슬룸 목편한케어 플라잉": "목편한케어 플라잉",
    "슬룸 바디풀고컷 EMS 복부 뱃살 마사지기 안마기 옆구리살 셀룰라이트": "바디 풀고컷",
    "슬룸 넥숄더 프로 초강력 목 어깨 마사지기 무선 온열 베개형 안마기": "넥숄더 프로",
    "팔꿈치 마사지기 안마기 연장 벨트": "팔꿈치 마사지기 연장벨트",
    "눈편한케어 무선 온열 안대 진동 눈 마사지기, 1개, SL24EQ08": "눈편한케어",
    "슬룸 목베개 플러스, 그레이, 1개, SL24EQ09": "목베개 플러스",
    "슬룸 허리편한케어 V2 마사지기, 네이비, 1개, SL25EQ01": "허리편한케어 V2",
    "슬룸 골반 마사지기 안마기 의자 엉덩이 고관절 허리 진동 온열 에어백": "골반 마사지기",
    "슬룸 허리 마사지기 스트레칭 온열 3단 조절 마사지 허리베개 프로": "허리베개 프로",
    "슬룸 발편한케어 EMS 저주파 발 발바닥 마사지기 안마기 족저근막염": "발편한케어 V1",
}


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


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
        cur = rv.get("product")
        new = RENAME_MAP.get(cur)
        if new and new != cur:
            renames[rid] = (cur, new)

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
    eprint("  reviews.json 저장 완료")

    # recompute_products는 이미 products.json에 있는 상품만 갱신하므로, 타깃 캐노니컬이
    # 아직 상품 목록에 없으면(예: 이전 단계에서 이름이 완전히 바뀌어 사라진 경우) 빈 스텁을
    # 먼저 추가해야 재계산에서 채워진다.
    ppath = DATA_ROOT / MONTH / "products.json"
    pdata = json.loads(ppath.read_text(encoding="utf-8"))
    existing_names = {p["name"] for p in pdata["products"]}
    new_targets = {new for (_old, new) in renames.values()} - existing_names
    for name in sorted(new_targets):
        pdata["products"].append({
            "id": None, "name": name, "raw_name": name, "price": None,
            "review_count": 0, "avg_rating": 0.0,
            "rating_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
            "photo_count": 0, "sentiment": {"positive": 0, "neutral": 0, "negative": 0},
            "positive_rate": 0.0, "negative_rate": 0.0,
            "prev_review_count": None, "prev_avg_rating": None,
            "top_reviews": [], "bottom_reviews": [],
        })
        eprint(f"  [신규 상품 스텁 추가] {name}")
    ppath.write_text(json.dumps(pdata, ensure_ascii=False, indent=2), encoding="utf-8")

    recompute_products(MONTH, set())
    eprint("  products.json 재계산 완료")

    ppath = DATA_ROOT / MONTH / "products.json"
    pdata = json.loads(ppath.read_text(encoding="utf-8"))
    before_n = len(pdata["products"])
    pdata["products"] = [p for p in pdata["products"] if p.get("review_count", 0) > 0]
    after_n = len(pdata["products"])
    ppath.write_text(json.dumps(pdata, ensure_ascii=False, indent=2), encoding="utf-8")
    if before_n != after_n:
        eprint(f"  0건 된 상품 스텁 {before_n - after_n}개 정리 (상품 {before_n} -> {after_n}개)")

    recompute_summary(MONTH)
    eprint("  summary.json 재계산 완료")
    recompute_keywords(MONTH, set())
    eprint("  keywords.json 재계산 완료")
    eprint("\n[DONE]")


if __name__ == "__main__":
    main()
