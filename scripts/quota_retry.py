"""quota_retry.py — 임의 명령을 '한도(quota) 리셋 대기 → 재시도'로 감싸는 범용 래퍼
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
자식 명령의 종료코드 계약(reverify_suspect / classify_unclassified 와 동일):
  0 = 완료 → 래퍼도 0으로 종료
  3 = 한도 소진(출력에 "resets HH:MMam/pm" 포함) → 리셋 시각까지 대기 후 같은 명령 재시도
  그 외 = 비한도 실패 → 자동 재시도 없이 해당 코드로 즉시 종료(사람 확인)

사용:
  python scripts/quota_retry.py -- python scripts/classify_unclassified.py --brand 슬룸 --month 2026-05 --engine claude
"""
import subprocess
import sys
import time
from datetime import datetime, timedelta

from auto_reverify_loop import parse_reset_time  # 동일 디렉터리 — resets 시각 파서 재사용

MAX_RETRIES = 8
MAX_TOTAL_SECONDS = 24 * 3600
SLEEP_CAP = 6 * 3600 + 300   # 오파싱으로 익일(+24h)이 잡혀도 최대 6시간만 대기


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        log("사용법: python quota_retry.py -- <명령...>")
        sys.exit(1)
    start = time.time()
    for attempt in range(1, MAX_RETRIES + 1):
        if time.time() - start > MAX_TOTAL_SECONDS:
            log("최대 누적 대기 시간(24시간) 초과 — 중단")
            sys.exit(1)
        log(f"[시도 {attempt}/{MAX_RETRIES}] 실행: {' '.join(argv)}")
        r = subprocess.run(argv, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        print(out, flush=True)
        if r.returncode == 0:
            log("완료")
            sys.exit(0)
        if r.returncode == 3:
            target = parse_reset_time(out)
            if not target:
                log("한도 소진 감지됐으나 리셋 시각 파싱 실패 — 30분 후 재시도")
                target = datetime.now() + timedelta(minutes=30)
            wait_s = max(60, (target - datetime.now()).total_seconds() + 120)
            wait_s = min(wait_s, SLEEP_CAP)
            log(f"한도 소진 — {target.strftime('%H:%M')} 리셋 예상, {int(wait_s // 60)}분 대기 후 재시도")
            time.sleep(wait_s)
            continue
        log(f"비한도 종료코드 {r.returncode} — 자동재시도 중단(사람 확인 필요)")
        sys.exit(r.returncode)
    log("최대 재시도 횟수 초과 — 중단")
    sys.exit(1)


if __name__ == "__main__":
    main()
