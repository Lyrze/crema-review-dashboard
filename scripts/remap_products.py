"""remap_products.py — SS(스마트스토어) 상품명 매핑 보완 + 06월 자사몰 매핑 미적용분 보완

배경: 스마트스토어 리뷰는 네이버 리스팅 제목을 그대로 'product'로 써서 ProductResolver를
전혀 거치지 않았음(옵션 필드도 없어 띄어쓰기 차이로 기존 매칭도 깨짐). 06월은 자사몰조차
patch_product_mapping.py 가 한 번도 안 돌아 'products'(복수 귀속) 필드가 없었음.

동작:
  · 06월 자사몰 리뷰(ss_ 아님): ProductResolver(raw_name, raw_option) 로 products/is_set 부여
    (raw CSV 필요, data/raw/{brand}/2026-06/reviews.csv). 사은품(멤버십 빈 리스트)은 제거.
  · 모든 월의 SS 리뷰(ss_ 접두): ProductResolver(product, "") 우선 시도 → 실패 시 공백무시
    별칭 재시도 → 그래도 실패면 담당자 확정 보정표(SS_OVERRIDE)로 해결. 액세서리류는 제거.
  · 이후 patch_product_mapping.rebuild_products/rebuild_keywords/rebuild_candidates 로
    products.json/keywords.json/keyword_candidates.json 재계산, summary.json 도 재계산.

★ dry-run 기본, --apply 로 실제 반영. .bak 백업.
사용:
    python scripts/remap_products.py --months 2026-03,2026-04,2026-05,2026-06,2026-07
    python scripts/remap_products.py --months 2026-03,2026-04,2026-05,2026-06,2026-07 --apply
"""
import argparse
import csv
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from product_mapping import ProductResolver  # noqa: E402
from patch_product_mapping import rebuild_products, rebuild_keywords, rebuild_candidates, _col  # noqa: E402

# 담당자 확정 보정 — ProductResolver(+공백무시 별칭)로도 안 풀리는 SS 상품명 10건.
# 키: 이름에 포함되면 매칭되는 고유 부분 문자열(우선순위=리스트 순서, 먼저 매칭되는 것 채택)
SS_OVERRIDE = [
    ("USB 충전 어댑터", []),
    ("마그네슘 시너지 크림", []),
    ("지퍼 분리형", []),
    ("하루끝차", []),
    ("쿨매트", []),
    ("등허리 힐링케어", ["등허리 힐링케어"]),
    ("허리편한케어 마스터", ["허리편한케어 마스터"]),
    ("메모리폼 편한 베개", ["목베개"]),
]


def norm_nospace(s):
    return re.sub(r"\s+", "", s or "")


def load_alias_index(cfg):
    idx = [(norm_nospace(a), clean) for a, clean in cfg["aliases"]]
    idx.sort(key=lambda x: -len(x[0]))
    return idx


def resolve_ss_name(name, resolver, alias_nospace_idx, canonical):
    got, _ = resolver.resolve_ex(name, "")
    if got and all(g in canonical for g in got):
        return got
    hay = norm_nospace(name).lower()
    for a, clean in alias_nospace_idx:
        if len(a) >= 3 and a in hay:
            return [clean]
    for key, mapped in SS_OVERRIDE:
        if key in name:
            return mapped
    return got  # 마지막 수단: 원본 그대로(사람이 나중에 확인 가능하도록 리스트에 남김)


def load_raw_option_map(brand, month):
    """raw CSV -> {review_id: (product_name, option)} (06월 자사몰 재매핑용)"""
    p = ROOT / f"data/raw/{brand}/{month}/reviews.csv"
    out = {}
    if not p.is_file():
        return out
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = _col(r, "리뷰ID", "리뷰번호")
            if rid:
                out[rid] = (r.get("상품명", "") or "", r.get("상품옵션", "") or "")
    return out


