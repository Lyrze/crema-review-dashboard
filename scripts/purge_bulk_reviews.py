"""purge_bulk_reviews.py
~~~~~~~~~~~~~~~~~~~~~~~~
크리마 "일괄등록"(bulk CSV import) 템플릿으로 만들어진 마케팅/시딩성 가짜 리뷰를
찾아서 파이프라인 산출물(reviews.json/products.json/summary.json/keywords.json)에서
제거한다. 원본 CSV(리뷰 번호 컬럼)는 업로더가 임의로 정하는 값이라 크리마 내부
review_id와 다르므로, ID 매칭이 아니라 **본문 텍스트 부분일치**로 실제 반영된
리뷰를 찾는다(2026-09, 이 세션에서 확인된 방식).

사용:
  python scripts/purge_bulk_reviews.py --dry-run   # 대상만 확인
  python scripts/purge_bulk_reviews.py --apply     # 실제 제거 + 재계산
"""
import argparse, json, sys, glob, csv, os, shutil
from pathlib import Path
from collections import Counter, defaultdict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "docs" / "data" / "슬룸"
DOWNLOADS = Path(r"C:/Users/올릿/Downloads")


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def load_bulk_snippets():
    files = sorted(glob.glob(str(DOWNLOADS / "brand_csv-*.csv")))
    snippets = []
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        for r in rows[14:]:
            if not r or len(r) < 8:
                continue
            msg = (r[7] or "").strip()
            if len(msg) < 20:
                continue
            snippets.append(msg[:40])
    return snippets, len(files)


def find_matches(snippets):
    """월별로 reviews.json을 뒤져 본문에 스니펫이 포함된 review_id를 찾는다.

    스니펫 하나가 여러 리뷰(같은 문구가 중복 업로드된 경우)에 매칭될 수 있으므로
    첫 매치에서 멈추지 않고 해당 스니펫을 포함하는 리뷰를 전부 찾는다
    (예전엔 break로 스니펫당 1건만 잡아 중복분이 누락되는 버그가 있었음)."""
    matches = defaultdict(set)  # month -> set(review_id)
    details = []  # (month, review_id, product, rating)
    for m in sorted(p.name for p in DATA_ROOT.iterdir() if p.is_dir()):
        rpath = DATA_ROOT / m / "reviews.json"
        if not rpath.is_file():
            continue
        reviews = json.loads(rpath.read_text(encoding="utf-8"))["reviews"]
        for snippet in snippets:
            for rid, r in reviews.items():
                if rid in matches[m]:
                    continue
                if snippet in (r.get("text") or ""):
                    matches[m].add(rid)
                    details.append((m, rid, r.get("product"), r.get("rating")))
    return matches, details


