"""make_ss_result_file.py — 스마트스토어 분류 결과를 사람이 검토할 한 파일(Excel/CSV)로 정리
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
docs/data/스마트스토어/{월}/ 의 분류 결과(reviews·keywords·pvoc_intent)를 읽어,
SS 리뷰별로 [월·리뷰ID·상품·별점·작성일·감성·키워드분류·PVOC토픽·본문] 한 표로 만든다.

사용:
    python scripts/make_ss_result_file.py --months 2026-03,2026-04,2026-05,2026-06,2026-07 \
        --out 스마트스토어_분류결과.xlsx
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BUCKET_KR = {"complaint": "불만", "improvement": "개선", "praise": "칭찬"}


def build_maps(kdoc, pvdoc):
    """review_id → 키워드분류 문자열 / PVOC 토픽 문자열."""
    kw = {}
    for bucket, kr in BUCKET_KR.items():
        for k in (kdoc.get("by_intent", {}).get(bucket, []) or []):
            for rid in k.get("all_review_ids", []):
                kw.setdefault(str(rid), []).append(f"{kr}:{k.get('word')}")
    pv = {}
    for name, io in (pvdoc.get("topics", {}) if pvdoc else {}).items():
        for rid in io.get("neg", []):
            pv.setdefault(str(rid), []).append(f"{name}(불만)")
        for rid in io.get("pos", []):
            pv.setdefault(str(rid), []).append(f"{name}(만족)")
    return kw, pv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="스마트스토어")
    ap.add_argument("--months", default="2026-03,2026-04,2026-05,2026-06,2026-07")
    ap.add_argument("--out", default="스마트스토어_분류결과.xlsx")
    args = ap.parse_args()
    months = [m.strip() for m in args.months.split(",") if m.strip()]

    rows = []
    for m in months:
        d = ROOT / "docs" / "data" / args.brand / m
        rp = d / "reviews.json"
        if not rp.is_file():
            print(f"  [SKIP] {m}: 분류결과 없음", file=sys.stderr)
            continue
        reviews = json.loads(rp.read_text(encoding="utf-8")).get("reviews", {})
        kdoc = json.loads((d / "keywords.json").read_text(encoding="utf-8")) if (d / "keywords.json").is_file() else {}
        pvdoc = json.loads((d / "pvoc_intent.json").read_text(encoding="utf-8")) if (d / "pvoc_intent.json").is_file() else {}
        kwmap, pvmap = build_maps(kdoc, pvdoc)
        sent_kr = {"positive": "긍정", "negative": "부정", "neutral": "중립"}
        for rid, r in reviews.items():
            rows.append({
                "월": m,
                "리뷰ID": rid,
                "상품": r.get("product", ""),
                "별점": r.get("rating", ""),
                "작성일": r.get("date", ""),
                "감성": sent_kr.get(r.get("sentiment", ""), r.get("sentiment", "")),
                "키워드분류": " / ".join(kwmap.get(str(rid), [])),
                "PVOC토픽": " / ".join(pvmap.get(str(rid), [])),
                "본문": (r.get("text", "") or "").replace("\n", " "),
            })
    if not rows:
        print("생성할 데이터 없음", file=sys.stderr)
        sys.exit(1)
    df = pd.DataFrame(rows, columns=["월", "리뷰ID", "상품", "별점", "작성일", "감성", "키워드분류", "PVOC토픽", "본문"])
    out = Path(args.out)
    try:
        with pd.ExcelWriter(out, engine="openpyxl") as xw:
            df.to_excel(xw, index=False, sheet_name="전체")
            for m in months:
                sub = df[df["월"] == m]
                if len(sub):
                    sub.to_excel(xw, index=False, sheet_name=m)
    except Exception:
        out = out.with_suffix(".csv")
        df.to_csv(out, index=False, encoding="utf-8-sig")
    # 요약
    print(f"[OK] {len(df)}건 → {out}", file=sys.stderr)
    summ = df.groupby(["월", "감성"]).size().unstack(fill_value=0)
    print(summ.to_string(), file=sys.stderr)


if __name__ == "__main__":
    main()
