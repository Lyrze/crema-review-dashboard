"""
anonymize_csv.py
~~~~~~~~~~~~~~~~
크리마 리뷰 CSV에서 개인식별정보(PII)를 제거한 익명화 파일을 생성합니다.

처리 방식:
  - 회원ID  → SHA-256 해시 '사용자_익명ID' 컬럼으로 변환
             (같은 회원 = 항상 같은 해시 → 월 간 추적 가능, 역추적 불가)
  - 회원명, 주문번호 등 나머지 PII → 완전 제거

유지 컬럼 (AI 분석 + 집계):
  사용자_익명ID, 리뷰ID, 리뷰작성일, 상품구매일, 배송완료일, 리뷰본문,
  회원등급, 상품번호, 상품명, 상품가격, 상품옵션,
  리뷰작성경로, 리뷰별점, 태그, 포토개수, 동영상개수, 댓글개수, 댓글내용

Usage:
    python scripts/anonymize_csv.py \\
        --input  data/raw/슬룸/2026-03/reviews.csv \\
        --output data/anonymized/슬룸/2026-03/reviews_anon.csv
"""

import argparse
import sys
import csv
import hashlib
from pathlib import Path

# 완전 제거할 컬럼 (개인정보)
DROP_COLUMNS = {
    "리뷰code",
    "주문번호",
    "회원명",
    "추가수집정보",
    "적립금",
    "적립금지급일",
    "포토1_url", "포토2_url", "포토3_url", "포토4_url",
    "동영상1_url", "동영상2_url", "동영상3_url", "동영상4_url",
}

# 해시 솔트: 동일 솔트 = 월이 달라도 같은 사용자 = 같은 익명 ID
HASH_SALT = "crema-anon-v1"


def hash_user_id(raw_id: str) -> str:
    """회원ID → 12자리 익명 ID (역추적 불가, 동일인 추적 가능)"""
    if not raw_id:
        return ""
    return hashlib.sha256(f"{HASH_SALT}:{raw_id}".encode()).hexdigest()[:12]


def detect_encoding(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            with open(path, encoding=enc) as f:
                f.read(4096)
            return enc
        except (UnicodeDecodeError, Exception):
            continue
    raise ValueError(f"CSV 인코딩 감지 실패: {path}")


def anonymize(input_path: str, output_path: str) -> dict:
    inp = Path(input_path)
    out = Path(output_path)

    if not inp.exists():
        raise FileNotFoundError(f"입력 파일 없음: {inp}")

    out.parent.mkdir(parents=True, exist_ok=True)
    encoding = detect_encoding(inp)

    rows_out = []
    out_headers = None
    dropped = []

    with open(inp, encoding=encoding, newline="") as fin:
        reader = csv.DictReader(fin)
        raw_headers = reader.fieldnames or []
        headers = [h.lstrip("﻿") for h in raw_headers]  # BOM 제거

        # 출력 컬럼 구성
        out_headers = []
        for h in headers:
            if h == "회원ID":
                out_headers.append("사용자_익명ID")   # 해시로 대체
            elif h in DROP_COLUMNS:
                dropped.append(h)
            else:
                out_headers.append(h)

        for row in reader:
            clean = {k.lstrip("﻿"): v for k, v in row.items()}
            out_row = {}
            for h in headers:
                if h in DROP_COLUMNS:
                    continue
                if h == "회원ID":
                    out_row["사용자_익명ID"] = hash_user_id(clean.get(h, ""))
                else:
                    out_row[h] = clean.get(h, "")
            rows_out.append(out_row)

    with open(out, "w", encoding="utf-8-sig", newline="") as fout:
        writer = csv.DictWriter(fout, fieldnames=out_headers)
        writer.writeheader()
        writer.writerows(rows_out)

    return {
        "rows": len(rows_out),
        "kept": len(out_headers),
        "dropped": dropped,
        "output": str(out),
    }


def main():
    parser = argparse.ArgumentParser(description="크리마 CSV PII 익명화")
    parser.add_argument("--input",  required=True, help="원본 CSV 경로")
    parser.add_argument("--output", required=True, help="출력 CSV 경로")
    args = parser.parse_args()

    try:
        s = anonymize(args.input, args.output)
        print(f"[OK] 익명화 완료: {s['rows']:,}건 | 유지 {s['kept']}컬럼 | 제거: {', '.join(s['dropped'])}")
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
