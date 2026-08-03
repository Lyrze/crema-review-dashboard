"""purge_bulk_reviews.py — 브랜드가 직접 일괄등록한 리뷰를 대시보드 산출물에서 제거

'일괄 등록 리뷰/*.csv' (크리마 리뷰 일괄등록 템플릿으로 브랜드가 직접 업로드한 리뷰)의
리뷰 본문을 raw CSV의 실제 리뷰 본문과 정확 대조해 동일 리뷰를 찾아 제거한다.

  · reviews.json  : 매칭된 자사몰 리뷰 제거 (스마트스토어 ss_ 리뷰는 대상 아님 — 건드리지 않음)
  · summary.json  : KPI(총수·평점분포·감성카운트·긍부율·경로분포·타임라인·포토율) 재계산
  · products.json : patch_product_mapping.rebuild_products 재사용 (멤버십 기반, MoM prev 체인)
  · keywords.json : patch_product_mapping.rebuild_keywords 재사용 (word/all_review_ids/by_product)
  · keyword_candidates.json : patch_product_mapping.rebuild_candidates 재사용
  · pvoc_intent.json : topics.{pos,neg} 에서 제거, 양쪽 다 빈 토픽은 삭제

★ 감성/키워드 AI 판정 자체는 건드리지 않는다 — 이미 판정된 리뷰를 '빼기'만 한다.
★ 기본은 dry-run(요약만 출력). 실제 반영은 --apply. 반영 전 .bak 백업(--no-backup 로 생략).
★ 월은 반드시 시간순으로 지정 (products.json의 전월대비 prev_* 체인 유지).

사용:
    python scripts/purge_bulk_reviews.py --brand 슬룸 --months 2026-03,2026-04,2026-05,2026-06,2026-07
    python scripts/purge_bulk_reviews.py --brand 슬룸 --months 2026-03,2026-04,2026-05,2026-06,2026-07 --apply
"""
import argparse
import csv
import glob
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
from patch_product_mapping import rebuild_products, rebuild_keywords, rebuild_candidates, _col  # noqa: E402


def norm(s):
    return re.sub(r"\s+", "", s or "").strip()


