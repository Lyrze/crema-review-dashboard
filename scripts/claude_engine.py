"""claude_engine.py — Claude Code CLI(구독 인증) 기반 분석 엔진 어댑터
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
API 키 없이, 로그인된 Claude Code CLI(`claude -p`)를 subprocess로 호출한다.
OllamaAnalyzer 와 동일한 인터페이스(client.generate / verify_keyword_reviews /
analyze_sentiment_batch ...)를 제공하므로, 파이프라인 스크립트에서
`--engine claude` 만 주면 정밀 판정 단계를 Claude로 돌릴 수 있다.

설계:
  - ClaudeClient.generate(model, prompt, system, temperature) → OllamaClient 와 동형
    · 프롬프트는 stdin 으로 전달(따옴표/길이/한글 안전), --model 로 모델 지정
    · 하드 타임아웃 + 지수백오프 재시도
  - ClaudeAnalyzer(OllamaAnalyzer) → self.client 만 ClaudeClient 로 교체,
    verify_keyword_reviews / analyze_sentiment_batch 등 상위 로직은 그대로 상속

주의: 팀 구독은 사용량 한도가 있으므로 대량(감성 전수)보다 '판사' 단계에 쓰는 것을 권장.
"""
import os
import shutil
import subprocess
import time
import logging

logger = logging.getLogger(__name__)

_QUOTA_SIGNALS = ("usage limit", "session limit", "rate limit", "quota", "limit reached",
                  "too many requests", "429", "overloaded")


def _looks_like_quota(text) -> bool:
    m = str(text).lower()
    return any(k in m for k in _QUOTA_SIGNALS)

# OllamaAnalyzer 상속 (동일 디렉터리)
try:
    from ollama_analysis import OllamaAnalyzer
except Exception:  # 단독 import 대비
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ollama_analysis import OllamaAnalyzer


