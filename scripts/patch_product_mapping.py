"""patch_product_mapping.py — 옵션 기반 유의어 매핑으로 기존 산출물 재라벨링
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
product_mapping.ProductResolver 로 raw CSV(상품명+옵션)에서 상품 멤버십을 재도출해
기존 docs/data/{brand}/{month}/ 의 산출물을 갱신한다.

  · reviews.json : 각 리뷰에 products(복수 귀속) + is_set(세트리뷰 표시) 추가.
                   사은품 전용 리뷰(멤버십 []) 는 제거. product(단일)=products[0] 유지.
  · products.json: 새 멤버십으로 상품별 집계 재계산(다중 귀속 → 리뷰 1건이 여러 상품에 반영).
  · keywords.json: all_review_ids/reviews 에서 제거된 사은품 리뷰 필터 + by_product/count 재계산.

★ AI 감성 판정(reviews[].sentiment)은 건드리지 않는다 — 라벨/집계만 재구성.
★ 기본은 dry-run(요약만 출력). 실제 반영은 --apply. 반영 전 .bak 백업(--no-backup 로 생략).

사용:
    python scripts/patch_product_mapping.py --brand 슬룸 --months 2026-03,2026-04,2026-05
    python scripts/patch_product_mapping.py --brand 슬룸 --months 2026-03,2026-04,2026-05 --apply
"""
import argparse
import csv
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from product_mapping import ProductResolver  # noqa: E402


def _prev_month(m):
    """'2026-01' → '2025-12'."""
    try:
        y, mo = [int(x) for x in str(m).split("-")[:2]]
    except Exception:
        return None
    mo -= 1
    if mo == 0:
        y, mo = y - 1, 12
    return f"{y:04d}-{mo:02d}"


def _load_prev_counts(brand, month):
    """이전월의 (이미 패치된) products.json → {name:{rc,avg}}. 없으면 {}."""
    pm = _prev_month(month)
    if not pm:
        return {}
    pp = ROOT / "docs" / "data" / brand / pm / "products.json"
    if not pp.is_file():
        return {}
    try:
        arr = json.loads(pp.read_text(encoding="utf-8")).get("products", [])
    except Exception:
        return {}
    return {p["name"]: {"rc": p.get("review_count", 0), "avg": p.get("avg_rating")}
            for p in arr if p.get("name")}


def _col(r, *names):
    for n in names:
        v = r.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def load_raw_map(brand, month, resolver):
    """raw CSV → {review_id: {products, is_set, photo, price, pid, raw_name}}"""
    p = ROOT / f"data/raw/{brand}/{month}/reviews.csv"
    if not p.is_file():
        raise SystemExit(f"[ERROR] raw CSV 없음: {p}")
    out = {}
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    for r in rows:
        rid = _col(r, "리뷰ID", "리뷰번호")
        if not rid:
            continue
        name = _col(r, "상품명")
        opt = _col(r, "상품옵션")
        prods, is_set = resolver.resolve_ex(name, opt)
        photo = any(_col(r, f"사진{i}_url") for i in range(1, 5))
        out[rid] = {
            "products": prods,
            "is_set": is_set,
            "photo": photo,
            "price": _col(r, "상품가격"),
            "pid": _col(r, "상품번호"),
            "raw_name": name,
        }
    return out, len(rows)


