"""merge_smartstore.py — 스마트스토어 분류 결과를 슬룸 각 월 데이터에 '더해서' 병합
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
자사몰(슬룸) 기존 결과는 그대로 두고, 스마트스토어(스마트스토어 브랜드 폴더)에서 미리 분류한
SS 리뷰를 슬룸 각 월 JSON에 주입한다. SS 리뷰ID는 ss_ 접두사라 자사몰과 겹치지 않고,
대시보드에서 'ss_'로 시작하는 리뷰에 스마트스토어 뱃지가 붙는다.

담당자 업로드 전 실행:
    python scripts/merge_smartstore.py --months 2026-03,2026-04,2026-05
    (SS가 있는 월만; 슬룸 {월} 과 스마트스토어 {월} 이 둘 다 있어야 함)

동작(자사몰 미변경, 추가만):
  reviews.json  : SS 리뷰 추가 (+count)
  keywords.json : SS 키워드를 같은 word에 병합(all_review_ids 합집합) 또는 신규 추가
  pvoc_intent   : SS 리뷰ID를 같은 토픽 pos/neg에 추가
  summary.json  : 리뷰수·별점분포·감성카운트·긍/부율·경로분포에 SS 반영(가산)
  products.json : 같은 상품명에 SS 가산(리뷰수·감성·별점), 신규 상품은 추가

멱등: 이미 병합됐으면(ss_ 리뷰가 이미 있으면) 스킵. 원본 백업(.bak) 생성.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SENTS = ("positive", "neutral", "negative")


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save(o, p):
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_reviews(base, ss):
    br = base.get("reviews", {})
    added = 0
    for rid, rv in ss.get("reviews", {}).items():
        if rid not in br:
            br[rid] = rv
            added += 1
    base["reviews"] = br
    base["count"] = len(br)
    return added


def _merge_kw_obj(jk, sk, sample_cap=6):
    ids = list(dict.fromkeys(list(jk.get("all_review_ids", [])) + list(sk.get("all_review_ids", []))))
    jk["all_review_ids"] = ids
    jk["count"] = len(ids)
    if "reviews" in jk or "reviews" in sk:
        jk["reviews"] = list(dict.fromkeys(list(jk.get("reviews", [])) + list(sk.get("reviews", []))))
    # review_samples: 자사몰 우선 + SS 일부 보충
    samp = list(jk.get("review_samples", []))
    for s in sk.get("review_samples", []):
        if len(samp) >= sample_cap:
            break
        samp.append(s)
    jk["review_samples"] = samp
    # by_product 카운트 가산
    if isinstance(jk.get("by_product"), list) and isinstance(sk.get("by_product"), list):
        bp = {d.get("product"): dict(d) for d in jk["by_product"]}
        for d in sk["by_product"]:
            k = d.get("product")
            if k in bp:
                bp[k]["count"] = bp[k].get("count", 0) + d.get("count", 0)
            else:
                bp[k] = dict(d)
        jk["by_product"] = sorted(bp.values(), key=lambda x: x.get("count", 0), reverse=True)


def merge_keyword_list(jlist, slist):
    idx = {k.get("word"): k for k in jlist}
    for sk in slist:
        w = sk.get("word")
        if w in idx:
            _merge_kw_obj(idx[w], sk)
        else:
            jlist.append(sk)
    jlist.sort(key=lambda x: x.get("count", 0), reverse=True)


def merge_keywords(base, ss):
    for bucket in ("praise", "complaint", "improvement"):
        jl = base.setdefault("by_intent", {}).setdefault(bucket, [])
        merge_keyword_list(jl, ss.get("by_intent", {}).get(bucket, []))
    for key in ("negative_keywords", "positive_keywords", "low_rating_keywords"):
        if key in base or key in ss:
            jl = base.setdefault(key, [])
            merge_keyword_list(jl, ss.get(key, []))


def merge_pvoc(base, ss):
    bt = base.setdefault("topics", {})
    for name, io in ss.get("topics", {}).items():
        tgt = bt.setdefault(name, {"pos": [], "neg": []})
        for pol in ("pos", "neg"):
            tgt[pol] = list(dict.fromkeys(list(tgt.get(pol, [])) + list(io.get(pol, []))))


def recompute_from_reviews(reviews):
    """병합된 reviews 로 감성/별점 집계."""
    pos = neu = neg = 0
    rd = {str(i): 0 for i in range(1, 6)}
    rsum = 0
    n = 0
    for r in reviews.values():
        n += 1
        s = r.get("sentiment")
        if s == "positive":
            pos += 1
        elif s == "negative":
            neg += 1
        else:
            neu += 1
        rt = int(r.get("rating") or 0)
        if 1 <= rt <= 5:
            rd[str(rt)] += 1
            rsum += rt
    return {"n": n, "pos": pos, "neu": neu, "neg": neg, "rd": rd,
            "avg": round(rsum / n, 2) if n else 0}


def merge_summary(base, reviews, ss_reviews, channel="스마트스토어"):
    k = base.get("kpis", {})
    agg = recompute_from_reviews(reviews)
    k["total_reviews"] = agg["n"]
    k["avg_rating"] = agg["avg"]
    k["rating_distribution"] = agg["rd"]
    k["positive_count"] = agg["pos"]
    k["neutral_count"] = agg["neu"]
    k["negative_count"] = agg["neg"]
    k["positive_rate"] = round(agg["pos"] / agg["n"] * 100, 2) if agg["n"] else 0
    k["negative_rate"] = round(agg["neg"] / agg["n"] * 100, 2) if agg["n"] else 0
    base["kpis"] = k
    # 경로분포에 채널 가산
    rpd = base.get("review_path_distribution")
    if isinstance(rpd, dict):
        rpd[channel] = rpd.get(channel, 0) + len(ss_reviews)
        base["review_path_distribution"] = rpd


def merge_products(base, ss):
    bl = base.get("products") if isinstance(base, dict) else base
    sl = ss.get("products") if isinstance(ss, dict) else ss
    idx = {p.get("name"): p for p in bl}
    for sp in sl:
        nm = sp.get("name")
        if nm in idx:
            jp = idx[nm]
            jrc, src = jp.get("review_count", 0), sp.get("review_count", 0)
            tot = jrc + src
            # 감성 가산
            js = jp.get("sentiment", {}) or {}
            ss_ = sp.get("sentiment", {}) or {}
            merged_sent = {x: js.get(x, 0) + ss_.get(x, 0) for x in SENTS}
            jp["sentiment"] = merged_sent
            # 별점분포 가산
            jrd = jp.get("rating_distribution", {}) or {}
            srd = sp.get("rating_distribution", {}) or {}
            jp["rating_distribution"] = {str(i): jrd.get(str(i), 0) + srd.get(str(i), 0) for i in range(1, 6)}
            # 평균별점 가중
            if tot:
                jp["avg_rating"] = round((jp.get("avg_rating", 0) * jrc + sp.get("avg_rating", 0) * src) / tot, 2)
                jp["positive_rate"] = round(merged_sent["positive"] / tot * 100, 2)
                jp["negative_rate"] = round(merged_sent["negative"] / tot * 100, 2)
            jp["review_count"] = tot
            jp["photo_count"] = jp.get("photo_count", 0) + sp.get("photo_count", 0)
        else:
            bl.append(sp)
    bl.sort(key=lambda x: x.get("review_count", 0), reverse=True)


def merge_month(month, jm_dir, ss_dir, prefix="ss_", channel="스마트스토어"):
    jm_r = jm_dir / "reviews.json"
    ss_r = ss_dir / "reviews.json"
    if not jm_r.is_file():
        eprint(f"  [SKIP] {month}: {jm_dir.name} reviews.json 없음"); return False
    if not ss_r.is_file():
        eprint(f"  [SKIP] {month}: {channel} 분류결과 없음"); return False
    base_reviews = load(jm_r)
    # 멱등: 이미 해당 채널(prefix) 리뷰가 있으면 스킵
    if any(str(k).startswith(prefix) for k in base_reviews.get("reviews", {})):
        eprint(f"  [SKIP] {month}: 이미 {channel} 병합됨"); return False
    ss_reviews = load(ss_r)

    # 백업
    for f in ("reviews.json", "keywords.json", "summary.json", "products.json", "pvoc_intent.json"):
        p = jm_dir / f
        if p.is_file():
            shutil.copy2(p, p.with_suffix(".json.bak"))

    added = merge_reviews(base_reviews, ss_reviews)
    save(base_reviews, jm_r)

    if (jm_dir / "keywords.json").is_file() and (ss_dir / "keywords.json").is_file():
        jk = load(jm_dir / "keywords.json"); merge_keywords(jk, load(ss_dir / "keywords.json"))
        save(jk, jm_dir / "keywords.json")
    if (jm_dir / "summary.json").is_file():
        js = load(jm_dir / "summary.json")
        merge_summary(js, base_reviews.get("reviews", {}), ss_reviews.get("reviews", {}), channel=channel)
        save(js, jm_dir / "summary.json")
    if (jm_dir / "products.json").is_file() and (ss_dir / "products.json").is_file():
        jp = load(jm_dir / "products.json"); merge_products(jp, load(ss_dir / "products.json"))
        save(jp, jm_dir / "products.json")
    if (ss_dir / "pvoc_intent.json").is_file():
        pv_p = jm_dir / "pvoc_intent.json"
        jv = load(pv_p) if pv_p.is_file() else {"topics": {}}
        merge_pvoc(jv, load(ss_dir / "pvoc_intent.json"))
        save(jv, pv_p)

    eprint(f"  [OK] {month}: {channel} {added}건 병합 (자사몰 미변경, 추가만)")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="슬룸", help="자사몰 브랜드(대상)")
    ap.add_argument("--ss-brand", default="스마트스토어", help="채널 분류결과 브랜드 폴더 (예: 스마트스토어, 쿠팡)")
    ap.add_argument("--prefix", default="ss_", help="채널 리뷰ID 접두사 (스마트스토어=ss_, 쿠팡=cp_)")
    ap.add_argument("--channel", default="스마트스토어", help="경로분포/로그에 쓸 채널명")
    ap.add_argument("--months", required=True, help="쉼표구분 월 (채널 분류결과 있는 월만)")
    ap.add_argument("--data-root", default=str(ROOT / "docs" / "data"))
    args = ap.parse_args()
    droot = Path(args.data_root)
    n = 0
    for m in [x.strip() for x in args.months.split(",") if x.strip()]:
        if merge_month(m, droot / args.brand / m, droot / args.ss_brand / m,
                       prefix=args.prefix, channel=args.channel):
            n += 1
    eprint(f"완료 — {n}개월 병합. 대시보드에서 병합 리뷰 뱃지 확인 후 커밋/업로드하세요.")


if __name__ == "__main__":
    main()
