"""merge_neck_v2_naming.py — '넥숄더 힐링케어V2'(공백없음) -> '넥숄더 힐링케어 V2'(공백있음) 통일

product_mapping.json 은 이미 갱신됨(canonical/aliases/rules). 여기서는 이미 생성된
5개월 reviews.json 의 review['products']/['product'] 값을 리네임하고 products/keywords/
keyword_candidates 를 재계산한다.
"""
import csv
import json
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from patch_product_mapping import rebuild_products, rebuild_keywords, rebuild_candidates, _col  # noqa: E402

OLD = "넥숄더 힐링케어V2"
NEW = "넥숄더 힐링케어 V2"


def main():
    apply_ = "--apply" in sys.argv
    prev = {}
    for month in ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]:
        ddir = ROOT / "docs" / "data" / "슬룸" / month
        rpath, ppath, kpath = ddir / "reviews.json", ddir / "products.json", ddir / "keywords.json"
        cpath = ddir / "keyword_candidates.json"
        rjson = json.loads(rpath.read_text(encoding="utf-8"))
        reviews = rjson["reviews"]

        changed = 0
        for rid, rv in reviews.items():
            prods = rv.get("products")
            if prods and OLD in prods:
                rv["products"] = [NEW if p == OLD else p for p in prods]
                if rv.get("product") == OLD:
                    rv["product"] = NEW
                changed += 1
            elif rv.get("product") == OLD:
                rv["product"] = NEW
                changed += 1

        print(f"[{month}] {changed}건 리네임")

        agg_reviews = {}
        for rid, rv in reviews.items():
            arv = dict(rv)
            arv["_id"] = rid
            if not arv.get("products"):
                arv["products"] = [arv["product"]] if arv.get("product") else []
            agg_reviews[rid] = arv

        raw_info = {}
        p = ROOT / f"data/raw/슬룸/{month}/reviews.csv"
        if p.is_file():
            with open(p, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    rid = _col(r, "리뷰ID", "리뷰번호")
                    if not rid:
                        continue
                    try:
                        photo_n = int(str(r.get("포토개수", "0") or "0").strip() or 0)
                    except ValueError:
                        photo_n = 0
                    raw_info[rid] = {"photo": photo_n > 0, "pid": _col(r, "상품번호"),
                                      "price": _col(r, "상품가격"), "raw_name": _col(r, "상품명")}

        prods_json, counts = rebuild_products(agg_reviews, raw_info, prev)
        prev = counts
        kw = json.loads(kpath.read_text(encoding="utf-8")) if kpath.is_file() else {}
        if kw:
            kw = rebuild_keywords(kw, agg_reviews)
        cand = None
        if cpath.is_file():
            cand = rebuild_candidates(json.loads(cpath.read_text(encoding="utf-8")), set(reviews.keys()))

        if apply_ and changed:
            for f in [rpath, ppath, kpath] + ([cpath] if cand is not None else []):
                if f.is_file():
                    shutil.copy2(f, f.with_suffix(f.suffix + ".bak3"))
            rjson["reviews"] = reviews
            rpath.write_text(json.dumps(rjson, ensure_ascii=False, indent=2), encoding="utf-8")
            ppath.write_text(json.dumps(prods_json, ensure_ascii=False, indent=2), encoding="utf-8")
            if kw:
                kpath.write_text(json.dumps(kw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if cand is not None:
                cpath.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"    [APPLIED] {month}")

    print("\n[완료]" + ("" if apply_ else "  (dry-run — --apply 로 반영)"))


if __name__ == "__main__":
    main()
