"""patch_smartstore_product_names.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
스마트스토어 리뷰(review_id가 ss_ 접두사)의 상품명은 네이버 특유의 긴 마케팅
제목 그대로 들어와서(옵션 필드가 없어 크리마 프로모션 문구 제거 로직이 안 먹힘),
merge_smartstore.py 로 슬룸에 얹으면 기존 21개 캐노니컬 상품이 수십 개로
쪼개지는 문제가 있었다(2026-09 확인). 키워드 매칭으로 원래 상품에 되돌린다.

동시에 자사몰 쪽에서도 같은 이유로 안 뭉쳐졌던 "목베개"(플러스/V2 표기 없이
그냥 목베개) 라벨도 함께 정리한다(사용자 확인: "목베개"로 별도 분류).

사용자 확정 규칙(2026-09-07):
  1. 버전 표기 없는 "발편한케어" → V1로 고정
  2. 버전/수식어 없는 "목베개" → 별도 캐노니컬 "목베개"(플러스와 다른 취급)
  3. "마그네슘 시너지 크림"(사은품) 리뷰 → 분석에서 완전 제외
  4. "하루끝차" → 신규 캐노니컬 "하루끝차"로 유지
  5. "목베개 커버" → 액세서리로 별도 유지(그대로 둠, 병합 안 함)

사용:
  python scripts/patch_smartstore_product_names.py --dry-run
  python scripts/patch_smartstore_product_names.py --apply
"""
import argparse, json, re, sys
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


# (정규식, 캐노니컬명) — 위에서부터 먼저 매칭되는 것을 채택(구체적인 것 먼저)
RULES = [
    (re.compile(r"목\s*마사지\s*베개\s*V2|목마사지베개\s*V2"), "목 마사지 베개 V2"),
    (re.compile(r"목베개플러스|목베개\s*플러스"), "목베개 플러스"),
    (re.compile(r"팔꿈치\s*마사지기|엘보케어"), "프리미엄 엘보케어 팔꿈치 마사지기"),
    (re.compile(r"허리편한케어\s*V2|허리편한케어V2"), "허리편한케어 V2"),
    (re.compile(r"허리편한케어\s*V1|허리편한케어V1"), "허리편한케어 V1"),  # 케이블은 아래서 먼저 걸러짐
    (re.compile(r"전용\s*케이블|충전\s*케이블"), "케이블"),
    (re.compile(r"USB.*충전.*어댑터|충전기"), "어댑터"),
    (re.compile(r"허리베개\s*프로"), "허리베개 프로"),
    (re.compile(r"허리베개"), "허리베개"),
    (re.compile(r"손편한케어"), "손편한케어"),
    (re.compile(r"넥숄더\s*프로"), "넥숄더 프로"),
    (re.compile(r"넥숄더\s*힐링케어\s*V2|넥숄더힐링케어\s*V2"), "넥숄더 힐링케어 V2"),
    (re.compile(r"넥숄더\s*힐링케어"), "넥숄더 힐링케어"),
    (re.compile(r"목편한케어\s*플라잉"), "목편한케어 플라잉"),
    (re.compile(r"목편한케어"), "목편한케어"),
    (re.compile(r"종아리편한케어"), "종아리편한케어"),
    (re.compile(r"코어\s*요추\s*벨트"), "코어 요추벨트"),
    (re.compile(r"골반.*마사지기|골반케어"), "골반 마사지기"),
    (re.compile(r"바디\s*풀고컷|바디풀고컷"), "바디 풀고컷"),
    (re.compile(r"눈편한케어"), "눈편한케어"),
    (re.compile(r"발편한케어"), "발편한케어 V1"),          # 사용자 결정 1: 버전 없으면 V1
    (re.compile(r"하루끝차"), "하루끝차"),                  # 사용자 결정 4: 신규 캐노니컬
    (re.compile(r"목베개\s*커버"), "목베개 커버"),          # 사용자 결정 5: 액세서리 유지(그대로 분리)
]

# 케이블/전용케이블 규칙이 허리편한케어V1 규칙보다 뒤에 있으면 "허리편한케어 V1 전용 케이블"이
# 먼저 "허리편한케어 V1"로 잘못 매칭되므로, 실제 순서는 케이블 규칙을 먼저 시도한다.
RULES_ORDERED = [
    (re.compile(r"전용\s*케이블|충전\s*케이블"), "케이블"),
    (re.compile(r"USB.*충전.*어댑터|충전기"), "어댑터"),
    (re.compile(r"목베개\s*커버"), "목베개 커버"),
    (re.compile(r"목베개플러스|목베개\s*플러스"), "목베개 플러스"),
    (re.compile(r"목\s*마사지\s*베개\s*V2|목마사지베개\s*V2"), "목 마사지 베개 V2"),
    (re.compile(r"팔꿈치\s*마사지기(?!\s*연장벨트)|엘보케어"), "프리미엄 엘보케어 팔꿈치 마사지기"),
    (re.compile(r"허리편한케어\s*V2|허리편한케어V2"), "허리편한케어 V2"),
    (re.compile(r"허리편한케어\s*V1|허리편한케어V1"), "허리편한케어 V1"),
    (re.compile(r"허리베개\s*프로"), "허리베개 프로"),
    (re.compile(r"허리베개"), "허리베개"),
    (re.compile(r"손편한케어"), "손편한케어"),
    (re.compile(r"넥숄더\s*프로"), "넥숄더 프로"),
    (re.compile(r"넥숄더\s*힐링케어\s*V2|넥숄더힐링케어\s*V2"), "넥숄더 힐링케어 V2"),
    (re.compile(r"넥숄더\s*힐링케어"), "넥숄더 힐링케어"),
    (re.compile(r"목편한케어\s*플라잉"), "목편한케어 플라잉"),
    (re.compile(r"목편한케어"), "목편한케어"),
    (re.compile(r"종아리편한케어"), "종아리편한케어"),
    (re.compile(r"코어\s*요추\s*벨트"), "코어 요추벨트"),
    (re.compile(r"골반.*마사지기|골반케어"), "골반 마사지기"),
    (re.compile(r"바디\s*풀고컷|바디풀고컷"), "바디 풀고컷"),
    (re.compile(r"눈편한케어"), "눈편한케어"),
    (re.compile(r"발편한케어"), "발편한케어 V1"),
    (re.compile(r"하루끝차"), "하루끝차"),
    (re.compile(r"목베개"), "목베개"),                       # bare 목베개(위 규칙들에 안 걸린 나머지)
]