def load_bulk_texts(bulk_dir: Path):
    """일괄등록 CSV 전부에서 리뷰내용(message) 텍스트 집합 반환."""
    texts = set()
    files = sorted(glob.glob(str(bulk_dir / "*.csv")))
    for f in files:
        with open(f, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        # 행 구조: 0=헤더, 1=필수/선택, 2=설명문, 3=예시(crematest/홍길동), 4~=실제 데이터
        for r in rows[4:]:
            if len(r) < 8:
                continue
            msg = (r[7] or "").strip()
            if msg:
                texts.add(norm(msg))
    return texts, len(files)


def load_raw_info(brand: str, month: str):
    """raw CSV -> {review_id: {photo(bool), pid, price, raw_name, body}}"""
    p = ROOT / f"data/raw/{brand}/{month}/reviews.csv"
    out = {}
    if not p.is_file():
        return out
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = _col(r, "리뷰ID", "리뷰번호")
            if not rid:
                continue
            try:
                photo_n = int(str(r.get("포토개수", "0") or "0").strip() or 0)
            except ValueError:
                photo_n = 0
            out[rid] = {
                "photo": photo_n > 0,
                "pid": _col(r, "상품번호"),
                "price": _col(r, "상품가격"),
                "raw_name": _col(r, "상품명"),
                "body": r.get("리뷰본문", "") or "",
            }
    return out


def find_excluded_ids(reviews: dict, raw_info: dict, bulk_texts: set):
    excluded = []
    for rid, rv in reviews.items():
        if str(rid).startswith("ss_"):
            continue
        body = raw_info.get(rid, {}).get("body") or rv.get("text", "")
        if norm(body) in bulk_texts:
            excluded.append(rid)
    return excluded


def recompute_summary(reviews: dict, raw_info: dict, old_summary: dict):
    total = len(reviews)
    rd = {str(i): 0 for i in range(1, 6)}
    pos = neu = neg = 0
    rsum = 0
    path_dist = Counter()
    tl_count = Counter()
    tl_ratings = {}
    photo_n = 0
    for rid, rv in reviews.items():
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
        ch = rv.get("channel") or "미분류"
        path_dist[ch] += 1
        d = rv.get("date")
        if d:
            tl_count[d] += 1
            tl_ratings.setdefault(d, []).append(rt)
        if raw_info.get(rid, {}).get("photo"):
            photo_n += 1

    avg = round(rsum / total, 2) if total else 0.0
    summary = dict(old_summary)
    k = dict(summary.get("kpis", {}))
    k["total_reviews"] = total
    k["avg_rating"] = avg
    k["rating_distribution"] = rd
    k["positive_count"] = pos
    k["neutral_count"] = neu
    k["negative_count"] = neg
    k["positive_rate"] = round(pos / total * 100, 2) if total else 0.0
    k["negative_rate"] = round(neg / total * 100, 2) if total else 0.0
    k["photo_review_count"] = photo_n
    k["photo_review_rate"] = round(photo_n / total * 100, 1) if total else 0.0
    summary["kpis"] = k
    summary["review_path_distribution"] = dict(path_dist)
    tl = []
    for d in sorted(tl_count):
        ratings = [x for x in tl_ratings[d] if x]
        tl.append({
            "date": d, "count": tl_count[d],
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else 0,
        })
    summary["timeline"] = tl
    return summary


def recompute_pvoc(pvoc: dict, alive: set):
    topics = pvoc.get("topics", {})
    new_topics = {}
    for name, io in topics.items():
        pos = [x for x in io.get("pos", []) if x in alive]
        neg = [x for x in io.get("neg", []) if x in alive]
        if pos or neg:
            new_topics[name] = {"pos": pos, "neg": neg}
    out = dict(pvoc)
    out["topics"] = new_topics
    return out


def purge_month(brand, month, bulk_texts, prev_counts, apply_, backup):
    ddir = ROOT / "docs" / "data" / brand / month
    rpath, spath, ppath, kpath = (ddir / "reviews.json", ddir / "summary.json",
                                  ddir / "products.json", ddir / "keywords.json")
    cpath = ddir / "keyword_candidates.json"
    pvpath = ddir / "pvoc_intent.json"
    if not rpath.is_file():
        print(f"  [SKIP] {month}: reviews.json 없음")
        return prev_counts

    rjson = json.loads(rpath.read_text(encoding="utf-8"))
    reviews = rjson.get("reviews", {})
    raw_info = load_raw_info(brand, month)
    if not raw_info:
        print(f"  [WARN] {month}: raw CSV 없음 — 리뷰 본문(reviews.json)만으로 대조합니다(600자 절단 영향 가능)")

    excluded = find_excluded_ids(reviews, raw_info, bulk_texts)
    survivors = {rid: rv for rid, rv in reviews.items() if rid not in excluded}
    alive = set(survivors.keys())

    print(f"\n[{month}] 전체 {len(reviews)}건 중 일괄등록 매칭 {len(excluded)}건 제외 -> 잔존 {len(survivors)}건")

    # rebuild_products/rebuild_keywords 는 rv['products'](복수 귀속)를 요구한다.
    # 일부 월(예: 06월)은 patch_product_mapping.py 를 아직 안 거쳐 'product'(단수)만 있음 —
    # 집계 전용 사본에서만 보정하고, 실제 reviews.json(survivors)의 스키마는 건드리지 않는다.
    agg_reviews = {}
    for rid, rv in survivors.items():
        arv = dict(rv)
        arv["_id"] = rid
        if not arv.get("products"):
            arv["products"] = [arv["product"]] if arv.get("product") else []
        agg_reviews[rid] = arv

    prods_json, counts = rebuild_products(agg_reviews, raw_info, prev_counts)

    kw = json.loads(kpath.read_text(encoding="utf-8")) if kpath.is_file() else {}
    kw = rebuild_keywords(kw, agg_reviews) if kw else kw

    cand = None
    if cpath.is_file():
        cand = rebuild_candidates(json.loads(cpath.read_text(encoding="utf-8")), alive)

    old_summary = json.loads(spath.read_text(encoding="utf-8")) if spath.is_file() else {}
    new_summary = recompute_summary(survivors, raw_info, old_summary) if old_summary else old_summary

    new_pvoc = None
    if pvpath.is_file():
        new_pvoc = recompute_pvoc(json.loads(pvpath.read_text(encoding="utf-8")), alive)

    print(f"    상품 {len(prods_json['products'])}종 (재계산 완료)")
    if new_summary:
        print(f"    총 리뷰수 {new_summary['kpis']['total_reviews']} · "
              f"평점 {new_summary['kpis']['avg_rating']} · "
              f"긍정률 {new_summary['kpis']['positive_rate']}%")

    if apply_:
        targets = [rpath, spath, ppath, kpath, pvpath] + ([cpath] if cand is not None else [])
        if backup:
            for f in targets:
                if f.is_file():
                    shutil.copy2(f, f.with_suffix(f.suffix + ".bak"))
        rjson["reviews"] = survivors
        rjson["count"] = len(survivors)
        rpath.write_text(json.dumps(rjson, ensure_ascii=False, indent=2), encoding="utf-8")
        ppath.write_text(json.dumps(prods_json, ensure_ascii=False, indent=2), encoding="utf-8")
        if kw:
            kpath.write_text(json.dumps(kw, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if new_summary:
            spath.write_text(json.dumps(new_summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if cand is not None:
            cpath.write_text(json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")
        if new_pvoc is not None:
            pvpath.write_text(json.dumps(new_pvoc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    [APPLIED] {month} 반영 완료" + (" (.bak 백업)" if backup else ""))

    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="슬룸")
    ap.add_argument("--months", required=True, help="쉼표구분, 시간순으로(예: 2026-03,2026-04,...)")
    ap.add_argument("--bulk-dir", default=str(ROOT / "일괄 등록 리뷰"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    bulk_texts, n_files = load_bulk_texts(Path(args.bulk_dir))
    print(f"[일괄등록] {n_files}개 파일 · 고유 텍스트 {len(bulk_texts)}건 로드")

    months = [m.strip() for m in args.months.split(",") if m.strip()]
    prev = {}
    for mo in months:
        prev = purge_month(args.brand, mo, bulk_texts, prev, args.apply, not args.no_backup)
    print("\n[완료]" + ("" if args.apply else "  (dry-run — 실제 반영은 --apply)"))


if __name__ == "__main__":
    main()