def recompute_products(month: str, purge_ids: set):
    ppath = DATA_ROOT / month / "products.json"
    rpath = DATA_ROOT / month / "reviews.json"
    pdata = json.loads(ppath.read_text(encoding="utf-8"))
    reviews = json.loads(rpath.read_text(encoding="utf-8"))["reviews"]

    affected_products = set()
    for p in pdata["products"]:
        # 이 상품에 속한, purge 대상이 아닌 리뷰만으로 재집계
        prod_reviews = [(rid, r) for rid, r in reviews.items()
                         if r.get("product") == p["name"] and rid not in purge_ids]
        old_count = p["review_count"]
        new_count = len(prod_reviews)
        if new_count == old_count:
            continue  # 이 상품엔 purge 대상 없음
        affected_products.add(p["name"])

        if new_count == 0:
            # 이 달에 이 상품 리뷰가 하나도 안 남으면 0으로 표시만 하고 남겨둠(상품 자체 삭제는 안 함)
            p["review_count"] = 0
            p["avg_rating"] = 0.0
            p["rating_distribution"] = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}
            p["photo_count"] = 0
            p["sentiment"] = {"positive": 0, "neutral": 0, "negative": 0}
            p["positive_rate"] = 0.0
            p["negative_rate"] = 0.0
            p["top_reviews"] = []
            p["bottom_reviews"] = []
            continue

        ratings = [r["rating"] for _, r in prod_reviews]
        p["review_count"] = new_count
        p["avg_rating"] = round(sum(ratings) / new_count, 2)
        dist = Counter(str(r["rating"]) for _, r in prod_reviews)
        p["rating_distribution"] = {str(i): dist.get(str(i), 0) for i in range(1, 6)}
        sent = Counter((r.get("sentiment") or "").lower() for _, r in prod_reviews)
        pos, neu, neg = sent.get("positive", 0), sent.get("neutral", 0), sent.get("negative", 0)
        p["sentiment"] = {"positive": pos, "neutral": neu, "negative": neg}
        p["positive_rate"] = round(pos / new_count * 100, 2) if new_count else 0.0
        p["negative_rate"] = round(neg / new_count * 100, 2) if new_count else 0.0

        # top/bottom_reviews에서 purge 대상 제거(있으면)
        p["top_reviews"] = [tr for tr in (p.get("top_reviews") or []) if tr.get("review_id") not in purge_ids]
        p["bottom_reviews"] = [br for br in (p.get("bottom_reviews") or []) if br.get("review_id") not in purge_ids]

    ppath.write_text(json.dumps(pdata, ensure_ascii=False, indent=2), encoding="utf-8")
    return affected_products


def recompute_summary(month: str):
    spath = DATA_ROOT / month / "summary.json"
    rpath = DATA_ROOT / month / "reviews.json"
    sdata = json.loads(spath.read_text(encoding="utf-8"))
    reviews = json.loads(rpath.read_text(encoding="utf-8"))["reviews"]

    total = len(reviews)
    ratings = [r["rating"] for r in reviews.values() if r.get("rating") is not None]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
    dist = Counter(str(r["rating"]) for r in reviews.values() if r.get("rating") is not None)
    rating_distribution = {str(i): dist.get(str(i), 0) for i in range(1, 6)}
    sent = Counter((r.get("sentiment") or "").lower() for r in reviews.values())
    pos, neu, neg = sent.get("positive", 0), sent.get("neutral", 0), sent.get("negative", 0)

    k = sdata["kpis"]
    k["total_reviews"] = total
    k["avg_rating"] = avg_rating
    k["rating_distribution"] = rating_distribution
    k["positive_count"] = pos
    k["neutral_count"] = neu
    k["negative_count"] = neg
    k["positive_rate"] = round(pos / total * 100, 2) if total else 0.0
    k["negative_rate"] = round(neg / total * 100, 2) if total else 0.0
    # photo_review_count/rate, mom_* 등은 purge 대상이 사진 없는 텍스트 리뷰라 영향이 없어 보존

    # 타임라인 day별 count/avg_rating 재계산
    by_date = defaultdict(list)
    for r in reviews.values():
        d = r.get("date")
        if d:
            by_date[d].append(r.get("rating"))
    new_tl = []
    for entry in sdata["timeline"]:
        d = entry["date"]
        ratings_d = [x for x in by_date.get(d, []) if x is not None]
        entry["count"] = len(ratings_d)
        entry["avg_rating"] = round(sum(ratings_d) / len(ratings_d), 2) if ratings_d else 0.0
        new_tl.append(entry)
    sdata["timeline"] = new_tl

    spath.write_text(json.dumps(sdata, ensure_ascii=False, indent=2), encoding="utf-8")