def remap_month(brand, month, resolver, alias_idx, canonical, prev_counts, apply_, backup, redo_own):
    ddir = ROOT / "docs" / "data" / brand / month
    rpath, spath, ppath, kpath = (ddir / "reviews.json", ddir / "summary.json",
                                  ddir / "products.json", ddir / "keywords.json")
    cpath = ddir / "keyword_candidates.json"
    if not rpath.is_file():
        print(f"  [SKIP] {month}: reviews.json 없음")
        return prev_counts

    rjson = json.loads(rpath.read_text(encoding="utf-8"))
    reviews = rjson.get("reviews", {})

    own_opt_map = load_raw_option_map(brand, month) if redo_own else {}
    dropped_own = dropped_ss = remapped_ss = 0
    new_reviews = {}
    for rid, rv in dict(reviews).items():
        rv = dict(rv)
        if rid.startswith("ss_"):
            cur = rv.get("product", "")
            new_prods = resolve_ss_name(cur, resolver, alias_idx, canonical)
            if not new_prods:
                dropped_ss += 1
                continue
            if new_prods != [cur]:
                remapped_ss += 1
            rv["products"] = new_prods
            rv["product"] = new_prods[0]
            rv["is_set"] = False
            new_reviews[rid] = rv
        else:
            if redo_own and rid in own_opt_map:
                raw_name, opt = own_opt_map[rid]
                prods, is_set = resolver.resolve_ex(raw_name, opt)
                if not prods:
                    dropped_own += 1
                    continue
                rv["products"] = prods
                rv["product"] = prods[0]
                rv["is_set"] = is_set
            new_reviews[rid] = rv

    print(f"\n[{month}] SS 재매핑 {remapped_ss}건 · SS 액세서리 제외 {dropped_ss}건"
          + (f" · 자사몰 사은품 제외 {dropped_own}건(06월 최초 매핑)" if redo_own else ""))

    alive = set(new_reviews.keys())
    agg_reviews = {}
    for rid, rv in new_reviews.items():
        arv = dict(rv)
        arv["_id"] = rid
        if not arv.get("products"):
            arv["products"] = [arv["product"]] if arv.get("product") else []
        agg_reviews[rid] = arv

    raw_info = {}
    p = ROOT / f"data/raw/{brand}/{month}/reviews.csv"
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

    prods_json, counts = rebuild_products(agg_reviews, raw_info, prev_counts)
    kw = json.loads(kpath.read_text(encoding="utf-8")) if kpath.is_file() else {}
    if kw:
        kw = rebuild_keywords(kw, agg_reviews)
    cand = None
    if cpath.is_file():
        cand = rebuild_candidates(json.loads(cpath.read_text(encoding="utf-8")), alive)

    print(f"    상품 {len(prods_json['products'])}종 · 리뷰 {len(new_reviews)}건")

    if apply_:
        targets = [rpath, spath, ppath, kpath] + ([cpath] if cand is not None else [])
        if backup:
            for f in targets:
                if f.is_file():
                    shutil.copy2(f, f.with_suffix(f.suffix + ".bak2"))
        rjson["reviews"] = new_reviews
        rjson["count"] = len(new_reviews)
        rpath.write_text(json.dumps(rjson, ensure_ascii=False, indent=2), encoding="utf-8")
        ppath.write_text(json.dumps(prods_json, ensure_ascii=False, indent=2), encoding="utf-8")
        if kw:
            kpath.write_text(json.dumps(kw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if cand is not None:
            cpath.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")
        if spath.is_file() and (dropped_ss or dropped_own):
            old_summary = json.loads(spath.read_text(encoding="utf-8"))
            _recompute_summary_totals(new_reviews, old_summary)
            spath.write_text(json.dumps(old_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    [APPLIED] {month} 반영 완료")

    return counts


def _recompute_summary_totals(reviews, summary):
    total = len(reviews)
    rd = {str(i): 0 for i in range(1, 6)}
    pos = neu = neg = 0
    rsum = 0
    for rv in reviews.values():
        rt = int(rv.get("rating") or 0)
        if 1 <= rt <= 5:
            rd[str(rt)] += 1
            rsum += rt
        s = rv.get("sentiment", "neutral")
        if s == "positive":
            pos += 1
        elif s == "negative":
            neg += 1
        else:
            neu += 1
    k = summary.setdefault("kpis", {})
    k["total_reviews"] = total
    k["avg_rating"] = round(rsum / total, 2) if total else 0.0
    k["rating_distribution"] = rd
    k["positive_count"] = pos
    k["neutral_count"] = neu
    k["negative_count"] = neg
    k["positive_rate"] = round(pos / total * 100, 2) if total else 0.0
    k["negative_rate"] = round(neg / total * 100, 2) if total else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="슬룸")
    ap.add_argument("--months", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    resolver = ProductResolver()
    cfg = json.loads((ROOT / "scripts" / "product_mapping.json").read_text(encoding="utf-8"))
    canonical = set(cfg["canonical"]) | {"등허리 힐링케어", "허리편한케어 마스터", "목베개"}
    alias_idx = load_alias_index(cfg)

    months = [m.strip() for m in args.months.split(",") if m.strip()]
    prev = {}
    for mo in months:
        redo_own = (mo == "2026-06")  # 06월만 자사몰 최초 매핑 적용
        prev = remap_month(args.brand, mo, resolver, alias_idx, canonical, prev,
                            args.apply, not args.no_backup, redo_own)
    print("\n[완료]" + ("" if args.apply else "  (dry-run — 실제 반영은 --apply)"))


if __name__ == "__main__":
    main()