def rebuild_products(reviews, raw_map, prev_counts=None):
    """멤버십 기반 상품별 집계 재계산. reviews = {rid: rv(products 포함)}."""
    agg = defaultdict(lambda: {
        "rc": 0, "rsum": 0.0, "rdist": Counter(), "photo": 0,
        "sent": Counter(), "revs": [], "pid": Counter(), "price": Counter(),
        "raw": Counter(),
    })
    for rid, rv in reviews.items():
        rating = rv.get("rating") or 0
        sent = rv.get("sentiment", "neutral")
        rm = raw_map.get(rid, {})
        for pn in rv.get("products", []):
            a = agg[pn]
            a["rc"] += 1
            a["rsum"] += rating
            a["rdist"][str(int(rating))] += 1 if rating else 0
            if rm.get("photo"):
                a["photo"] += 1
            a["sent"][sent] += 1
            a["revs"].append(rid)
            if rm.get("pid"):
                a["pid"][rm["pid"]] += 1
            if rm.get("price"):
                a["price"][rm["price"]] += 1
            if rm.get("raw_name"):
                a["raw"][rm["raw_name"]] += 1

    prev_counts = prev_counts or {}
    products = []
    for pn, a in sorted(agg.items(), key=lambda x: -x[1]["rc"]):
        rc = a["rc"]
        pos = a["sent"].get("positive", 0)
        neg = a["sent"].get("negative", 0)
        neu = a["sent"].get("neutral", 0)
        rdist = {str(s): a["rdist"].get(str(s), 0) for s in range(1, 6)}
        # 대표 메타(가장 흔한 raw 상품번호/가격/원본명)
        pid = a["pid"].most_common(1)[0][0] if a["pid"] else ""
        price_s = a["price"].most_common(1)[0][0] if a["price"] else "0"
        try:
            price = int(float(str(price_s).replace(",", "")))
        except Exception:
            price = 0
        raw_name = a["raw"].most_common(1)[0][0] if a["raw"] else pn
        # top/bottom 리뷰 (멤버 리뷰만)
        member = [reviews[r] for r in a["revs"]]

        def _rv_obj(rv):
            return {"review_id": rv.get("_id", ""), "text": str(rv.get("text", ""))[:400],
                    "rating": rv.get("rating", 0), "date": rv.get("date", "")}
        top = sorted(member, key=lambda v: (-(v.get("rating") or 0), -len(str(v.get("text", "")))))[:5]
        bot = sorted(member, key=lambda v: ((v.get("rating") or 0), -len(str(v.get("text", "")))))[:5]
        prev = prev_counts.get(pn, {})
        products.append({
            "id": pid,
            "name": pn,
            "raw_name": raw_name,
            "price": price,
            "review_count": rc,
            "avg_rating": round(a["rsum"] / rc, 2) if rc else 0,
            "rating_distribution": rdist,
            "photo_count": a["photo"],
            "sentiment": {"positive": pos, "neutral": neu, "negative": neg},
            "positive_rate": round(pos / rc * 100, 2) if rc else 0,
            "negative_rate": round(neg / rc * 100, 2) if rc else 0,
            "prev_review_count": prev.get("rc", 0),
            "prev_avg_rating": prev.get("avg", None),
            "top_reviews": [_rv_obj(v) for v in top],
            "bottom_reviews": [_rv_obj(v) for v in bot],
        })
    counts = {p["name"]: {"rc": p["review_count"], "avg": p["avg_rating"]} for p in products}
    return {"products": products}, counts


def rebuild_keywords(kw, reviews):
    """제거된 리뷰 필터 + by_product/count 재계산 (products 멤버십 기반, 다중귀속)."""
    alive = set(reviews.keys())

    def prod_of(rid):
        return reviews.get(rid, {}).get("products", [])

    # 단순 배열들 (word,count,reviews)
    for arr_name in ("negative_keywords", "positive_keywords", "low_rating_keywords"):
        for it in kw.get(arr_name, []):
            ids = [x for x in it.get("reviews", []) if x in alive]
            it["reviews"] = ids
            it["count"] = len(ids)

    # by_intent 상세
    for key, arr in kw.get("by_intent", {}).items():
        for it in arr:
            ids = [x for x in it.get("all_review_ids", []) if x in alive]
            it["all_review_ids"] = ids
            it["count"] = len(ids)
            if "reviews" in it:
                it["reviews"] = [x for x in it["reviews"] if x in alive]
            # by_product 재계산 (멤버십 다중귀속)
            bp = Counter()
            for rid in ids:
                for pn in prod_of(rid):
                    bp[pn] += 1
            it["by_product"] = [{"product": p, "count": c}
                                for p, c in sorted(bp.items(), key=lambda x: -x[1])]
            # review_samples product 갱신(대표=첫 상품), 죽은 리뷰 제거
            new_samples = []
            for s in it.get("review_samples", []):
                rid = str(s.get("review_id", ""))
                if rid not in alive:
                    continue
                pl = prod_of(rid)
                if pl:
                    s["product"] = pl[0]
                new_samples.append(s)
            it["review_samples"] = new_samples
    return kw


def rebuild_candidates(cand, alive):
    """keyword_candidates.json 의 review_ids/samples 에서 제거된 사은품 리뷰 필터."""
    for it in cand.get("candidates", []):
        ids = [x for x in it.get("review_ids", []) if str(x) in alive]
        it["review_ids"] = ids
        it["count"] = len(ids)
        if "samples" in it:
            it["samples"] = [s for s in it["samples"]
                             if str(s.get("review_id", s) if isinstance(s, dict) else s) in alive]
    return cand


