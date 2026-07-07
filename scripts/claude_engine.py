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

    def __init__(self, timeout: int = 90):
        self.exe = _find_claude()
        self.timeout = timeout
        # npm 전역 경로를 PATH 에 보강 (subprocess 상속 환경)
        self._env = dict(os.environ)
        npm = os.path.expandvars(r"%APPDATA%\npm")
        if npm not in self._env.get("PATH", ""):
            self._env["PATH"] = self._env.get("PATH", "") + os.pathsep + npm
        self._env["PYTHONUTF8"] = "1"

    def generate(self, model: str, prompt: str, system: str = "",
                 temperature: float = 0.1, max_retries: int = 3) -> str:
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
                    return r.stdout.strip()
                last = RuntimeError(f"claude rc={r.returncode}: {(r.stderr or '')[:200]}")
            except subprocess.TimeoutExpired as e:
                last = e
                logger.warning("claude 타임아웃 (시도 %d/%d)", attempt + 1, max_retries)
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(2 ** attempt)
        raise RuntimeError(f"claude 호출 실패: {last}")


class ClaudeAnalyzer(OllamaAnalyzer):
    """OllamaAnalyzer 의 모든 로직을 상속하되, 백엔드만 Claude CLI 로 교체."""

    def __init__(self, model: str = "sonnet", timeout: int = 90, **_ignored):
        # base_url 등 Ollama 전용 인자는 무시 (호환용)
        self.model = model or "sonnet"
        self.client = ClaudeClient(timeout=timeout)

    def health_check(self) -> bool:
        try:
            out = self.client.generate(self.model, "핑. 한 글자로만 답: 'ok'", temperature=0.0)
            logger.info("Claude CLI 정상, 모델: %s", self.model)
            return bool(out)
        except Exception as e:  # noqa: BLE001
            logger.error("Claude CLI 응답 없음: %s", e)
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
