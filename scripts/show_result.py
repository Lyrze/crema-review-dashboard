"""
show_result.py  —  처리 결과 요약 출력
Usage: python scripts/show_result.py <brand> <month>
"""
import json, sys

brand = sys.argv[1] if len(sys.argv) > 1 else ""
month = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    path = f"docs/data/{brand}/{month}/summary.json"
    d = json.load(open(path, encoding="utf-8"))
    k = d["kpis"]
    print(f"  리뷰 수  : {k.get('total_reviews', 0):,}건")
    print(f"  평균 별점: {k.get('avg_rating', 0):.2f}")
    print(f"  긍정률   : {k.get('positive_rate', 0):.1f}%")
except Exception as e:
    print(f"  (결과 확인 실패: {e})")
