"""
Ollama AI 분석 모듈
- 로컬 Ollama API (localhost:11434) 를 통한 리뷰 감성 분석
- 모델: exaone3.5:7.8b
- 배치 처리, 키워드 추출, Smart Brief 생성
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
from typing import Any, List, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

__all__ = [
    "OllamaClient",
    "OllamaAnalyzer",
    "extract_json_from_response",
    "parse_sentiment_response",
]

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# URL 검증 헬퍼
# ────────────────────────────────────────────

def _validate_ollama_url(url: str) -> str:
    """
    Ollama 베이스 URL이 유효한 형식인지 검증.
    SSRF(서버 사이드 요청 위조) 방어:
      - localhost / 127.x / ::1 허용
      - RFC 1918 사설망만 허용 (10.x, 172.16-31.x, 192.168.x)
      - 그 외 IP 및 외부 호스트명 불허
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"허용되지 않는 URL 스킴입니다: {parsed.scheme!r}")

    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL에 호스트가 없습니다.")

    # IP 주소인 경우: ipaddress 모듈로 RFC 1918 / loopback 검증
    try:
        addr = ipaddress.ip_address(host)
        if not (addr.is_private or addr.is_loopback):
            raise ValueError(
                f"외부 IP에 대한 Ollama 요청은 허용되지 않습니다: {host!r}. "
                "localhost 또는 RFC 1918 사설 네트워크 주소만 사용하세요."
            )
    except ValueError as ip_exc:
        # ip_address() 자체가 올바른 ValueError 를 던진 경우 (비-IP 주소가 아닌 경우) 재발생
        if "허용되지 않는" in str(ip_exc) or "외부 IP" in str(ip_exc):
            raise
        # 호스트명(문자열)인 경우: localhost 만 허용
        if host not in ("localhost",):
            raise ValueError(
                f"외부 호스트명에 대한 Ollama 요청은 허용되지 않습니다: {host!r}. "
                "'localhost' 또는 사설 IP 주소만 사용하세요."
            ) from ip_exc

    return url.rstrip("/")


# ────────────────────────────────────────────
# Ollama REST API 클라이언트
# ────────────────────────────────────────────

class OllamaClient:
    """Ollama REST API 래퍼 클래스."""

    _CONNECT_TIMEOUT: float = 5.0    # 연결 타임아웃 (초)
    _HEALTH_TIMEOUT: float = 5.0     # 헬스체크 타임아웃 (초)

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: int = 120,
    ) -> None:
        self.base_url = _validate_ollama_url(base_url)
        self.timeout = timeout

        # 커넥션 풀 + 자동 재시도 (네트워크 오류 한정)
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        retry = Retry(
            total=0,          # 비즈니스 로직에서 재시도 관리 → HTTP 레이어는 0
            connect=1,        # 연결 오류 1회 재시도
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def health_check(self) -> bool:
        """Ollama 서버 실행 여부 확인."""
        try:
            resp = self.session.get(
                f"{self.base_url}/api/tags",
                timeout=self._HEALTH_TIMEOUT,
            )
            return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def list_models(self) -> List[str]:
        """설치된 모델 목록 반환."""
        try:
            resp = self.session.get(
                f"{self.base_url}/api/tags",
                timeout=(self._CONNECT_TIMEOUT, 10.0),
            )
            resp.raise_for_status()
            data: dict = resp.json()
            return [m["name"] for m in data.get("models", [])]
        except requests.exceptions.RequestException as exc:
            logger.warning("모델 목록 조회 실패: %s", exc)
            return []

    def generate(
        self,
        model: str,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,
        max_retries: int = 3,
    ) -> str:
        """
        텍스트 생성 요청.
        타임아웃 시 지수 백오프로 재시도.
        연결 오류는 즉시 RuntimeError 로 전파.
        """
        if not model or not model.strip():
            raise ValueError("모델명이 비어 있습니다.")

        payload: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 2048,
            },
        }
        if system:
            payload["system"] = system

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                resp = self.session.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=(self._CONNECT_TIMEOUT, float(self.timeout)),
                )
                resp.raise_for_status()
                return resp.json().get("response", "")

            except requests.exceptions.Timeout as exc:
                wait = 2 ** attempt
                logger.warning(
                    "응답 시간 초과 (시도 %d/%d), %d초 후 재시도...",
                    attempt + 1, max_retries, wait,
                )
                last_exc = exc
                time.sleep(wait)

            except requests.exceptions.ConnectionError as exc:
                raise RuntimeError(f"Ollama 서버 연결 실패: {exc}") from exc

            except requests.exceptions.HTTPError as exc:
                raise RuntimeError(f"Ollama HTTP 오류: {exc}") from exc

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Ollama 요청 실패: {exc}") from exc
                wait = 2 ** attempt
                logger.warning("알 수 없는 오류 (시도 %d/%d): %s", attempt + 1, max_retries, exc)
                time.sleep(wait)

        # 최대 재시도 소진 — last_exc 는 반드시 설정되어 있음
        assert last_exc is not None, "재시도 루프를 완료했으나 예외가 기록되지 않았습니다."
        raise RuntimeError(
            f"최대 재시도 횟수({max_retries})를 초과했습니다."
        ) from last_exc


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