def _find_claude():
    """claude 실행 파일 경로 탐색 (PATH → npm 전역 → 흔한 위치)."""
    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if exe:
        return exe
    cands = [
        os.path.expandvars(r"%APPDATA%\npm\claude.cmd"),
        os.path.expandvars(r"%APPDATA%\npm\claude"),
        os.path.expanduser(r"~/.local/bin/claude"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return "claude"  # 최후: PATH에 있길 기대


class ClaudeClient:
    """OllamaClient 와 동형의 generate() 를 제공하는 Claude CLI 래퍼."""

    CIRCUIT_THRESHOLD = 3  # 연속 완전실패(재시도 다 소진) 이 횟수 이상이면 회로 차단
                           # (2는 콜드스타트/일시 타임아웃 2회에 과민 개방 → 3으로 상향)

    def __init__(self, timeout: int = 90):
        self.exe = _find_claude()
        self.timeout = timeout
        self.fail_count = 0     # 누적 호출 실패 수 (한도/과부하 감지 — 재검증 이어받기 판단용)
        self.consec_fail = 0    # 연속 완전실패 카운터 (성공 시 리셋)
        self._circuit_open = False  # True면 실제 호출 없이 즉시 실패 (텍스트 패턴에 안 잡히는
                                     # rc=1/빈 stderr·연속 타임아웃 등 '조용한' 한도 소진 방지)
        self._quota_seen = False    # 실패 중 한도(quota) 신호를 실제로 관측했는가
                                     # (회로 차단 시 quota vs 비한도 실패를 구분해 상위에 알림)
        # npm 전역 경로를 PATH 에 보강 (subprocess 상속 환경)
        self._env = dict(os.environ)
        npm = os.path.expandvars(r"%APPDATA%\npm")
        if npm not in self._env.get("PATH", ""):
            self._env["PATH"] = self._env.get("PATH", "") + os.pathsep + npm
        self._env["PYTHONUTF8"] = "1"

    def generate(self, model: str, prompt: str, system: str = "",
                 temperature: float = 0.1, max_retries: int = 3) -> str:
        if self._circuit_open:
            # 이미 연속실패로 회로가 열림 — 실제 subprocess 호출 없이 즉시 실패.
            # ★ fail_count 를 반드시 증가시킨다: reverify_suspect 는 fail_count 델타(df)로 실패를
            #   감지하는데, 여기서 안 올리면 회로개방 후 모든 키워드가 df=0(=성공)으로 보여
            #   미검증 멤버를 '완료'로 마킹→영구 스킵하는 '조용한 거짓 완료' 버그가 생긴다(2026-07-14 리뷰).
            self.fail_count += 1
            self.consec_fail += 1
            # 한도(quota)를 실제로 봤을 때만 'quota' 표기 → 상위가 exit 3(리셋 후 재시도)로 처리.
            # 비한도 연속실패(타임아웃 등)는 quota 표기 안 함 → 상위가 사람 확인 경로로 중단.
            if self._quota_seen:
                raise RuntimeError("claude 호출 회로 차단 — quota(한도) 소진 추정. 리셋 후 재시도")
            raise RuntimeError("claude 호출 회로 차단 — 연속 비한도 실패, 사람 확인 필요")
        # system 은 프롬프트 앞에 결합 (CLI -p 는 단일 프롬프트)
        full = (system.strip() + "\n\n" + prompt) if system else prompt
        args = [self.exe, "-p", "--model", (model or "sonnet")]
        last = None
        for attempt in range(max_retries):
            try:
                r = subprocess.run(
                    args, input=full, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", env=self._env,
                    timeout=self.timeout,
                    shell=self.exe.lower().endswith(".cmd"),  # .cmd 는 shell 경유
                )
                if r.returncode == 0 and (r.stdout or "").strip():
                    self.consec_fail = 0
                    self._quota_seen = False   # 성공 → quota 관측 플래그 리셋(스티키 오보 방지)
                    return r.stdout.strip()
                err = (r.stderr or "")[:200]
                out = (r.stdout or "")[:200]
                # 한도 소진 메시지는 stderr가 아니라 stdout으로 나옴 (예: "You've hit your
                # session limit · resets 8:20pm") — stderr·stdout 둘 다 메시지에 담아
                # 상위(reverify_suspect/ollama_analysis)의 한도 감지가 반드시 잡도록 한다.
                last = RuntimeError(f"claude rc={r.returncode}: {(err + ' ' + out).strip()}")
                if _looks_like_quota(err) or _looks_like_quota(out):
                    self._quota_seen = True
                    break  # 한도 소진 — 재시도로 낭비하지 않고 즉시 실패 처리
            except subprocess.TimeoutExpired as e:
                last = e
                logger.warning("claude 타임아웃 (시도 %d/%d)", attempt + 1, max_retries)
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(2 ** attempt)
        self.fail_count += 1    # 재시도까지 모두 실패 (rc=1/타임아웃/한도 등)
        self.consec_fail += 1
        if self.consec_fail >= self.CIRCUIT_THRESHOLD:
            self._circuit_open = True  # 이후 호출은 subprocess 없이 즉시 실패
        raise RuntimeError(f"claude 호출 실패: {last}")


class ClaudeAnalyzer(OllamaAnalyzer):
    """OllamaAnalyzer 의 모든 로직을 상속하되, 백엔드만 Claude CLI 로 교체."""

    def __init__(self, model: str = "sonnet", timeout: int = 90, **_ignored):
        # base_url 등 Ollama 전용 인자는 무시 (호환용)
        self.model = model or "sonnet"
        self.client = ClaudeClient(timeout=timeout)
        self.last_error = None  # health_check 실패 시 원인 메시지 (호출측의 한도감지용)

    def health_check(self) -> bool:
        try:
            out = self.client.generate(self.model, "핑. 한 글자로만 답: 'ok'", temperature=0.0)
            logger.info("Claude CLI 정상, 모델: %s", self.model)
            self.last_error = None
            return bool(out)
        except Exception as e:  # noqa: BLE001
            logger.error("Claude CLI 응답 없음: %s", e)
            self.last_error = str(e)
            return False


def make_analyzer(engine: str, model: str = None, base_url: str = "http://localhost:11434"):
    """엔진 팩토리 — 스크립트에서 공용으로 사용.
    engine='claude' → ClaudeAnalyzer(model 기본 sonnet)
    그 외          → OllamaAnalyzer(model 기본 qwen2.5:14b 등 호출측 지정)
    """
    if str(engine).lower() == "claude":
        return ClaudeAnalyzer(model=model or "sonnet")
    return OllamaAnalyzer(model=model or "qwen2.5:14b", base_url=base_url)


if __name__ == "__main__":  # 간이 자가진단
    a = ClaudeAnalyzer(model="sonnet")
    print("health:", a.health_check())
    ok = a.verify_keyword_reviews(
        "가격/가성비", "부정",
        [{"review_id": "t1", "text": "가격은 괜찮은데 소음이 커서 불편", "rating": 3},
         {"review_id": "t2", "text": "이 가격에 이 성능이면 너무 비싸다", "rating": 2}],
        mode="batch",
    )
    print("kept:", [s.get("review_id") for s in ok], "(t2만 남아야 정상)")
