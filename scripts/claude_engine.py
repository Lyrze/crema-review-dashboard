"""claude_engine.py — Claude Code CLI(구독 인증) 기반 리뷰 분석 엔진
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
API 키 없이, 로그인된 Claude Code CLI(`claude -p`)를 subprocess로 호출해 리뷰
감성분석·키워드추출·3단계 검증(관련성/의도분류/반대신문)·Smart Brief 등을 수행한다.

이 프로젝트는 과거 Ollama(로컬 GPU)와 Claude 중 엔진을 고를 수 있었으나, GPU가
약한 PC에서도 똑같이 쓸 수 있도록 Claude 단일 엔진으로 통합했다(2026-07-24).
이전 Ollama 지원 코드는 backup/pre-ollama-removal-2026-07-24 브랜치에 보존돼 있다.

설계:
  - ClaudeClient.generate(model, prompt, system, temperature) — 프롬프트는 stdin으로
    전달(따옴표/길이/한글 안전), --model 로 모델 지정. 하드 타임아웃 + 지수백오프 재시도 +
    회로차단(연속 실패 시 조용한 거짓 완료 방지).
  - ClaudeAnalyzer — 감성분석/키워드추출/3단계 검증/Smart Brief 등 분석 로직 전담.
    self.client.generate(...) 호출 하나로 통일돼 있어 나중에 다른 엔진이 필요해지면
    self.client 만 교체하면 된다.

주의: 구독 한도가 있으므로 대량 호출은 quota_retry.py 로 감싸서 실행할 것.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ClaudeClient",
    "ClaudeAnalyzer",
    "is_quota",
    "extract_json_from_response",
    "parse_sentiment_response",
]

_QUOTA_SIGNALS = ("usage limit", "session limit", "rate limit", "quota", "limit reached",
                  "too many requests", "429", "overloaded")


def is_quota(text) -> bool:
    """한도 소진/과부하성 오류인지 판정 (Claude CLI 세션 한도 등).
    이런 오류는 '보존하고 계속'이 아니라 즉시 상위로 전파해야 남은 항목에서
    동일 오류로 재시도를 반복해 호출을 낭비하지 않는다."""
    return any(k in str(text).lower() for k in _QUOTA_SIGNALS)


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
    """Claude Code CLI(`claude -p`) subprocess 래퍼."""

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
                # 상위(reverify_suspect 등)의 한도 감지가 반드시 잡도록 한다.
                last = RuntimeError(f"claude rc={r.returncode}: {(err + ' ' + out).strip()}")
                if is_quota(err) or is_quota(out):
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


# ────────────────────────────────────────────
# JSON 파싱 헬퍼
# ────────────────────────────────────────────

_MD_CODE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```",
    re.DOTALL,
)


def extract_json_from_response(text: str) -> Any:
    """
    LLM 응답에서 JSON 블록을 추출한다.
    마크다운 코드 블록(```json ... ```) 또는 순수 JSON 모두 처리.
    None 반환 시 파싱 실패를 의미한다.
    """
    if not text:
        return None

    # 1) 마크다운 코드 블록
    match = _MD_CODE_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 2) 중괄호/대괄호 블록 직접 추출 (가장 바깥쪽)
    #    중괄호 우선: {"matched":[...]} 같은 응답에서 내부 배열만 뽑히는 것 방지
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start: end + 1])
            except json.JSONDecodeError:
                continue

    # 3) 전체 텍스트 파싱 시도
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return None


# ────────────────────────────────────────────
# 감성 분석
# ────────────────────────────────────────────

SENTIMENT_SYSTEM = """당신은 한국어 쇼핑몰 리뷰 감성 분석 전문가입니다.
헬스케어/마사지기 제품 리뷰를 분석하여 감성을 분류합니다.
반드시 JSON 형식으로만 응답하고, 다른 텍스트는 출력하지 마세요."""

SENTIMENT_BATCH_TEMPLATE = """다음 {count}개의 한국어 리뷰를 감성 분석해주세요.