# ────────────────────────────────────────────
# OllamaAnalyzer 메인 클래스
# ────────────────────────────────────────────

class OllamaAnalyzer:
    """
    Ollama 기반 리뷰 분석기.
    감성 분석, 키워드 추출, Smart Brief 생성을 담당한다.
    """

    _MAX_TEXT_LEN: int = 500    # 개별 텍스트 최대 길이 (토큰 절약)
    _MAX_KEYWORD_SAMPLE: int = 100  # 키워드 추출 최대 샘플 수
    _MAX_BRIEF_SAMPLE: int = 20     # 브리프 생성 최대 샘플 수

    def __init__(
        self,
        model: str = "exaone3.5:7.8b",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self.client = OllamaClient(base_url=base_url)

    def health_check(self) -> bool:
        """Ollama 서버 상태 확인 및 모델 존재 여부 검증."""
        if not self.client.health_check():
            logger.error("Ollama 서버에 연결할 수 없습니다: %s", self.client.base_url)
            return False

        models = self.client.list_models()
        base_model = self.model.split(":")[0]
        available = any(base_model in m for m in models)

        if not available:
            logger.warning(
                "모델 '%s' 이 설치되어 있지 않습니다. 설치된 모델: %s",
                self.model, models,
            )
            logger.warning("'ollama pull %s' 명령으로 설치하세요.", self.model)
            return False

        logger.info("Ollama 서버 정상. 모델: %s", self.model)
        return True

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

        Note: run_pipeline() 내부에서 직접 배치 루프를 돌릴 때는
              Progress 클래스를 사용하여 일관된 진행률 표시를 권장한다.
              이 메서드는 독립 호출 전용이다.
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
        """키워드에 매칭된 리뷰 샘플 중 실제로 그 키워드 주제를 다루는 것만 남긴다.

        어휘(정규식) 매칭의 false positive(단어가 우연히 포함됐을 뿐 의미상 무관,
        혹은 반대 맥락)를 LLM으로 걸러낸다.

        Args:
            word: 키워드 (예: "강도 불량")
            polarity: '긍정' | '부정' | '개선'
            samples: [{"text": ..., ...}, ...] review_samples 형식
            mode: 'batch'(여러 건 묶음, 빠름) | 'item'(1건씩, 정밀)

        Returns:
            검증을 통과한 samples 부분집합 (원본 dict 그대로 유지)
        """
        if not samples:
            return []

        type_desc = {
            "긍정": "제품에 대해 긍정적으로 칭찬하는",
            "부정": "제품에 대해 부정적으로 불만을 제기하는",
            "개선": "제품에 대해 개선을 요청하는",
        }.get(polarity, "관련된")

        def sent_ok(sent) -> bool:
            """키워드 극성 ↔ 리뷰 감성 엄격 일치 — 같은 극성 판정만 귀속.

            예: 부정 키워드 '소음'에 '조용해요/소음 걱정 없어요'(긍정·중립) 유입 차단.
            '개선'은 부정·중립 허용(개선 요청은 만족 표현이 아님). None(미판정)은 보존.
            """
            if sent is None:
                return True
            if polarity == "긍정":
                return sent == "긍정"
            if polarity == "부정":
                return sent == "부정"
            return sent != "긍정"

        if mode == "item":
            kept: List[dict] = []
            for sm in samples:
                text = str(sm.get("text", "")).replace("\n", " ")[:300]
                if not text.strip():
                    continue
                prompt = (
                    f'키워드: "{word}"\n'
                    f'다음 리뷰가 실제로 "{word}" 주제를 다루나요? '
                    f"단어만 우연히 포함된 경우는 무관입니다.\n"
                    f"리뷰: {text}\n\n"
                    "다음 중 하나로만 답하세요: 무관 / 긍정 / 부정 / 중립\n"
                    "(그 주제에 대한 평가 기준 — 불만·불편이면 부정, 만족하거나 "
                    '"문제없다/조용하다/괜찮다"처럼 문제가 없다는 표현이면 긍정, 단순 언급이면 중립)'
                )
                try:
                    raw = self.client.generate(
                        model=self.model, prompt=prompt,
                        system="당신은 한국어 리뷰 분류 검증 전문가입니다.",
                        temperature=0.0,
                    )
                except RuntimeError as exc:
                    logger.warning("재분류(item) 실패, 보존: %s", exc)
                    kept.append(sm)
                    continue
                low = raw.strip()
                # 무관 판정 우선 ("해당 없음"이 "해당"으로 오인되지 않도록)
                if any(t in low for t in ("무관", "아니")) or "해당없" in low.replace(" ", "") or "no" in low.lower():
                    continue
                sent = "부정" if "부정" in low else "긍정" if "긍정" in low else "중립" if "중립" in low else None
                if sent is None and any(t in low for t in ("예", "해당", "관련")):
                    sent = "중립"
                if sent and sent_ok(sent):
                    sm["ai_sent"] = sent
                    kept.append(sm)
            return kept

        # batch 모드 (기본): 12건씩 묶어 판별 (감성 동시 판정)
        kept = []
        BATCH = 12
        def _line(j, sm):
            rt = sm.get("rating")
            tag = f"(★{int(rt)}) " if isinstance(rt, (int, float)) and rt else ""
            return f"[{j}] {tag}{str(sm.get('text','')).replace(chr(10),' ')[:220]}"

        for i in range(0, len(samples), BATCH):
            chunk = samples[i: i + BATCH]
            list_text = "\n".join(_line(j, sm) for j, sm in enumerate(chunk))
            prompt = (
                f"당신은 한국어 리뷰 분류 검증 전문가입니다.\n"
                f'키워드: "{word}" (이 키워드는 {type_desc} 주제입니다)\n\n'
                f'아래 리뷰들 중에서 실제로 "{word}" 주제를 다루는 리뷰를 고르고, '
                f"각 리뷰가 그 주제에 대해 갖는 감성(긍정/부정/중립)을 함께 판정하세요.\n"
                f"단어가 우연히 포함됐을 뿐 의미상 무관한 리뷰는 제외하세요.\n\n"
                f"감성 판정 기준 (그 주제에 대한 평가 기준, 별점·전체 분위기와 무관):\n"
                f"- 그 주제에 불만·불편·문제를 말하면 → 부정\n"
                f"- 그 주제가 만족스럽거나 문제없다고 말하면 → 긍정\n"
                f'- 특히 "소음이 없어요", "조용해요", "걱정했는데 괜찮아요"처럼 '
                f"문제가 없다는 표현은 반드시 긍정입니다\n"
                f"- 별점은 참고일 뿐입니다: 별점이 높아도 그 주제에 명시적 불만이 있으면 부정, "
                f"그 주제를 칭찬·만족하면 별점과 무관하게 긍정입니다\n"
                f"- 단순 언급뿐 평가가 없으면 → 중립\n\n"
                f"리뷰:\n{list_text}\n\n"
                'JSON으로만 답하세요: {"matched":[{"no":0,"sent":"긍정"},{"no":2,"sent":"부정"}]}  '
                '(없으면 {"matched":[]})'
            )
            try:
                raw = self.client.generate(
                    model=self.model, prompt=prompt,
                    system="당신은 한국어 리뷰 분류 검증 전문가입니다. JSON으로만 응답하세요.",
                    temperature=0.0,
                )
                parsed = extract_json_from_response(raw)
                # dict({"matched":[...]}) 또는 배열만 온 경우 모두 수용
                mlist = None
                if isinstance(parsed, dict) and isinstance(parsed.get("matched"), list):
                    mlist = parsed["matched"]
                elif isinstance(parsed, list):
                    mlist = parsed
                picks: List[tuple] = []  # (idx, sent|None) — 구형 [0,2] 응답도 호환
                if mlist is not None:
                    for x in mlist:
                        if isinstance(x, int) and 0 <= x < len(chunk):
                            picks.append((x, None))
                        elif isinstance(x, dict):
                            no = x.get("no")
                            if isinstance(no, int) and 0 <= no < len(chunk):
                                s = str(x.get("sent", ""))
                                sent = "부정" if "부" in s else "긍정" if "긍" in s else "중립"
                                picks.append((no, sent))
                seen_idx: set = set()
                for j, sent in picks:
                    if j in seen_idx:
                        continue
                    seen_idx.add(j)
                    if sent_ok(sent):
                        sm = chunk[j]
                        if sent:
                            sm["ai_sent"] = sent
                        kept.append(sm)
            except RuntimeError as exc:
                logger.warning("재분류(batch) 실패, 청크 보존: %s", exc)
                kept.extend(chunk)

        # 2차 정밀 검증 — 별점과 키워드 극성이 상반된 '의심 귀속'만 1건씩 재확인
        # (배치 판정에서 새는 케이스 차단: 예) 부정 키워드에 ★5 칭찬 리뷰)
        def _suspicious(sm) -> bool:
            rt = sm.get("rating")
            if not isinstance(rt, (int, float)) or not rt:
                return False
            return rt <= 3 if polarity == "긍정" else rt >= 4

        sus = [sm for sm in kept if _suspicious(sm)]
        if sus:
            confirmed = {id(x) for x in self.verify_keyword_reviews(word, polarity, sus, mode="item")}
            before_n = len(kept)
            kept = [sm for sm in kept if not _suspicious(sm) or id(sm) in confirmed]
            if len(kept) != before_n:
                logger.info("  2차 검증 '%s': 의심 %d건 중 %d건 제외", word, len(sus), before_n - len(kept))
        return kept

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
