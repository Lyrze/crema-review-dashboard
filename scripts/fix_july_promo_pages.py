"""fix_july_promo_pages.py — 7월 [신금동x슬룸]/[마리코리아x슬룸] 프로모 페이지 리뷰 재매핑

담당자 확인 결과(2026-08-03):
  · [신금동x슬룸]: 옵션에 '경추 마사지 베개 V2' -> 목 마사지 베개 V2,
                   옵션에 '경추 마사지 베개 V1' -> 목베개 플러스
  · [마리코리아x슬룸]: 세트 옵션(허리편한케어V2+목마사지베개V2)은 이미 정상 분해되어 있음(그대로 둠).
                      단품 옵션('목 마사지베개 V2 1개', 공백표기차로 미매칭)은 -> 목 마사지 베개 V2
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

MONTH = "2026-07"


def main():
    apply_ = "--apply" in sys.argv
    ddir = ROOT / "docs" / "data" / "슬룸" / MONTH
    rpath, ppath, kpath = ddir / "reviews.json", ddir / "products.json", ddir / "keywords.json"
    cpath = ddir / "keyword_candidates.json"

    raw_opt = {}
    with open(ROOT / f"data/raw/슬룸/{MONTH}/reviews.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = _col(r, "리뷰ID", "리뷰번호")
            if rid:
                raw_opt[rid] = (r.get("상품명", "") or "", r.get("상품옵션", "") or "")

    rjson = json.loads(rpath.read_text(encoding="utf-8"))
    reviews = rjson["reviews"]

    changed = 0
    for rid, rv in reviews.items():
        name, opt = raw_opt.get(rid, ("", ""))
        if "신금동" in name:
            new_prods = ["목베개 플러스"] if "V1" in opt else ["목 마사지 베개 V2"]
            rv["products"] = new_prods
            rv["product"] = new_prods[0]
            changed += 1
        elif "마리코리아" in name:
            if rv.get("products") == ["허리편한케어 V2", "목 마사지 베개 V2"]:
                continue  # 이미 정상 분해됨
            rv["products"] = ["목 마사지 베개 V2"]
            rv["product"] = "목 마사지 베개 V2"
            changed += 1

    print(f"[{MONTH}] 프로모 페이지 재매핑 {changed}건")

    # prev_counts: 06월 products.json 에서 시드(체인 유지)
    prev_path = ROOT / "docs/data/슬룸/2026-06/products.json"
    prev_counts = {}
    if prev_path.is_file():
        for p in json.loads(prev_path.read_text(encoding="utf-8"))["products"]:
            prev_counts[p["name"]] = {"rc": p["review_count"], "avg": p["avg_rating"]}

    agg_reviews = {}
    for rid, rv in reviews.items():
        arv = dict(rv)
        arv["_id"] = rid
        if not arv.get("products"):
            arv["products"] = [arv["product"]] if arv.get("product") else []
        agg_reviews[rid] = arv

    raw_info = {}
    with open(ROOT / f"data/raw/슬룸/{MONTH}/reviews.csv", encoding="utf-8-sig") as f:
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

    prods_json, counts = rebuild_products(agg_reviews, raw_info, prev_counts)
    kw = json.loads(kpath.read_text(encoding="utf-8")) if kpath.is_file() else {}
    if kw:
        kw = rebuild_keywords(kw, agg_reviews)
    cand = None
    if cpath.is_file():
        cand = rebuild_candidates(json.loads(cpath.read_text(encoding="utf-8")), set(reviews.keys()))

    print(f"    상품 {len(prods_json['products'])}종")
    for name in ["목 마사지 베개 V2", "목베개 플러스", "허리편한케어 V2"]:
        rc = next((p["review_count"] for p in prods_json["products"] if p["name"] == name), None)
        print(f"    {name}: {rc}")

    if apply_ and changed:
        for f in [rpath, ppath, kpath] + ([cpath] if cand is not None else []):
            if f.is_file():
                shutil.copy2(f, f.with_suffix(f.suffix + ".bak4"))
        rjson["reviews"] = reviews
        rpath.write_text(json.dumps(rjson, ensure_ascii=False, indent=2), encoding="utf-8")
        ppath.write_text(json.dumps(prods_json, ensure_ascii=False, indent=2), encoding="utf-8")
        if kw:
            kpath.write_text(json.dumps(kw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if cand is not None:
            cpath.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")
        print("    [APPLIED]")
    else:
        print("\n(dry-run — --apply 로 반영)")


if __name__ == "__main__":
    main()