리뷰 목록:
{reviews}

각 리뷰에 대해 다음 JSON 배열 형식으로 응답하세요:
[
  {{
    "index": 0,
    "sentiment": "positive",
    "score": 0.9,
    "reason": "만족 표현과 긍정적 경험 서술"
  }},
  ...
]

sentiment 값:
- "positive": 만족, 칭찬, 추천 등 긍정적 표현
- "neutral": 사실적 서술, 보통, 애매한 표현
- "negative": 불만, 문제, 반품 요청 등 부정적 표현

score: 0.0(매우 부정) ~ 1.0(매우 긍정) 사이의 실수

반드시 {count}개의 항목을 순서대로 JSON 배열로만 응답하세요."""

_VALID_SENTIMENTS: frozenset = frozenset({"positive", "neutral", "negative"})
_NEUTRAL_FALLBACK: dict = {"sentiment": "neutral", "score": 0.5, "reason": "파싱 실패"}
_EMPTY_FALLBACK: dict = {"sentiment": "neutral", "score": 0.5, "reason": "빈 텍스트"}


def parse_sentiment_response(raw: str, batch_size: int) -> List[dict]:
    """
    LLM 감성 분석 응답을 파싱하여 배치 크기에 맞게 반환.
    파싱 실패 또는 유효하지 않은 값은 neutral fallback 처리.
    """
    parsed = extract_json_from_response(raw)
    if not isinstance(parsed, list):
        logger.debug("감성 응답 파싱 실패 (배치크기 %d), fallback 적용", batch_size)
        return [dict(_NEUTRAL_FALLBACK)] * batch_size

    result: List[Optional[dict]] = [None] * batch_size
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("index", -1)
        if not isinstance(idx, int) or not (0 <= idx < batch_size):
            continue
        sentiment = item.get("sentiment", "neutral")
        if sentiment not in _VALID_SENTIMENTS:
            sentiment = "neutral"
        result[idx] = {
            "sentiment": sentiment,
            "score": float(min(max(item.get("score", 0.5), 0.0), 1.0)),  # 클램프
            "reason": str(item.get("reason", "")),
        }

    # None 자리를 fallback으로 채움
    return [r if r is not None else dict(_NEUTRAL_FALLBACK) for r in result]


# ────────────────────────────────────────────
# 키워드 추출
# ────────────────────────────────────────────

KEYWORD_SYSTEM = """당신은 한국어 텍스트 분석 전문가입니다.
제품 리뷰에서 핵심 키워드를 추출하고 의도별로 분류합니다.
반드시 JSON 형식으로만 응답하세요."""

KEYWORD_TEMPLATE = """다음은 마사지기 제품의 리뷰 {count}개입니다.

리뷰 내용:
{reviews}

위 리뷰들에서 자주 언급되는 핵심 키워드를 추출하고, 다음 3가지 카테고리로 분류해주세요:

1. praise (칭찬): 제품의 장점, 좋은 점, 만족 요소
2. complaint (불만): 제품의 단점, 문제점, 불만 사항
3. improvement (개선요청): 개선되었으면 하는 점, 요청사항

각 카테고리별로 상위 {top_n}개 키워드를 다음 JSON 형식으로 응답하세요:
{{
  "praise": [
    {{"word": "키워드", "count": 예상빈도, "context": "짧은 맥락 설명"}},
    ...
  ],
  "complaint": [...],
  "improvement": [...]
}}

키워드는 1~4어절의 한국어 명사구 또는 형용사구로 추출하세요."""


# ────────────────────────────────────────────
# Smart Brief 생성
# ────────────────────────────────────────────

BRIEF_SYSTEM = """당신은 한국어 이커머스 리뷰 분석 전문가입니다.
데이터를 바탕으로 브랜드 매니저가 빠르게 읽을 수 있는 인사이트 요약을 작성합니다.
명확하고 실행 가능한 한국어 문장으로 작성하세요."""

PRODUCT_BRIEF_TEMPLATE = """다음은 '{product_name}' 제품의 리뷰 분석 데이터입니다.

