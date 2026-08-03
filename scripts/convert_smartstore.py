"""convert_smartstore.py — 네이버 스마트스토어 리뷰 엑셀 → 자사몰(크리마) raw CSV 스키마 변환
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
스마트스토어에서 내려받은 리뷰 엑셀(.xlsx)을 크리마 자사몰 raw CSV 32컬럼 형식으로 바꿔,
월별로 나눠 저장한다. 이후 자사몰 raw 와 concat 해서 process_data.py 로 한 번에 처리하면
슬룸 브랜드에 '스마트스토어 채널'로 합쳐진다.

핵심 처리:
  - 리뷰작성경로 = "스마트스토어" 고정  → 대시보드에서 채널 구분/뱃지의 근거가 됨
  - 리뷰ID = "ss_" + 리뷰글번호          → 자사몰 리뷰ID 와 절대 충돌하지 않도록 접두사
  - PII(상품주문번호·등록자 등)는 담지 않음(빈값) → GitHub 유출 원천 차단
  - 스마트스토어에 없는 컬럼(상품옵션·회원등급·가격 등)은 빈값

사용:
    python scripts/convert_smartstore.py \
        --input "data/anonymized/슬룸/스마트스토어 0301~0731/스마트스토어 리뷰 0301~0731.xlsx" \
        --outdir "data/raw/스마트스토어_변환"
    # → reviews_ss_2026-03.csv ... reviews_ss_2026-07.csv 생성 (월별)

출력은 data/raw/ 하위(gitignore)에 두어 실수로 커밋되지 않게 한다.
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

# 크리마 자사몰 raw CSV 컬럼 순서 (data/raw/{브랜드}/{월}/reviews.csv 헤더와 동일해야 concat 가능)
CREMA_COLUMNS = [
    "리뷰ID", "리뷰code", "주문번호", "리뷰작성일", "상품구매일", "배송완료일", "리뷰본문",
    "회원ID", "회원명", "회원등급", "추가수집정보", "상품번호", "상품명", "상품가격",
    "상품옵션", "적립금", "적립금지급일", "리뷰작성경로", "리뷰별점", "태그",
    "포토개수", "동영상개수", "포토1_url", "포토2_url", "포토3_url", "포토4_url",
    "동영상1_url", "동영상2_url", "동영상3_url", "동영상4_url", "댓글개수", "댓글내용",
]

CHANNEL_TAG = "스마트스토어"   # 리뷰작성경로에 넣을 채널 값 (뱃지/채널구분 근거)


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def parse_dt(raw) -> tuple:
    """'2026.07.31. 12:27:49' → ('2026-07-31 12:27:49', '2026-07').
    파싱 실패 시 ('', '') 반환."""
    s = str(raw or "").strip()
    m = re.match(r"(\d{4})[.\-/](\d{2})[.\-/](\d{2})\.?\s*(\d{2}:\d{2}:\d{2})?", s)
    if not m:
        return "", ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    t = m.group(4) or "00:00:00"
    return f"{y}-{mo}-{d} {t}", f"{y}-{mo}"


def clean_int_str(v) -> str:
    """5033037755.0 같은 float 표기를 5033037755 로 (ID/번호용)."""
    s = str(v or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    if s in ("nan", "None"):
        return ""
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="스마트스토어 리뷰 엑셀(.xlsx) 경로")
    ap.add_argument("--outdir", default="data/raw/스마트스토어_변환", help="월별 CSV 출력 폴더")
    ap.add_argument("--brand-prefix", default="ss_", help="리뷰ID 충돌방지 접두사(기본 ss_)")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_file():
        eprint(f"[ERROR] 입력 파일 없음: {inp}")
        sys.exit(1)

    import warnings
    warnings.filterwarnings("ignore")  # openpyxl 기본스타일 경고 억제
    df = pd.read_excel(inp, dtype=str)
    eprint(f"  읽음: {len(df)}건 · 컬럼 {len(df.columns)}개")

    # 필수 원본 컬럼 확인
    need = ["리뷰글번호", "리뷰등록일", "리뷰상세내용", "상품명", "구매자평점", "상품번호"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        eprint(f"[ERROR] 스마트스토어 엑셀에 필요한 컬럼 없음: {miss}")
        eprint(f"        실제 컬럼: {list(df.columns)}")
        sys.exit(1)

    rows_by_month: dict = {}
    skipped = 0
    for _, r in df.iterrows():
        body = str(r.get("리뷰상세내용") or "").strip()
        rid = clean_int_str(r.get("리뷰글번호"))
        dt, month = parse_dt(r.get("리뷰등록일"))
        if not body or not rid or not month:
            skipped += 1
            continue
        photo = 1 if str(r.get("포토/영상") or "").strip() not in ("", "nan", "None") else 0
        rec = {c: "" for c in CREMA_COLUMNS}     # 기본 전부 빈값
        rec["리뷰ID"] = f"{args.brand_prefix}{rid}"
        rec["리뷰작성일"] = dt
        rec["리뷰본문"] = body
        rec["상품번호"] = clean_int_str(r.get("상품번호"))
        rec["상품명"] = str(r.get("상품명") or "").strip()
        rec["리뷰작성경로"] = CHANNEL_TAG        # ← 채널 태그(핵심)
        rec["리뷰별점"] = clean_int_str(r.get("구매자평점"))
        rec["포토개수"] = photo
        rec["동영상개수"] = 0
        rec["댓글개수"] = 0
        # 주문번호·회원ID·회원명·상품옵션·회원등급·가격 등은 의도적으로 빈값(PII 차단 / SS 미보유)
        rows_by_month.setdefault(month, []).append(rec)

    if skipped:
        eprint(f"  건너뜀(본문/ID/날짜 없음): {skipped}건")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    total = 0
    for month in sorted(rows_by_month):
        out = outdir / f"reviews_ss_{month}.csv"
        mdf = pd.DataFrame(rows_by_month[month], columns=CREMA_COLUMNS)
        mdf.to_csv(out, index=False, encoding="utf-8-sig")
        total += len(mdf)
        eprint(f"  [OK] {month}: {len(mdf)}건 → {out}")
    eprint(f"  완료 — 총 {total}건 / {len(rows_by_month)}개월")


if __name__ == "__main__":
    main()
