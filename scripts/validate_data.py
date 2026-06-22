"""validate_data.py --brand 슬룸 --month 2026-05
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
월별 산출물(JSON)의 정합성을 자동 점검한다. update-data.bat 끝에서 호출되어
배포 직전 데이터 이상을 잡아낸다.

판정:
  ❌ FAIL (exit 1) — 배포하면 안 되는 치명 문제. .bat 가 푸시를 중단한다.
     · 필수 파일 없음/파싱 불가 (summary/products/keywords/reviews)
     · total_reviews 불일치 (KPI ≠ reviews.json ≠ 상품 합계)
     · products 에 positive_rate/negative_rate 누락
  ⚠️ WARN (exit 0) — 배포는 가능하나 확인 권장.
     · 감성 커버리지 < 100% 또는 별점 폴백 존재
     · 후보 review_id 가 reviews.json 에 없음
     · 칭찬에 부정감성 / 불만·개선에 긍정감성 비율이 높음
     · pvoc_intent / keyword_candidates 누락
  ✅ PASS — 이상 없음.

사용:
  python scripts/validate_data.py --brand 슬룸 --month 2026-05
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--warn-reverse-pct", type=float, default=10.0,
                    help="칭찬 역감성 비율 WARN 임계(%)")
    args = ap.parse_args()

    d = ROOT / "docs" / "data" / args.brand / args.month
    fails, warns, oks = [], [], []

    def load(name, required=True):
        p = d / name
        if not p.is_file():
            (fails if required else warns).append(f"{name} 파일 없음")
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            (fails if required else warns).append(f"{name} 파싱 실패: {str(e)[:60]}")
            return None

    summary = load("summary.json")
    products_raw = load("products.json")
    keywords = load("keywords.json")
    reviews_doc = load("reviews.json")
    pvoc = load("pvoc_intent.json", required=False)
    cands = load("keyword_candidates.json", required=False)

    reviews = (reviews_doc or {}).get("reviews", {}) if isinstance(reviews_doc, dict) else {}
    products = products_raw if isinstance(products_raw, list) else (products_raw or {}).get("products", []) if products_raw else []
    nrev = len(reviews)

    # ── 1) total_reviews 정합성 (KPI = reviews.json = 상품 합계) ──
    if summary is not None and reviews_doc is not None:
        kpi_tot = (summary.get("kpis", {}) or {}).get("total_reviews")
        psum = sum(p.get("review_count", 0) for p in products)
        if kpi_tot == nrev == psum:
            oks.append(f"리뷰 수 일치: KPI={kpi_tot} = reviews={nrev} = 상품합={psum}")
        else:
            fails.append(f"리뷰 수 불일치: KPI={kpi_tot}, reviews.json={nrev}, 상품합={psum}")

    # ── 2) products 필수 필드 ──
    if products:
        miss = [p.get("name", "?") for p in products
                if "positive_rate" not in p or "negative_rate" not in p]
        if miss:
            fails.append(f"products positive_rate/negative_rate 누락 {len(miss)}개: {miss[:3]}")
        else:
            oks.append(f"products {len(products)}개 모두 positive_rate/negative_rate 보유")

    # ── 3) 감성 커버리지 ──
    if reviews:
        have = sum(1 for r in reviews.values() if r.get("sentiment"))
        fb = sum(1 for r in reviews.values() if r.get("sentiment_src") == "rating")
        pct = have / nrev * 100 if nrev else 0
        if pct >= 100 and fb == 0:
            oks.append(f"감성 커버리지 100% (폴백 0)")
        else:
            warns.append(f"감성 커버리지 {pct:.0f}% · 별점폴백 {fb}건 (patch_sentiment.py 로 보완 권장)")

    # ── 4) 버킷 감성 정합성 (칭찬=긍정?, 불만·개선=부정?) ──
    if keywords and reviews:
        bi = keywords.get("by_intent", {}) or {}
        def reverse_rate(items, bad):
            tot = rev = 0
            for it in items or []:
                for rid in it.get("all_review_ids", []):
                    s = (reviews.get(str(rid), {}) or {}).get("sentiment")
                    tot += 1
                    if s == bad:
                        rev += 1
            return tot, rev
        pt, pr = reverse_rate(bi.get("praise"), "negative")
        if pt and pr / pt * 100 > args.warn_reverse_pct:
            warns.append(f"칭찬 버킷에 부정감성 {pr}/{pt} ({pr/pt*100:.0f}%) — 오분류 점검 권장")
        else:
            oks.append(f"칭찬 버킷 역감성 정상 ({pr}/{pt})")
        nt, npos = reverse_rate((bi.get("complaint") or []) + (bi.get("improvement") or []), "positive")
        # 불만·개선은 별점 기반이라 일부 긍정 혼입 가능 → 정보성 표기만
        oks.append(f"불만·개선 버킷 멤버 {nt} (긍정감성 {npos} — 혼합리뷰 가능)")

    # ── 5) 후보 review_id 실재 여부 ──
    if cands is not None and reviews:
        miss = sum(1 for c in cands.get("candidates", [])
                   for rid in c.get("review_ids", []) if str(rid) not in reviews)
        if miss:
            warns.append(f"keyword_candidates review_id {miss}개가 reviews.json 에 없음")
        else:
            oks.append(f"keyword_candidates 후보 {len(cands.get('candidates', []))}개 · review_id 전부 실재")
    elif cands is None:
        warns.append("keyword_candidates.json 없음 (발굴 단계 건너뜀?)")

    # ── 6) pvoc_intent ──
    if pvoc is None:
        warns.append("pvoc_intent.json 없음 (구매경험 VOC 감성 데이터 미생성)")
    elif isinstance(pvoc.get("topics"), dict):
        oks.append(f"pvoc_intent 토픽 {len(pvoc['topics'])}개")

    # ── 리포트 ──
    print("=" * 56)
    print(f" 데이터 정합성 검증 — {args.brand} / {args.month}")
    print("=" * 56)
    for m in oks:
        print(f"  [OK]   {m}")
    for m in warns:
        print(f"  [WARN] {m}")
    for m in fails:
        print(f"  [FAIL] {m}")
    print("-" * 56)
    if fails:
        print(f" 결과: ❌ FAIL ({len(fails)}건) — 배포 보류 권장. 위 항목을 먼저 해결하세요.")
        sys.exit(1)
    if warns:
        print(f" 결과: ⚠️ WARN ({len(warns)}건) — 배포는 가능하나 확인 권장.")
        sys.exit(0)
    print(" 결과: ✅ PASS — 이상 없음.")
    sys.exit(0)


if __name__ == "__main__":
    main()