평균 별점: {avg_rating}/5.0
감성 분포: 긍정 {positive}개 / 중립 {neutral}개 / 부정 {negative}개

리뷰 샘플 ({count}개):
{reviews}

위 데이터를 바탕으로 다음 JSON 형식으로 상품 인사이트를 작성해주세요:
{{
  "brief": "2~3문장의 핵심 요약 (강점과 주요 이슈 포함)",
  "key_insights": [
    "핵심 인사이트 1 (구체적인 수치나 예시 포함)",
    "핵심 인사이트 2",
    "핵심 인사이트 3"
  ]
}}"""

SMART_BRIEF_TEMPLATE = """다음은 '{brand}' 브랜드의 {month} 리뷰 분석 요약입니다.

[전체 KPI]
- 총 리뷰수: {total_reviews}건
- 평균 별점: {avg_rating}/5.0
- 긍정 비율: {positive_rate}%
- 부정 비율: {negative_rate}%
- 포토리뷰 비율: {photo_review_rate}%
{mom_section}

[상위 5개 상품]
{top_products}

[주요 부정 키워드 Top 10]
{neg_keywords}

위 데이터를 바탕으로 브랜드 매니저를 위한 월간 리뷰 Smart Brief를 한국어로 작성해주세요.
- 이번 달 전반적인 리뷰 품질 평가
- 주목해야 할 상품 (긍정/부정 양면)
- 고객 불만 패턴과 즉시 대응이 필요한 이슈
- 다음 달 개선을 위한 실행 제안 1~2개

