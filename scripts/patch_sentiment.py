"""patch_sentiment.py <YYYY-MM> [브랜드]
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
reviews.json 의 리뷰별 감성을 '안전하게' 채우거나 재처리한다.

대상(재처리할 리뷰):
  1) 감성 라벨이 아직 없는 리뷰
  2) 타임아웃으로 별점 폴백된 리뷰 (sentiment_src == "rating")  ← 나중에 AI로 업그레이드

특징:
  - 배치당 하드 타임아웃(45s): Ollama 가 hang 해도 즉시 빠져나옴(무한 멈춤 방지).
  - 진행분 즉시 저장(이어받기): 재실행하면 남은 것만 이어서.
  - 성공 시 sentiment_src('rating') 표시 제거 → 진짜 AI 감성으로 승격.
  - keywords/summary/products 는 건드리지 않음(reviews.json sentiment 만 패치).
    ※ 집계(KPI)는 다음 전체 파이프라인 실행 때 반영됨.

사용:
  python scripts/patch_sentiment.py 2026-05
  python scripts/patch_sentiment.py 2026-05 슬룸
"""
import sys, json, time, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT); os.environ["PYTHONUTF8"] = "1"
sys.path.insert(0, str(ROOT / "scripts"))
from ollama_analysis import OllamaAnalyzer  # noqa: E402

month = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
brand = sys.argv[2] if len(sys.argv) > 2 else "슬룸"
model = "qwen2.5:7b"
P = ROOT / f"docs/data/{brand}/{month}/reviews.json"
if not P.is_file():
    print(f"[ERROR] {P} 없음"); sys.exit(1)
data = json.loads(P.read_text(encoding="utf-8"))
reviews = data.get("reviews", {})


def needs(r):
    """재처리 대상: 감성 없음 OR 별점 폴백된 것. (본문 있는 경우만)"""
    if not (r.get("text") or "").strip():
        return False
    return (not r.get("sentiment")) or (r.get("sentiment_src") == "rating")


def save():
    P.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


todo = [(rid, r) for rid, r in reviews.items() if needs(r)]
total = len(reviews)
print(f"{brand}/{month}: 재처리 대상 {len(todo)} / 전체 {total}", flush=True)
if not todo:
    print("재처리할 건 없음 (모두 AI 감성 보유)"); sys.exit(0)

an = OllamaAnalyzer(model=model)
if not an.health_check():
    print("[ERROR] Ollama 무응답 — 종료(나중에 재실행)"); sys.exit(2)

B = 5; consec = 0; done = 0; t0 = time.time()
ex = ThreadPoolExecutor(max_workers=1)
i = 0
while i < len(todo):
    chunk = todo[i:i + B]
    fut = ex.submit(an.analyze_sentiment_batch, [r.get("text", "") for _, r in chunk])
    try:
        res = fut.result(timeout=45)
        for (rid, r), sr in zip(chunk, res):
            s = (sr.get("sentiment") or "neutral")
            r["sentiment"] = s if s in ("positive", "neutral", "negative") else "neutral"
            r.pop("sentiment_src", None)   # 폴백 표시 제거 → 진짜 AI 감성으로 승격
        consec = 0; done += len(chunk); i += B
        if (i // B) % 15 == 0:
            save(); print(f"  진행 {done}/{len(todo)} ({round(done/len(todo)*100)}%) {round(time.time()-t0)}s", flush=True)
    except FTimeout:
        consec += 1; save()
        print(f"  [TIMEOUT] batch@{i} 연속 {consec} — 저장됨", flush=True)
        try: ex.shutdown(wait=False, cancel_futures=True)
        except Exception: pass
        ex = ThreadPoolExecutor(max_workers=1)
        try: an = OllamaAnalyzer(model=model)
        except Exception: pass
        if consec >= 4:
            print("  연속 타임아웃 4회 → Ollama 멈춤 추정. 저장 후 종료(재실행으로 이어받기).", flush=True)
            break
        time.sleep(3)
    except Exception as e:
        save(); print(f"  [ERR] {str(e)[:140]} — 저장 후 종료", flush=True); break

save()
left = sum(1 for rid, r in reviews.items() if needs(r))
print(f"{month} 종료: 이번 처리 {done} · 남은 {left} · {round(time.time()-t0)}s", flush=True)
sys.exit(0 if left == 0 else 3)
