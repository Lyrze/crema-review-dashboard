"""auto_reverify_loop.py — Claude 세션 한도(quota) 도달 시 리셋 시각까지 자동 대기 후 재시도
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
사람이 "지금 한도 풀렸나?" 를 수동으로 확인해 재실행할 필요 없이, reverify_suspect.py 가
완료(exit 0)될 때까지 리셋 시각을 자동 파싱해 대기 → 재시도를 반복한다.

배경(과거 실수 — CLAUDE.md 참고):
  Claude Code의 예약작업(scheduled-tasks MCP, create_scheduled_task/update_scheduled_task)은
  무인 컨텍스트에서 첫 Bash tool_use 승인 대기 상태로 멈춰버리는 문제가 두 번 반복 관측됨
  (2026-07-09 20:25, 2026-07-10 02:40 — 둘 다 Bash 명령을 큐에 올린 후 무응답으로 정지,
  session-notification은 "완료"로 잘못 표시됨). LLM 세션·도구승인이 필요 없는 이 스크립트는
  일반 OS 프로세스 루프로만 동작하므로 그 문제를 원천적으로 피한다.

사용:
    python scripts/auto_reverify_loop.py --brand 슬룸 --months 2026-04,2026-05,2026-03 --engine claude
    (뒤에 붙는 인자는 reverify_suspect.py 에 그대로 전달됨)

동작:
  1. reverify_suspect.py 를 실행
  2. exit 0(전체 완료) → 종료
  3. exit 3(한도 소진) → stdout/stderr에서 "resets HH:MMam/pm" 파싱 → 그 시각(+2분 여유)까지
     sleep → 재시도. 파싱 실패 시 30분 후 재시도.
  4. 그 외 종료코드 → 자동재시도 없이 즉시 중단(로그인 문제 등 사람 개입 필요)
  5. 안전장치: 최대 8회 재시도 / 최대 24시간 누적 대기 넘으면 중단
"""
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_RETRIES = 8
MAX_TOTAL_SECONDS = 24 * 3600
RESET_RE = re.compile(r"resets\s+(\d{1,2}):(\d{2})\s*(am|pm)", re.IGNORECASE)


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def parse_reset_time(text):
    """'resets 12:30pm' 등에서 다음 도래 시각(datetime)을 계산. 이미 지난 시각이면 내일로."""
    m = RESET_RE.search(text)
    if not m:
        return None
    hh, mm, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ap == "pm" and hh != 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def main():
    argv = sys.argv[1:]
    if not argv:
        log("사용법: python auto_reverify_loop.py <reverify_suspect.py 인자...>")
        sys.exit(1)
    cmd = [sys.executable, str(ROOT / "scripts" / "reverify_suspect.py")] + argv
    start = time.time()
    for attempt in range(1, MAX_RETRIES + 1):
        if time.time() - start > MAX_TOTAL_SECONDS:
            log("최대 누적 대기 시간(24시간) 초과 — 중단")
            sys.exit(1)
        log(f"[시도 {attempt}/{MAX_RETRIES}] 실행: {' '.join(cmd)}")
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        print(out, flush=True)
        if r.returncode == 0:
            log("완료 — 전체 재검증 종료")
            sys.exit(0)
        if r.returncode == 3:
            target = parse_reset_time(out)
            if not target:
                log("한도 소진 감지됐으나 리셋 시각 파싱 실패 — 30분 후 재시도")
                target = datetime.now() + timedelta(minutes=30)
            wait_s = max(60, (target - datetime.now()).total_seconds() + 120)  # 2분 여유
            # 상한 6시간: 시계오차 등으로 리셋시각이 과거로 파싱돼 '익일(+24h)'로 잡히면
            # 한 번에 최대 24h를 자버리는 사고 방지. quota 리셋은 통상 수 시간 내이므로
            # 일찍 깨어 재확인해도 손해 없음(다시 quota면 새 리셋시각으로 재대기).
            wait_s = min(wait_s, 6 * 3600 + 300)
            log(f"한도 소진 — {target.strftime('%H:%M')} 리셋 예상, {int(wait_s // 60)}분 대기 후 재시도")
            time.sleep(wait_s)
            continue
        log(f"예상치 못한 종료코드 {r.returncode} — 자동재시도 중단(사람 확인 필요)")
        sys.exit(r.returncode)
    log("최대 재시도 횟수 초과 — 중단")
    sys.exit(1)


if __name__ == "__main__":
    main()