4~6문장의 자연스러운 한국어 문단으로 작성하세요 (JSON 불필요)."""


def _make_fallback_brief(brand: str, month: str, kpis: dict) -> str:
    """AI 생성 실패 시 KPI 기반 기본 요약문을 반환한다."""
    total = kpis.get("total_reviews", 0)
    avg = kpis.get("avg_rating", 0.0)
    pos_rate = kpis.get("positive_rate", 0.0)
    neg_rate = kpis.get("negative_rate", 0.0)
    return (
        f"{brand} 브랜드 {month} 월간 리뷰 요약: "
        f"총 {total}건의 리뷰가 수집되었으며 평균 별점은 {avg}점입니다. "
        f"긍정 비율 {pos_rate}%, 부정 비율 {neg_rate}%로 집계되었습니다. "
        f"(AI 브리프 생성 불가 — 수동 확인 필요)"
    )


# ────────────────────────────────────────────
# ClaudeAnalyzer — 리뷰 분석 메인 클래스
# ────────────────────────────────────────────

class ClaudeAnalyzer:
    """
    Claude 기반 리뷰 분석기.
    감성 분석, 키워드 추출, 3단계 검증(관련성/의도분류/반대신문), Smart Brief 생성을 담당한다.
    """

    _MAX_TEXT_LEN: int = 500    # 개별 텍스트 최대 길이 (토큰 절약)
    _MAX_KEYWORD_SAMPLE: int = 100  # 키워드 추출 최대 샘플 수
    _MAX_BRIEF_SAMPLE: int = 20     # 브리프 생성 최대 샘플 수

    def __init__(self, model: str = "sonnet", timeout: int = 90, **_ignored):
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

    # ── 감성 분석 ──

    def analyze_sentiment_batch(self, texts: List[str]) -> List[dict]:
        """
        리뷰 텍스트 배치를 감성 분석한다.

        반환값: [{"sentiment": str, "score": float, "reason": str}, ...]
        """
        if not texts:
            return []

        # 유효 / 빈 텍스트 분리
        valid_indices: List[int] = []
        valid_texts: List[str] = []
        for i, text in enumerate(texts):
            stripped = text.strip() if isinstance(text, str) else ""
            if stripped:
                valid_indices.append(i)
                valid_texts.append(stripped[: self._MAX_TEXT_LEN])

        if not valid_texts:
            return [dict(_EMPTY_FALLBACK)] * len(texts)

        reviews_formatted = "\n".join(
            f"[{i}] {t}" for i, t in enumerate(valid_texts)
        )
        prompt = SENTIMENT_BATCH_TEMPLATE.format(
            count=len(valid_texts),
            reviews=reviews_formatted,
        )

        try:
            raw = self.client.generate(
                model=self.model,
                prompt=prompt,
                system=SENTIMENT_SYSTEM,
                temperature=0.05,
            )
            parsed = parse_sentiment_response(raw, len(valid_texts))
        except RuntimeError as exc:
            logger.error("감성 분석 실패: %s", exc)
            parsed = [dict(_NEUTRAL_FALLBACK)] * len(valid_texts)

        # 원본 인덱스 순서로 재조립
        full_results: List[dict] = [dict(_EMPTY_FALLBACK)] * len(texts)
        for vi, oi in enumerate(valid_indices):
            if vi < len(parsed):
                full_results[oi] = parsed[vi]

        return full_results

    def analyze_sentiment_all(
        self, texts: List[str], batch_size: int = 5
    ) -> List[dict]:
        """
        전체 리뷰 목록을 배치 단위로 감성 분석.
        진행률을 콘솔에 출력한다.
        """
        if batch_size < 1:
            raise ValueError(f"batch_size는 1 이상이어야 합니다: {batch_size}")

        all_results: List[dict] = []
        total = len(texts)

        for i in range(0, total, batch_size):
            batch = texts[i: i + batch_size]
            results = self.analyze_sentiment_batch(batch)
            all_results.extend(results)

            done = min(i + batch_size, total)
            pct = done / total * 100 if total else 0.0
            bar_len = 25
            filled = int(bar_len * done / total) if total else 0
            bar = "=" * filled + "-" * (bar_len - filled)
            print(
                f"\r  감성 분석 [{bar}] {done}/{total} ({pct:.0f}%)",
                end="",
                flush=True,
            )

        print()
        return all_results

    # ── 키워드 추출 ──

    def extract_keywords(self, texts: List[str], top_n: int = 30) -> dict:
        """
        리뷰 텍스트 목록에서 칭찬/불만/개선요청 키워드를 추출한다.
        텍스트가 너무 많으면 무작위 샘플링하여 처리.

        반환값: {"praise": [...], "complaint": [...], "improvement": [...]}
        """
        empty: dict = {"praise": [], "complaint": [], "improvement": []}
        if not texts:
            return empty

        # 샘플링 (재현성을 위해 seed 고정)
        if len(texts) > self._MAX_KEYWORD_SAMPLE:
            import random
            rng = random.Random(42)
            sample = rng.sample(texts, self._MAX_KEYWORD_SAMPLE)
        else:
            sample = list(texts)

        sample_trimmed = [t[:200] for t in sample if t and t.strip()]
        if not sample_trimmed:
            return empty

        reviews_formatted = "\n".join(f"- {t}" for t in sample_trimmed)
        prompt = KEYWORD_TEMPLATE.format(
            count=len(sample_trimmed),
            reviews=reviews_formatted,
            top_n=min(top_n, 20),
        )

        try:
            raw = self.client.generate(
                model=self.model,
                prompt=prompt,
                system=KEYWORD_SYSTEM,
                temperature=0.2,
            )
            parsed = extract_json_from_response(raw)
            if isinstance(parsed, dict):
                return {
                    "praise": parsed.get("praise", []),
                    "complaint": parsed.get("complaint", []),
                    "improvement": parsed.get("improvement", []),
                }
        except RuntimeError as exc:
            logger.error("키워드 추출 실패: %s", exc)

        return empty

    # ── 상품별 브리프 ──

    def generate_product_brief(
        self,
        product_name: str,
        reviews: List[str],
        avg_rating: float,
        sentiment: dict,
    ) -> dict:
        """
        상품별 핵심 인사이트 요약 생성.

        반환값: {"brief": str, "key_insights": [str, ...]}
        """
        if not reviews:
            return {"brief": "리뷰 데이터 없음.", "key_insights": []}

        sample = reviews[: self._MAX_BRIEF_SAMPLE]
        reviews_formatted = "\n".join(
            f"{i + 1}. {r[:200]}"
            for i, r in enumerate(sample)
            if r and r.strip()
        )

        prompt = PRODUCT_BRIEF_TEMPLATE.format(
            product_name=product_name,
            avg_rating=avg_rating,
            positive=sentiment.get("positive", 0),
            neutral=sentiment.get("neutral", 0),
            negative=sentiment.get("negative", 0),
            count=len(sample),
            reviews=reviews_formatted,
        )

        try:
            raw = self.client.generate(
                model=self.model,
                prompt=prompt,
                system=BRIEF_SYSTEM,
                temperature=0.3,
            )

            parsed = extract_json_from_response(raw)
            if isinstance(parsed, dict) and "brief" in parsed:
                return {
                    "brief": str(parsed.get("brief", "")),
                    "key_insights": [str(x) for x in parsed.get("key_insights", [])],
                }

            # JSON 파싱 실패 시 전체 텍스트를 brief로 사용
            return {"brief": raw.strip()[:500], "key_insights": []}

        except RuntimeError as exc:
            logger.error("상품 브리프 생성 실패 (%s): %s", product_name, exc)
            return {"brief": "생성 실패.", "key_insights": []}

    # ── 키워드 리뷰 재분류(검증) ──

    def verify_keyword_reviews(
        self,
        word: str,
        polarity: str,
        samples: List[dict],
        mode: str = "batch",
    ) -> List[dict]:
        """키워드에 매칭된 리뷰를 3단계 게이트로 검증해 진짜 귀속분만 남긴다.

        ① 관련성: 그 주제를 실제로 다루는가 (우연한 단어·가정 표현·타제품 비교 제외)
        ② 의도 분류: 칭찬/불만/개선요청/해당없음 → 키워드 카테고리와 일치하는 것만
        ③ 반대신문: 통과분을 1건씩 근거 서술형 예/아니오로 재확인
        각 단계 AI 호출 실패 시 해당 건 보존(드롭 안 함).

        Args:
            word: 키워드 (예: "강도 불량")
            polarity: '긍정' | '부정' | '개선' (→ 칭찬/불만/개선요청)
            samples: [{"text":..,"rating":..}, ...] review_samples 형식
            mode: 'batch'(①②를 12건 묶음) | 'item'(①②도 1건씩)

        Returns:
            검증을 통과한 samples 부분집합 (원본 dict 그대로 유지, ai_intent 태그 추가)
        """
        if not samples:
            return []

        # 키워드 카테고리 ↔ 의도 라벨
        want = {"긍정": "칭찬", "부정": "불만", "개선": "개선요청"}.get(polarity, "관련")
        batch = 1 if mode == "item" else 12

        def _line(j, sm):
            rt = sm.get("rating")
            tag = f"(★{int(rt)}) " if isinstance(rt, (int, float)) and rt else ""
            return f"[{j}] {tag}{str(sm.get('text','')).replace(chr(10),' ')[:240]}"

        def _gen(prompt, sysmsg):
            return self.client.generate(model=self.model, prompt=prompt, system=sysmsg, temperature=0.0)

        # ───── 단계 ① 관련성 게이트 ─────
        # 우연한 단어 포함·가정 표현·다른 제품 비교를 제거
        survivors: List[dict] = []
        for i in range(0, len(samples), batch):
            chunk = samples[i: i + batch]
            lt = "\n".join(_line(j, sm) for j, sm in enumerate(chunk))
            prompt = (
                f'주제: "{word}"\n\n'
                f'아래 리뷰가 이 제품의 "{word}" 주제를 실제로 평가하거나 직접 다루는지 판정하세요.\n'
                f"다음은 모두 '무관'입니다:\n"
                f'- 단어만 우연히 포함 (예: "고장나면 또 살게요"는 고장을 실제 겪은 게 아님)\n'
                f'- 가정·조건 표현 ("~했으면", "~라면", "~었으면")\n'
                f"- 다른 제품·장소(마사지샵, 예전 제품 등) 이야기\n"
                f"- 주제와 무관한 일반 후기\n\n"
                f"리뷰:\n{lt}\n\n"
                '관련 있는 번호만 JSON으로: {"relevant":[0,2]} (없으면 {"relevant":[]})'
            )
            try:
                parsed = extract_json_from_response(_gen(prompt, "당신은 한국어 리뷰 분석가입니다. JSON으로만 답하세요."))
                rel = parsed.get("relevant") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
                if rel is None:
                    survivors.extend(chunk)  # 파싱 실패 → 보존(다음 단계서 거름)
                    continue
                idx = {int(x) for x in rel if isinstance(x, int) and 0 <= x < len(chunk)}
                survivors.extend(chunk[j] for j in range(len(chunk)) if j in idx)
            except RuntimeError as exc:
                if is_quota(exc):
                    raise  # 한도 소진 — 남은 청크 낭비 호출 방지, 즉시 상위로 전파
                logger.warning("재분류 ①관련성 실패, 보존: %s", exc)
                survivors.extend(chunk)

        # ───── 단계 ② 의도 분류 ─────
        # 칭찬/불만/개선요청/해당없음 → 키워드 카테고리와 일치하는 것만
        stage2: List[dict] = []
        for i in range(0, len(survivors), batch):
            chunk = survivors[i: i + batch]
            lt = "\n".join(_line(j, sm) for j, sm in enumerate(chunk))
            prompt = (
                f'주제: "{word}"\n\n'
                f'아래 각 리뷰가 이 제품의 "{word}" 주제에 대해 어떤 의도인지 분류하세요:\n'
                f"- 칭찬: 그 주제를 긍정적으로 평가하거나 만족함\n"
                f"- 불만: 그 주제의 문제·불편·하자를 보고함\n"
                f'- 개선요청: 변화를 요구("~했으면 좋겠다","~추가해주세요")하거나 부족·결핍을 지적함. '
                f"단순 만족(\"좋아요\",\"조절돼서 좋아요\")은 개선요청이 아니라 칭찬입니다\n"
                f"- 해당없음: 위 어디에도 해당하지 않음\n"
                f"별점이 아니라 본문 내용으로 판정하세요.\n\n"
                f"리뷰:\n{lt}\n\n"
                'JSON으로만: {"items":[{"no":0,"intent":"칭찬"},{"no":1,"intent":"불만"}]}'
            )
            try:
                parsed = extract_json_from_response(_gen(prompt, "당신은 한국어 리뷰 분류 전문가입니다. JSON으로만 답하세요."))
                items = parsed.get("items") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
                if items is None:
                    stage2.extend(chunk)
                    continue
                intent_by = {}
                for it in items:
                    if isinstance(it, dict) and isinstance(it.get("no"), int):
                        intent_by[it["no"]] = str(it.get("intent", "")).replace(" ", "")
                for j, sm in enumerate(chunk):
                    if want in intent_by.get(j, ""):
                        sm["ai_intent"] = intent_by.get(j, "")
                        stage2.append(sm)
            except RuntimeError as exc:
                if is_quota(exc):
                    raise
                logger.warning("재분류 ②의도 실패, 보존: %s", exc)
                stage2.extend(chunk)

        # ───── 단계 ③ 반대신문 (1건씩 근거 서술 후 예/아니오) ─────
        q_by_pol = {
            "긍정": '이 리뷰 작성자가 이 제품의 "%s"을(를) 직접 만족·칭찬했습니까?\n'
                    "- 다른 제품/장소 칭찬은 아니오\n- 그 항목에 불만을 말하면 아니오",
            "부정": '이 리뷰 작성자가 이 제품의 "%s" 문제로 불만·불편을 직접 표현했습니까?\n'
                    "- 다른 제품/장소(마사지샵, 예전 제품 등) 불만은 아니오\n"
                    "- 칭찬하거나 문제없다고 하면 아니오\n"
                    '- "유선이었으면 불편" 같은 가정 표현은 아니오',
            "개선": '이 리뷰 작성자가 이 제품의 "%s"에 대해 변화·추가·보완을 요구하거나 부족함을 지적했습니까?\n'
                    '- 단순 만족("좋아요","조절돼서 좋아요")은 아니오\n- 다른 제품 이야기는 아니오',
        }
        qtmpl = q_by_pol.get(polarity, q_by_pol["부정"])
        final: List[dict] = []
        for sm in stage2:
            text = str(sm.get("text", "")).replace("\n", " ")[:300]
            prompt = (
                f"리뷰: {text}\n\n질문: {qtmpl % word}\n\n"
                '먼저 근거를 한 문장으로 쓰고, 마지막 줄을 "답: 예" 또는 "답: 아니오"로 끝내세요.'
            )
            try:
                raw = _gen(prompt, "당신은 한국어 리뷰 분석가입니다.")
            except RuntimeError as exc:
                if is_quota(exc):
                    raise
                final.append(sm)  # 판정 실패 시 보존
                continue
            last = (raw.strip().splitlines() or [""])[-1]
            if ("예" in last) and ("아니" not in last):
                final.append(sm)

        logger.info(
            "  3단계 검증 '%s'(%s): %d → ①%d → ②%d → ③%d",
            word, polarity, len(samples), len(survivors), len(stage2), len(final),
        )
        return final

    # ── Smart Brief ──

    def generate_smart_brief(
        self,
        brand: str,
        month: str,
        kpis: dict,
        top_products: List[dict],
        neg_keywords: List[dict],
    ) -> str:
        """
        월간 전체 Smart Brief 텍스트 생성.
        브랜드 매니저가 바로 읽을 수 있는 한국어 요약문 반환.
        생성 실패 시 빈 문자열 대신 기본 요약문을 반환한다.
        """
        mom_section = ""
        if kpis.get("mom_review_change") is not None:
            change: int = kpis["mom_review_change"]
            pct = kpis.get("mom_review_change_pct", 0)
            sign = "+" if change >= 0 else ""
            mom_section = f"- 전월 대비: {sign}{change}건 ({sign}{pct}%)"

        top_products_text = "\n".join(
            f"  {i + 1}. {p['name']}: {p['review_count']}건, 평균 {p['avg_rating']}점"
            for i, p in enumerate(top_products)
        )

        neg_keywords_text = "\n".join(
            f"  - {kw.get('word', kw.get('category', ''))}: {kw.get('count', 0)}건"
            for kw in neg_keywords
        ) if neg_keywords else "  (없음)"

        prompt = SMART_BRIEF_TEMPLATE.format(
            brand=brand,
            month=month,
            total_reviews=kpis.get("total_reviews", 0),
            avg_rating=kpis.get("avg_rating", 0.0),
            positive_rate=kpis.get("positive_rate", 0.0),
            negative_rate=kpis.get("negative_rate", 0.0),
            photo_review_rate=kpis.get("photo_review_rate", 0.0),
            mom_section=mom_section,
            top_products=top_products_text,
            neg_keywords=neg_keywords_text,
        )

        try:
            result = self.client.generate(
                model=self.model,
                prompt=prompt,
                system=BRIEF_SYSTEM,
                temperature=0.4,
            )
            return result.strip() if result else _make_fallback_brief(brand, month, kpis)
        except RuntimeError as exc:
            logger.error("Smart Brief 생성 실패: %s", exc)
            return _make_fallback_brief(brand, month, kpis)


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
