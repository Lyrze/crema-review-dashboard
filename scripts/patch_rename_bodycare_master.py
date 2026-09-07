"""patch_rename_bodycare_master.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
"허리편한케어 마스터"는 8월부터 "복부 순환 마스터 프리미엄 복부 마사지기"로
제품명만 바뀐 동일 제품(사용자 확인, 2026-09-07). 3~7월에 남아있는
"허리편한케어 마스터" 라벨을 전부 새 이름으로 통일한다(리뷰 자체는 그대로).

사용:
  python scripts/patch_rename_bodycare_master.py --dry-run
  python scripts/patch_rename_bodycare_master.py --apply
"""
import argparse, json, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from purge_bulk_reviews import recompute_products, recompute_keywords, backup  # noqa: E402

DATA_ROOT = ROOT / "docs" / "data" / "슬룸"
OLD_NAME = "허리편한케어 마스터"
NEW_NAME = "복부 순환 마스터 프리미엄 복부 마사지기"
MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]


def eprint(*a, **k):
    print(*a, file=sys.stderr, flush=True, **k)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    for month in MONTHS:
        rpath = DATA_ROOT / month / "reviews.json"
        if not rpath.is_file():
            continue
        rdata = json.loads(rpath.read_text(encoding="utf-8"))
        reviews = rdata["reviews"]
        targets = [rid for rid, rv in reviews.items() if rv.get("product") == OLD_NAME]
        eprint(f"[{month}] '{OLD_NAME}' -> '{NEW_NAME}' 대상 {len(targets)}건")
        if not targets or args.dry_run:
            continue

        for p in (rpath, DATA_ROOT / month / "products.json", DATA_ROOT / month / "keywords.json"):
            if p.is_file():
                backup(p)

        for rid in targets:
            reviews[rid]["product"] = NEW_NAME
            if isinstance(reviews[rid].get("products"), list):
                reviews[rid]["products"] = [NEW_NAME]
        rpath.write_text(json.dumps(rdata, ensure_ascii=False, indent=2), encoding="utf-8")

        # 캐노니컬명(상품 노드)만 바꿔치기 — review_count 등 통계는 그대로 이전
        ppath = DATA_ROOT / month / "products.json"
        pdata = json.loads(ppath.read_text(encoding="utf-8"))
        for p in pdata["products"]:
            if p["name"] == OLD_NAME:
                p["name"] = NEW_NAME
                p["raw_name"] = NEW_NAME
        ppath.write_text(json.dumps(pdata, ensure_ascii=False, indent=2), encoding="utf-8")

        recompute_keywords(month, set())
        eprint(f"  [{month}] products.json/keywords.json 반영 완료")

    if args.dry_run:
        eprint("\n[dry-run] 실제 변경 없음.")
    else:
        eprint("\n[DONE]")


if __name__ == "__main__":
    main()
