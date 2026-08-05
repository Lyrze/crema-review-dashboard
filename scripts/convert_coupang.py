"""convert_coupang.py — 쿠팡 제품별 리뷰 CSV → 자사몰(크리마) raw 스키마 변환 (2026-03~07)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
쿠팡 리뷰(제품별 CSV, 8컬럼: No./ReviewId/Stars/Date-Author-Shop/Option/Content/Image/Video)를
크리마 자사몰 raw 스키마 + sentiment 컬럼으로 변환해 월별로 나눈다.

핵심:
  - 날짜는 'Date/Author/Shop' 필드에 파이프로 묶여있음(예: '2026-06-22 05:38:40 | 닉 | 판매자:..') → 앞부분 파싱
  - **2026-03 ~ 2026-07 리뷰만** (그 외 기간 제외)
  - 리뷰작성경로 = "쿠팡" (뱃지/채널 근거), 리뷰ID = "cp_"+ReviewId (자사몰·스마트스토어와 충돌방지)
  - 별점 기반 감성(sentiment 컬럼): 4~5 positive / 3 neutral / 1~2 negative
    (sentiment_src 는 넣지 않음 — validate_data 가 'rating' 폴백을 FAIL 처리하므로. 쿠팡 별점감성은 최종값)
  - 본문 없음(별점만) → 본문 = "별점만 남기고 별도 리뷰작성하지 않음"
  - 상품명 = CSV 파일명(사용자가 정리한 SKU명)
  - 작성자 닉네임·판매자 등은 담지 않음(불필요)

사용:
    python scripts/convert_coupang.py --indir "쿠팡 리뷰 분류 필요" --outdir "data/raw/쿠팡_변환"
"""
import argparse
import glob
import os
import re
import sys
from pathlib import Path

import pandas as pd

CREMA_COLUMNS = [
    "리뷰ID", "리뷰code", "주문번호", "리뷰작성일", "상품구매일", "배송완료일", "리뷰본문",
    "회원ID", "회원명", "회원등급", "추가수집정보", "상품번호", "상품명", "상품가격",
    "상품옵션", "적립금", "적립금지급일", "리뷰작성경로", "리뷰별점", "태그",
    "포토개수", "동영상개수", "포토1_url", "포토2_url", "포토3_url", "포토4_url",
    "동영상1_url", "동영상2_url", "동영상3_url", "동영상4_url", "댓글개수", "댓글내용",
    "sentiment",   # ← process_data 가 읽는 별점기반 감성(영문 컬럼, COLUMN_MAP 대상 아님)
]
CHANNEL_TAG = "쿠팡"
STARONLY_TEXT = "별점만 남기고 별도 리뷰작성하지 않음"
MONTHS_OK = {"2026-03", "2026-04", "2026-05", "2026-06", "2026-07"}


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def star_sentiment(stars) -> str:
    try:
        s = int(float(str(stars).strip()))
    except Exception:
        return "neutral"
    if s >= 4:
        return "positive"
    if s == 3:
        return "neutral"
    return "negative"


def parse_date(dtf):
    """'2026-06-22 05:38:40 | 닉 | 판매자:..' → ('2026-06-22 05:38:40', '2026-06')."""
    head = str(dtf or "").split("|")[0].strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T]?(\d{2}:\d{2}:\d{2})?", head)
    if not m:
        return "", ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    t = m.group(4) or "00:00:00"
    return f"{y}-{mo}-{d} {t}", f"{y}-{mo}"


def clean_id(v):
    s = str(v or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return "" if s in ("nan", "None") else s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--indir", default="쿠팡 리뷰 분류 필요", help="제품별 CSV 폴더")
    ap.add_argument("--outdir", default="data/raw/쿠팡_변환")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.indir, "*.csv")))
    if not files:
        eprint(f"[ERROR] {args.indir} 에 CSV 없음"); sys.exit(1)

    rows_by_month: dict = {}
    total = out_of_range = staronly = skipped = 0
    for f in files:
        product = os.path.basename(f)[:-4]
        df = pd.read_csv(f, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        for _, r in df.iterrows():
            total += 1
            rid = clean_id(r.get("ReviewId"))
            dt, month = parse_date(r.get("Date/Author/Shop"))
            if not rid or not month:
                skipped += 1
                continue
            if month not in MONTHS_OK:
                out_of_range += 1
                continue
            stars = clean_id(r.get("Stars"))
            body = str(r.get("Review Content") or "").strip()
            if not body:
                body = STARONLY_TEXT
                staronly += 1
            photo = 1 if str(r.get("Image URLs") or "").strip() else 0
            video = 1 if str(r.get("Video URLs") or "").strip() else 0
            rec = {c: "" for c in CREMA_COLUMNS}
            rec["리뷰ID"] = f"cp_{rid}"
            rec["리뷰작성일"] = dt
            rec["리뷰본문"] = body
            rec["상품명"] = product
            rec["상품옵션"] = str(r.get("Option Name") or "").strip()[:200]
            rec["리뷰작성경로"] = CHANNEL_TAG
            rec["리뷰별점"] = stars
            rec["포토개수"] = photo
            rec["동영상개수"] = video
            rec["댓글개수"] = 0
            rec["sentiment"] = star_sentiment(stars)   # 별점기반(본문있는 건 이후 recheck가 교정)
            rows_by_month.setdefault(month, []).append(rec)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    kept = 0
    for month in sorted(rows_by_month):
        out = outdir / f"reviews_cp_{month}.csv"
        pd.DataFrame(rows_by_month[month], columns=CREMA_COLUMNS).to_csv(out, index=False, encoding="utf-8-sig")
        kept += len(rows_by_month[month])
        eprint(f"  [OK] {month}: {len(rows_by_month[month])}건 → {out}")
    eprint(f"  완료 — 총 입력 {total} · 기간외 제외 {out_of_range} · 무효 {skipped} · "
           f"채택 {kept}(별점만 {staronly})")


if __name__ == "__main__":
    main()