def patch_month(brand, month, resolver, prev_counts, apply, backup):
    ddir = ROOT / "docs" / "data" / brand / month
    rpath, ppath, kpath = ddir / "reviews.json", ddir / "products.json", ddir / "keywords.json"
    cpath = ddir / "keyword_candidates.json"
    for f in (rpath, ppath, kpath):
        if not f.is_file():
            print(f"  [SKIP] {month}: {f.name} 없음")
            return prev_counts

    # 단일 월 실행(자동화)이면 이전월 패치본에서 prev(MoM 비교) 시드
    if not prev_counts:
        prev_counts = _load_prev_counts(brand, month)

    raw_map, raw_n = load_raw_map(brand, month, resolver)
    rjson = json.loads(rpath.read_text(encoding="utf-8"))
    reviews = rjson.get("reviews", {})

    dropped, set_rev, multi = 0, 0, 0
    new_reviews = {}
    for rid, rv in reviews.items():
        rm = raw_map.get(rid)
        prods = rm["products"] if rm else [rv.get("product", "")]
        prods = [p for p in prods if p]     # 빈 상품명("") 제거 — raw 미존재 엣지에서 [""] 집계오염 방지
        is_set = bool(rm and rm["is_set"])
        if not prods:
            dropped += 1
            continue
        rv["_id"] = rid
        rv["products"] = prods
        rv["is_set"] = is_set
        rv["product"] = prods[0]          # 하위호환(단일)
        if is_set:
            set_rev += 1
        if len(prods) > 1:
            multi += 1
        new_reviews[rid] = rv

    # for 집계에서 _id 필요 → 유지, 저장 직전 제거
    prods_json, counts = rebuild_products(new_reviews, raw_map, prev_counts)
    kw = json.loads(kpath.read_text(encoding="utf-8"))
    kw = rebuild_keywords(kw, new_reviews)
    cand = None
    if cpath.is_file():
        cand = rebuild_candidates(json.loads(cpath.read_text(encoding="utf-8")), set(new_reviews.keys()))

    print(f"\n[{month}] raw {raw_n} · json리뷰 {len(reviews)} → 잔존 {len(new_reviews)} "
          f"(사은품제외 {dropped} · 세트리뷰 {set_rev} · 다중귀속 {multi}) · 상품 {len(prods_json['products'])}종")
    for p in prods_json["products"][:14]:
        print(f"    {p['review_count']:5}  {p['name']}  (평점 {p['avg_rating']}, 긍정 {p['positive_rate']}%)")

    if apply:
        bak_targets = [rpath, ppath, kpath] + ([cpath] if cand is not None else [])
        if backup:
            for f in bak_targets:
                shutil.copy2(f, f.with_suffix(f.suffix + ".bak"))
        for rv in new_reviews.values():
            rv.pop("_id", None)
        rjson["reviews"] = new_reviews
        rjson["count"] = len(new_reviews)
        rpath.write_text(json.dumps(rjson, ensure_ascii=False, indent=2), encoding="utf-8")
        ppath.write_text(json.dumps(prods_json, ensure_ascii=False, indent=2), encoding="utf-8")
        kpath.write_text(json.dumps(kw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if cand is not None:
            cpath.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    [APPLIED] {month} 반영 완료" + (" (.bak 백업)" if backup else ""))
    return counts


def main():
    ap = argparse.ArgumentParser(description="옵션 기반 매핑으로 기존 산출물 재라벨링")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--months", required=True, help="쉼표구분 (앞→뒤 순서로 prev 연결)")
    ap.add_argument("--apply", action="store_true", help="실제 파일 반영(미지정=dry-run)")
    ap.add_argument("--no-backup", action="store_true", help="반영 시 .bak 백업 생략")
    args = ap.parse_args()
    resolver = ProductResolver()
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    print(f"{'[APPLY]' if args.apply else '[DRY-RUN]'} 매핑 재라벨링: {args.brand} {months}")
    prev = {}
    for mo in months:
        prev = patch_month(args.brand, mo, resolver, prev, args.apply, not args.no_backup)
    print("\n[완료]" + ("" if args.apply else "  (dry-run — 실제 반영은 --apply)"))


if __name__ == "__main__":
    main()
