"""consolidate_product.py — 상품명 통합(재라벨링): 이미 병합된 산출물에서 상품명 A를 B로 합친다
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
같은 상품이 이름이 달라 분리된 경우(예: 쿠팡 '목베개플러스' vs 자사몰 '목베개 플러스')를
docs/data/{brand}/{month}/ 산출물에서 하나로 합친다. 감성값은 건드리지 않고 이름/집계만 재구성.

  · reviews.json : 각 리뷰의 product(단일) + products(복수) 에서 FROM→TO 치환(중복 제거)
  · products.json: FROM 상품 엔트리를 TO 로 병합(리뷰수·감성·별점분포·포토 합산, 평균 가중,
                   긍/부율 재계산, 대표리뷰 합침). TO 없으면 FROM 을 TO 로 이름만 변경.
  · keywords.json: 각 키워드 by_product 에서 FROM→TO 치환·카운트 합산

사용:
  python scripts/consolidate_product.py --brand 슬룸 --months 2026-03,2026-04,2026-05,2026-06,2026-07 \
    --map "목마사지베개 V2=>목 마사지 베개 V2" --map "목베개플러스=>목베개 플러스"
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SENTS = ("positive", "neutral", "negative")


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save(o, p):
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")


def apply_reviews(reviews, mapping):
    n = 0
    for v in reviews.values():
        pn = v.get("product")
        if pn in mapping:
            v["product"] = mapping[pn]; n += 1
        if isinstance(v.get("products"), list):
            seen, new = set(), []
            for x in v["products"]:
                y = mapping.get(x, x)
                if y not in seen:
                    seen.add(y); new.append(y)
            v["products"] = new
    return n


def merge_product_entries(plist, mapping):
    by_name = {p.get("name"): p for p in plist}
    for src, dst in mapping.items():
        sp = by_name.get(src)
        if not sp:
            continue
        dp = by_name.get(dst)
        if dp is None:
            sp["name"] = dst
            by_name[dst] = sp
            del by_name[src]
            continue
        # 합산
        srev, drev = sp.get("review_count", 0), dp.get("review_count", 0)
        tot = srev + drev
        ss = sp.get("sentiment", {}) or {}
        ds = dp.get("sentiment", {}) or {}
        dp["sentiment"] = {x: ds.get(x, 0) + ss.get(x, 0) for x in SENTS}
        srd = sp.get("rating_distribution", {}) or {}
        drd = dp.get("rating_distribution", {}) or {}
        dp["rating_distribution"] = {str(i): drd.get(str(i), 0) + srd.get(str(i), 0) for i in range(1, 6)}
        if tot:
            dp["avg_rating"] = round((dp.get("avg_rating", 0) * drev + sp.get("avg_rating", 0) * srev) / tot, 2)
            dp["positive_rate"] = round(dp["sentiment"]["positive"] / tot * 100, 2)
            dp["negative_rate"] = round(dp["sentiment"]["negative"] / tot * 100, 2)
        dp["review_count"] = tot
        dp["photo_count"] = dp.get("photo_count", 0) + sp.get("photo_count", 0)
        for fld in ("top_reviews", "bottom_reviews"):
            if isinstance(dp.get(fld), list) and isinstance(sp.get(fld), list):
                dp[fld] = (dp[fld] + sp[fld])[:5]
        del by_name[src]
    # 원래 순서 유지 어렵지 않게 리뷰수 순 재정렬
    return sorted(by_name.values(), key=lambda x: x.get("review_count", 0), reverse=True)


def apply_keywords(kdoc, mapping):
    def fix(lst):
        for k in lst or []:
            bp = k.get("by_product")
            if not isinstance(bp, list):
                continue
            acc = {}
            for d in bp:
                nm = mapping.get(d.get("product"), d.get("product"))
                acc[nm] = acc.get(nm, 0) + d.get("count", 0)
            k["by_product"] = sorted(({"product": p, "count": c} for p, c in acc.items()),
                                     key=lambda x: x["count"], reverse=True)
    for bucket in ("praise", "complaint", "improvement"):
        fix(kdoc.get("by_intent", {}).get(bucket, []))
    for key in ("negative_keywords", "positive_keywords", "low_rating_keywords"):
        fix(kdoc.get(key, []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="슬룸")
    ap.add_argument("--months", required=True)
    ap.add_argument("--map", action="append", default=[], help='"FROM=>TO" (여러 개 가능)')
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    mapping = {}
    for pair in args.map:
        if "=>" not in pair:
            eprint(f"[ERROR] --map 형식은 'FROM=>TO': {pair}"); sys.exit(1)
        a, b = pair.split("=>", 1)
        mapping[a.strip()] = b.strip()
    if not mapping:
        # --map 없으면 config 파일(scripts/product_consolidate.json)에서 읽는다
        cfg = ROOT / "scripts" / "product_consolidate.json"
        if cfg.is_file():
            mapping = load(cfg).get("map", {})
            eprint(f"(--map 미지정 → {cfg.name} 의 매핑 {len(mapping)}건 사용)")
    if not mapping:
        eprint("[ERROR] --map 또는 scripts/product_consolidate.json 필요"); sys.exit(1)
    eprint("통합 매핑:")
    for a, b in mapping.items():
        eprint(f"  {a!r} → {b!r}")

    for m in [x.strip() for x in args.months.split(",") if x.strip()]:
        d = ROOT / "docs" / "data" / args.brand / m
        rp = d / "reviews.json"
        if not rp.is_file():
            eprint(f"  [SKIP] {m}: reviews.json 없음"); continue
        if not args.no_backup:
            for f in ("reviews.json", "products.json", "keywords.json"):
                p = d / f
                if p.is_file():
                    shutil.copy2(p, p.with_suffix(".json.bak"))
        rdoc = load(rp)
        n = apply_reviews(rdoc.get("reviews", {}), mapping)
        save(rdoc, rp)
        if (d / "products.json").is_file():
            pdoc = load(d / "products.json")
            plist = pdoc.get("products") if isinstance(pdoc, dict) else pdoc
            merged = merge_product_entries(plist, mapping)
            if isinstance(pdoc, dict):
                pdoc["products"] = merged
            else:
                pdoc = merged
            save(pdoc, d / "products.json")
        if (d / "keywords.json").is_file():
            kdoc = load(d / "keywords.json")
            apply_keywords(kdoc, mapping)
            save(kdoc, d / "keywords.json")
        eprint(f"  [OK] {m}: 리뷰 {n}건 상품명 치환 + 상품/키워드 집계 병합")
    eprint("완료.")


if __name__ == "__main__":
    main()