def recompute_keywords(month: str, purge_ids: set):
    kpath = DATA_ROOT / month / "keywords.json"
    rpath = DATA_ROOT / month / "reviews.json"
    if not kpath.is_file():
        return
    kdata = json.loads(kpath.read_text(encoding="utf-8"))
    reviews = json.loads(rpath.read_text(encoding="utf-8"))["reviews"]
    bi = kdata.get("by_intent", {}) or {}
    removed_kw = 0
    for grp in ("praise", "complaint", "improvement"):
        items = bi.get(grp) or []
        new_items = []
        for kw in items:
            all_ids = kw.get("all_review_ids") or []
            new_ids = [rid for rid in all_ids if rid not in purge_ids]
            if len(new_ids) == len(all_ids):
                new_items.append(kw)
                continue
            if not new_ids:
                removed_kw += 1
                continue  # 이 키워드는 전부 가짜 리뷰였던 것 -> 통째로 제거
            kw["all_review_ids"] = new_ids
            kw["reviews"] = [rid for rid in (kw.get("reviews") or []) if rid not in purge_ids]
            kw["count"] = len(new_ids)
            by_prod_cnt = Counter()
            for rid in new_ids:
                prod = (reviews.get(rid) or {}).get("product") or "(상품 미상)"
                by_prod_cnt[prod] += 1
            kw["by_product"] = [{"product": p, "count": n} for p, n in
                                 sorted(by_prod_cnt.items(), key=lambda x: -x[1])]
            kw["review_samples"] = [s for s in (kw.get("review_samples") or [])
                                     if s.get("review_id") not in purge_ids]
            new_items.append(kw)
        bi[grp] = new_items
    kdata["by_intent"] = bi
    kpath.write_text(json.dumps(kdata, ensure_ascii=False, indent=2), encoding="utf-8")
    if removed_kw:
        eprint(f"  [{month}] 가짜 리뷰만으로 구성됐던 키워드 {removed_kw}개 통째로 제거")


def backup(path: Path):
    bak = path.with_suffix(path.suffix + ".bak_purge")
    if not bak.is_file():
        shutil.copy2(path, bak)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    snippets, nfiles = load_bulk_snippets()
    eprint(f"다운로드 CSV {nfiles}개에서 리뷰 스니펫 {len(snippets)}건 수집")
    matches, details = find_matches(snippets)

    total = sum(len(v) for v in matches.values())
    eprint(f"\n실제 우리 데이터에서 확인된 가짜(일괄등록) 리뷰: 총 {total}건")
    by_month_prod = Counter((m, p) for m, _, p, _ in details)
    for k, v in sorted(by_month_prod.items()):
        eprint(f"  {k}: {v}건")

    if args.dry_run:
        eprint("\n[dry-run] 실제 파일 변경 없음. --apply로 실행하면 반영됩니다.")
        return

    for month, ids in matches.items():
        if not ids:
            continue
        eprint(f"\n===== {month} 처리 시작 (제거 대상 {len(ids)}건) =====")
        rpath = DATA_ROOT / month / "reviews.json"
        ppath = DATA_ROOT / month / "products.json"
        spath = DATA_ROOT / month / "summary.json"
        kpath = DATA_ROOT / month / "keywords.json"
        for p in (rpath, ppath, spath, kpath):
            if p.is_file():
                backup(p)

        rdata = json.loads(rpath.read_text(encoding="utf-8"))
        before = len(rdata["reviews"])
        for rid in ids:
            rdata["reviews"].pop(rid, None)
        rdata["count"] = len(rdata["reviews"])
        rpath.write_text(json.dumps(rdata, ensure_ascii=False, indent=2), encoding="utf-8")
        eprint(f"  reviews.json: {before} -> {rdata['count']}건")

        affected = recompute_products(month, ids)
        eprint(f"  products.json 재계산 완료 (영향받은 상품: {sorted(affected)})")
        recompute_summary(month)
        eprint(f"  summary.json 재계산 완료")
        recompute_keywords(month, ids)
        eprint(f"  keywords.json 재계산 완료")

    eprint("\n[DONE] 전 월 처리 완료. *.bak_purge 로 원본 백업됨.")


if __name__ == "__main__":
    main()