EXCLUDE_RE = re.compile(r"마그네슘\s*시너지\s*크림")

# 자사몰 쪽에서도 같은 "목베개"(스트레칭 베개 프로모 문구) 케이스가 있어 함께 정리
LEGACY_RAW_NAMES_TO_MOKBEGAE = {
    "[썸머 수퍼데이] 목베개 스트레칭 베개",
    "목베개 스트레칭 베개",
}


def resolve(name: str):
    if EXCLUDE_RE.search(name):
        return "__EXCLUDE__"
    for pat, canon in RULES_ORDERED:
        if pat.search(name):
            return canon
    return None  # 매칭 안 됨 — 그대로 둠


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rpath = DATA_ROOT / MONTH / "reviews.json"
    rdata = json.loads(rpath.read_text(encoding="utf-8"))
    reviews = rdata["reviews"]

    renames, excludes, unmatched = {}, [], set()
    for rid, rv in reviews.items():
        name = rv.get("product") or ""
        is_ss = rid.startswith("ss_")
        if is_ss:
            canon = resolve(name)
            if canon == "__EXCLUDE__":
                excludes.append(rid)
            elif canon and canon != name:
                renames[rid] = (name, canon)
            elif canon is None:
                unmatched.add(name)
        elif name in LEGACY_RAW_NAMES_TO_MOKBEGAE:
            renames[rid] = (name, "목베개")

    eprint(f"[{MONTH}] SS 리뷰 상품명 재매핑 대상: {len(renames)}건 / 제외 대상: {len(excludes)}건")
    by_target = {}
    for rid, (old, new) in renames.items():
        by_target.setdefault(new, 0)
        by_target[new] += 1
    for t, n in sorted(by_target.items(), key=lambda x: -x[1]):
        eprint(f"  -> {t}: {n}건")
    if unmatched:
        eprint(f"  [매칭 안 됨, 원본 유지] {len(unmatched)}개 상품명:")
        for n in sorted(unmatched):
            eprint(f"     · {n}")
    if excludes:
        eprint(f"  [제외될 리뷰] {excludes}")

    if args.dry_run:
        eprint("\n[dry-run] 실제 변경 없음.")
        return

    for p in (rpath, DATA_ROOT / MONTH / "products.json", DATA_ROOT / MONTH / "summary.json",
              DATA_ROOT / MONTH / "keywords.json"):
        if p.is_file():
            backup(p)

    for rid, (old, new) in renames.items():
        reviews[rid]["product"] = new
    for rid in excludes:
        reviews.pop(rid, None)
    rdata["count"] = len(reviews)
    rpath.write_text(json.dumps(rdata, ensure_ascii=False, indent=2), encoding="utf-8")
    eprint(f"  reviews.json 저장 완료 (제외 {len(excludes)}건 반영, 총 {rdata['count']}건)")

    # recompute_products는 이미 products.json에 있는 상품만 재계산하므로,
    # 이번에 새로 생긴 캐노니컬명(하루끝차/목베개/목베개 커버 등)은 빈 스텁으로 먼저 추가해야
    # 재계산 루프에 걸려 정상적으로 채워진다.
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

    affected = recompute_products(MONTH, set(excludes))
    eprint(f"  products.json 재계산 완료")

    # 리뷰가 전부 다른 이름으로 옮겨가서 0건이 된 옛 프로모션 원본명 스텁은 정리
    pdata2 = json.loads(ppath.read_text(encoding="utf-8"))
    before_n = len(pdata2["products"])
    pdata2["products"] = [p for p in pdata2["products"] if p.get("review_count", 0) > 0]
    after_n = len(pdata2["products"])
    ppath.write_text(json.dumps(pdata2, ensure_ascii=False, indent=2), encoding="utf-8")
    if before_n != after_n:
        eprint(f"  0건 된 상품 스텁 {before_n - after_n}개 정리 (상품 {before_n} -> {after_n}개)")

    recompute_summary(MONTH)
    eprint(f"  summary.json 재계산 완료")
    recompute_keywords(MONTH, set(excludes))
    eprint(f"  keywords.json 재계산 완료")
    eprint("\n[DONE]")


if __name__ == "__main__":
    main()
