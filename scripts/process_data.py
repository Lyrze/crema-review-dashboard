"""
크리마 리뷰 CSV 데이터 처리 파이프라인
- 슬룸(Sloom) 헬스테크 마사지기 브랜드용
- GitHub Pages 정적 대시보드용 JSON 생성
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict

import pandas as pd

__all__ = [
    "load_csv",
    "calc_kpis",
    "calc_timeline",
    "calc_review_path_distribution",
    "calc_products",
    "extract_keywords_basic",
    "save_json",
    "update_index_json",
    "run_pipeline",
    "normalize_product_name",
    "validate_month",
    "validate_brand",
]

# 프로젝트 루트 기준 경로
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ────────────────────────────────────────────
# 로깅 설정
# ────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────
# 입력 검증 헬퍼
# ────────────────────────────────────────────

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_SAFE_NAME_RE = re.compile(r"^[\w가-힣\-. ]+$")


def validate_month(month: str) -> str:
    """YYYY-MM 형식 검증. 유효하지 않으면 ValueError 발생."""
    if not _MONTH_RE.match(month):
        raise ValueError(f"월 형식이 잘못되었습니다 (YYYY-MM 필요): {month!r}")
    return month


def validate_brand(brand: str) -> str:
    """브랜드명 검증 — 경로 순회(path traversal) 방지."""
    stripped = brand.strip()
    if not stripped:
        raise ValueError("브랜드명이 비어 있습니다.")
    if not _SAFE_NAME_RE.match(stripped):
        raise ValueError(
            f"브랜드명에 허용되지 않는 문자가 포함되어 있습니다: {brand!r}"
        )
    return stripped


def resolve_safe_output_dir(docs_root: Path, brand: str, month: str) -> Path:
    """
    출력 디렉토리를 반환하되, docs_root 외부로 벗어나지 않도록 검증.
    경로 순회 공격(path traversal) 방지.
    """
    out_dir = (docs_root / brand / month).resolve()
    if not str(out_dir).startswith(str(docs_root.resolve())):
        raise ValueError(
            f"출력 경로가 허용 범위를 벗어납니다: {out_dir}"
        )
    return out_dir


# ────────────────────────────────────────────
# 상품명 정규화
# ────────────────────────────────────────────

# 제거할 프로모션 접두사/패턴 (순서 중요: 구체적인 것부터)
PROMO_PATTERNS: List[str] = [
    # 1단계: ★[인플루언서x슬룸]...★ 형태의 맨 앞 콜라보+★ 블록
    r"^★\s*\[[^\]]*[Xx×][^\]]*슬룸[^\]]*\]\s*",
    r"^★\s*\[[^\]]*슬룸[^\]]*[Xx×][^\]]*\]\s*",
    # ★숫자일 특별 연장★ 같은 기간 프로모션 블록
    r"^★\d+일[^★]*★\s*",
    # ★[내용]★ 일반 강조 블록
    r"★[^★]*★\s*",
    # 뒤에 홀로 남은 ★
    r"★\s*$|^\s*★",
    r"★",
    r"☆[^☆]*☆\s*",
    r"♥[^♥]*♥\s*",
    # 콜라보 패턴: [인플루언서명x슬룸], [슬룸x인플루언서] (숫자포함 닉네임)
    r"\[[\w가-힣\s]+[Xx×][\w가-힣\s]*슬룸[\w가-힣\s]*\]\s*",
    r"\[[\w가-힣\s]*슬룸[\w가-힣\s]*[Xx×][\w가-힣\s]+\]\s*",
    # 콜라보 태그 괄호 밖 버전: 인플루언서X슬룸 / 덤순이X슬룸
    r"[가-힣\w]{2,15}[Xx×]슬룸\s*",
    r"슬룸[Xx×][가-힣\w]{2,15}\s*",
    # 일반 [ ] 프로모션 태그
    r"\[[^\]]{1,30}\]\s*",
    r"【[^】]{1,30}】\s*",
    r"〔[^〕]{1,30}〕\s*",
    # 괄호 안 할인/특가/이벤트
    r"\([^)]{1,30}할인[^)]*\)\s*",
    r"\([^)]{1,30}특가[^)]*\)\s*",
    r"\([^)]{1,30}이벤트[^)]*\)\s*",
    # 맨 앞 마케팅 키워드
    r"^(최대할인|타임딜|특가|비밀링크|핫딜|한정|이벤트|추가할인|쿠폰)\s*[_\-]?\s*",
    # 기간/회차 접두사: "7일 특별 연장" 같은 패턴
    r"^\d+일\s+특별\s+연장\s*",
    r"^[0-9]+차[!!\s]*\s*",
    # 구독자 전용/한정 프로모션 블록 (앞부분)
    r"^구독자\s*(한정|전용)\s*",
    r"^단독공구\s*",
    # 숫자% 할인 블록
    r"\d+%\s*할인[!！]*",
    # 계절/이벤트 프로모션 (어버이날, 설날 등)
    r"^(어버이날|설날|추석|크리스마스|블랙프라이데이)\s*",
    # 수식어 접두사: "역대급", "대박", "설레는"
    r"^(역대급|대박|설레는|찬스)\s*",
]

# 결과가 프로모션 전용 문구인지 감지하는 패턴 (정규화 후에도 프로모 문구만 남은 경우)
_PROMO_ONLY_RE: re.Pattern = re.compile(
    r"^(구독자|역대급|대박|설레는|단독공구|어버이날|선물\s*찬스|할인|특가|이벤트|찬스)[가-힣\w\s!！%]*$",
    re.IGNORECASE,
)

COMPILED_PROMO: List[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in PROMO_PATTERNS
]

# ────────────────────────────────────────────
# 제품명 유의어 → 표준명 매핑
# 마케팅 잔재 제거 후 2차 표준화에 사용
# ────────────────────────────────────────────
SYNONYM_ENTRIES: List[Tuple[re.Pattern, str]] = []

_RAW_SYNONYMS: List[Tuple[List[str], str]] = [
    # ── 목 마사지 베개 플러스 (V1) ──
    (["목베게플러스", "목베게+", "경추 목 마사지기 베개", "경추목베개", "목베개",
      "고밀도 프리미엄 경추 마사지 베개", "고밀도 프리미엄 경추 마사지 계절베개",
      "고밀도 프리미엄 경추 마사지 계절베개 목베개 플러스",
      "목베개 플러스 프리미엄", "목마사지베개",
      "경추 마사지기 베개", "목베개 플러스", "목베개플러스"], "목베개플러스"),
    # ── 목 마사지 베개 V2 ──
    (["목베개2", "목베개 V2", "목마사지V2", "목마사지베개 브이투",
      "목 마사지 베개 V2"], "목마사지베개 V2"),
    # ── 허리편한케어 V2 ──
    (["허리V2", "허리 마사지기 브이투", "허리편한케어V2+", "허리케어V2",
      "허리편한s2", "허리편한케어S2", "허리편한케어 V2",
      "허리편한케어 V2 허리", "허리편한케어V2 허리"], "허리편한케어 V2"),
    # ── 허리편한케어 V1 ──
    (["허리 V1", "허리 마사지기 브이원", "허리편한케어 플러스", "허리편한케어+",
      "허리케어V1", "SL23EQ02", "허리편한케어 V1",
      "허리편한케어 V1 허리"], "허리편한케어 V1"),
    # ── 발편한케어 V2 ──
    (["발V2", "발 마사지기 브이투", "발편한케어V2+", "발케어V2", "발편한케어 V2",
      "발편한케어 프로", "발편한케어 V2 발"], "발편한케어 V2"),
    # ── 발편한케어 V1 ──
    (["발 V1", "발 마사지기 브이원", "발편한케어 플러스", "발편한케어+",
      "발케어V1", "발편한케어 V1",
      "발편한케어 V1 프리미엄"], "발편한케어 V1"),
    # ── 어댑터 ──
    (["충전기", "어댑터"], "어댑터"),
    # ── 케이블 ──
    (["충전선", "충전기선", "연결선", "전선", "케이블"], "케이블"),
    # ── 넥숄더 힐링케어 V2 ──
    (["넥숄더V2", "목 어깨 마사지기 V2", "넥숄더 힐링케어 V2",
      "넥숄더 힐링케어 V2 목"], "넥숄더 힐링케어 V2"),
    # ── 넥숄더 프로 (구 모델, V2 이전) ──
    (["넥숄더 프로", "넥숄더 프로 목 어깨 마사지기",
      "넥숄더프로", "넥숄더 프로 목"], "넥숄더 프로"),
    # ── 넥숄더 힐링케어 (V2 이전 일반 모델) ──
    (["넥숄더 힐링케어 목 어깨 마사지기",
      "넥숄더 힐링케어 목"], "넥숄더 힐링케어"),
    # ── 하루수면 ──
    (["하루수면+", "하루수면"], "하루수면"),
    # ── 에어리프팅 ──
    (["에어마사지", "에어리프팅"], "에어리프팅"),
    # ── 목편한케어 ──
    (["목편한개어", "목변한개어", "변한개어", "목편한케어플러스", "목편한케어",
      "목편한케어 목"], "목편한케어"),
    # ── 목편한케어 플라잉 ──
    (["MDSD", "MSDS 서류"], "MSDS 서류"),
    (["SL24EQ04", "목편한케어 플라잉", "목편한케어 플라잉 목"], "목편한케어 플라잉"),
    # ── 골반 마사지기 ──
    (["골반 케어", "골반케어", "골반편한케어",
      "골반 케어 프리미엄", "골반 케어 프리미엄 골반 마사지기",
      "골반 마사지기", "골반마사지기"], "골반 마사지기"),
    # ── 눈편한케어 ──
    (["눈편한케어", "눈 마사지기"], "눈편한케어"),
    # ── 종아리편한케어 ──
    (["종아리편한케어", "종아리 마사지기", "종아리케어"], "종아리편한케어"),
]

for _synonyms, _canonical in _RAW_SYNONYMS:
    for _syn in _synonyms:
        _pat = re.compile(r"^" + re.escape(_syn) + r"$", re.IGNORECASE)
        SYNONYM_ENTRIES.append((_pat, _canonical))


def apply_synonym_map(name: str) -> str:
    """
    정규화된 상품명을 표준 제품명으로 변환한다.

    1단계: 정확히 일치하는 패턴 (^pattern$)
    2단계: 접두어 매칭 (name이 synonym으로 시작하는 경우, min 6자)
            예: "목베개 플러스 경추 목 마사지기 베개" → "목베개플러스"
    """
    name_s = name.strip()
    name_lower = name_s.lower()

    # 1단계: 정확 매칭
    for pattern, canonical in SYNONYM_ENTRIES:
        if pattern.match(name_s):
            return canonical

    # 2단계: 접두어 매칭 (normalized name이 synonym으로 시작)
    for synonyms, canonical in _RAW_SYNONYMS:
        for syn in synonyms:
            syn_l = syn.lower().strip()
            if len(syn_l) < 6:
                continue  # 너무 짧은 패턴은 오탐 방지
            # name이 "syn " 또는 "syn_" 이후 공백/특수문자로 이어지면 매칭
            if name_lower == syn_l or name_lower.startswith(syn_l + " ") or name_lower.startswith(syn_l + "_"):
                return canonical

    return name_s

# 쓸모없는 마케팅 잔재 접미사/접두사 제거
TRAILING_NOISE_RE: re.Pattern = re.compile(
    r"(최저가\s*OPEN[★!★]*"
    r"|^\s*역대급\s*대박\s*할인\s*$"
    r"|^\s*역대급\s*할인\s*$"
    r"|\s*특가$"
    r"|\s*OPEN[★!]*$)",
    re.IGNORECASE,
)

_SEPARATOR_RE: re.Pattern = re.compile(r"^[\s_\-|/\\]+|[\s_\-|/\\]+$")
_MARKETING_SUFFIX_RE: re.Pattern = re.compile(
    r"\s*(특가|할인|한정|이벤트|기획|세일)$"
)


def normalize_product_name(raw_name: str) -> str:
    """
    마케팅/프로모션 접두사를 제거하고 핵심 상품명을 추출한다.

    예:
      "★[까밀라댁x슬룸] 구독자 한정 역대급 할인★" → "구독자 한정 역대급 할인"
      "[타임딜] 목편한케어 목 마사지기"              → "목편한케어 목 마사지기"
      "★최대할인 비밀링크★ 목베개 플러스 특가"       → "목베개 플러스"
    """
    if not isinstance(raw_name, str):
        return str(raw_name)

    name = raw_name.strip()

    # 반복 적용으로 중첩 패턴 제거 (최대 5회)
    for _ in range(5):
        prev = name
        for pattern in COMPILED_PROMO:
            name = pattern.sub("", name).strip()
        if name == prev:
            break

    # 마케팅 잔재 접미사 제거
    name = TRAILING_NOISE_RE.sub("", name).strip()

    # 앞뒤 구분자 제거
    name = _SEPARATOR_RE.sub("", name)

    # 빈 문자열이 되면 원본 반환
    result = name if name else raw_name.strip()

    # 유의어 매핑 적용
    mapped = apply_synonym_map(result)

    # 정규화 후에도 프로모션 전용 문구만 남은 경우 → "(기타 프로모션)" 그룹으로 통합
    if _PROMO_ONLY_RE.match(mapped):
        return "(기타 프로모션)"

    return mapped


# ────────────────────────────────────────────
# 옵션 기반 정규화 (인플루언서 광고 + 세트 + 버전 분리)
# ────────────────────────────────────────────

# 옵션에서 추출할 알려진 상품 키워드 (긴 패턴 먼저)
_OPTION_PRODUCT_PATTERNS: List[tuple] = [
    (re.compile(r"목\s*마사지\s*베개\s*V2|목마사지베개\s*V2"), "목마사지베개 V2"),
    (re.compile(r"넥숄더\s*힐링케어\s*V2|넥숄더힐링케어\s*V2|넥숄더\s*V2"), "넥숄더 힐링케어 V2"),
    (re.compile(r"넥숄더\s*힐링케어(?!\s*V)"), "넥숄더 힐링케어"),
    (re.compile(r"넥숄더\s*프로"), "넥숄더 프로"),
    (re.compile(r"허리편한케어\s*V2|허리편한케어V2"), "허리편한케어 V2"),
    (re.compile(r"허리편한케어\s*V1|허리편한케어V1"), "허리편한케어 V1"),
    (re.compile(r"발편한케어\s*V2|발편한케어V2|발편한케어\s*프로"), "발편한케어 V2"),
    (re.compile(r"발편한케어\s*V1|발편한케어V1|발편한케어\s*데일리"), "발편한케어 V1"),
    (re.compile(r"발편한케어(?!\s*[VvDd데프])"), "발편한케어"),
    (re.compile(r"목편한케어\s*플라잉"), "목편한케어 플라잉"),
    (re.compile(r"목편한케어(?!\s*플)"), "목편한케어"),
    (re.compile(r"목베개\s*플러스|목베개플러스"), "목베개플러스"),
    (re.compile(r"종아리\s*마사지기|종아리편한케어|종아리\s*케어"), "종아리편한케어"),
    (re.compile(r"눈편한케어"), "눈편한케어"),
    (re.compile(r"손편한케어"), "손편한케어"),
    (re.compile(r"엘보케어|팔꿈치"), "엘보케어"),
    (re.compile(r"골반\s*케어|골반편한케어"), "골반케어"),
    (re.compile(r"코어\s*요추\s*벨트|코어요추벨트|요추\s*벨트"), "코어 요추벨트"),
    (re.compile(r"마그네슘\s*시너지\s*크림|마그네슘\s*크림"), "마그네슘 시너지 크림"),
    (re.compile(r"EMS\s*발\s*마사지기"), "EMS 발 마사지기"),
    (re.compile(r"USB\s*충전|어댑터|충전기"), "USB 충전 어댑터"),
    (re.compile(r"하루끝차|티백차"), "하루끝차"),
]

# 증정 표기 패턴 (해당 부분 제거)
_GIFT_PATTERN: re.Pattern = re.compile(
    r"\(증정\)[^+]*?(?=\+|$)|\+\s*\(증정\)[^+]*?(?=\+|$)|증정\)[^+]*?(?=\+|$)",
    re.IGNORECASE,
)

# 세트 표기 (★...SET★, [...세트])
_SET_NAME_PATTERN: re.Pattern = re.compile(
    r"★\s*[^★]*?SET\s*★|\[[^\]]*?세트\]",
    re.IGNORECASE,
)


def _extract_products_from_option(option: str) -> List[str]:
    """옵션 문자열에서 실제 상품들을 추출 (증정 제외, 중복 제거, 순서 유지)."""
    if not option or not isinstance(option, str):
        return []
    # 증정 제거
    cleaned = _GIFT_PATTERN.sub("", option)
    # 패턴 매칭으로 상품 추출
    found = []
    seen = set()
    for pattern, canonical in _OPTION_PRODUCT_PATTERNS:
        if pattern.search(cleaned):
            if canonical not in seen:
                # 추출 위치를 보존하기 위해 search().start()로 인덱스 확보
                m = pattern.search(cleaned)
                found.append((m.start(), canonical))
                seen.add(canonical)
    # 위치 순으로 정렬 (옵션에 먼저 등장하는 상품이 메인)
    found.sort(key=lambda x: x[0])
    return [name for _, name in found]


def _is_set_option(option: str) -> bool:
    """옵션에 세트 표기(★SET★, [세트])가 있는지."""
    if not option or not isinstance(option, str):
        return False
    return bool(_SET_NAME_PATTERN.search(option))


# 인플루언서/프로모션 raw_name이면서 옵션 기반 분해가 필요한 패턴
_PROMO_RAW_PATTERNS: List[re.Pattern] = [
    re.compile(r"덤순이|예나러브|사라패밀리|배말랭|코메리칸|까밀라댁|밍슐랭|꿀민|"
               r"강주은|44언니|구독자.*?할인|구독자.*?특가|역대급.*?할인|"
               r"초특가.*?할인|선물\s*찬스|특별\s*연장", re.IGNORECASE),
]

# "발편한케어 프리미엄 발 마사지기 (데일리, 프로)" 같은 버전 미분리 raw_name
_VERSION_AMBIGUOUS_PATTERNS: List[tuple] = [
    (re.compile(r"발편한케어\s*프리미엄.*?\(\s*데일리\s*,\s*프로\s*\)"),
     "발편한케어"),  # 옵션에서 V1/V2 결정
]


def resolve_product_with_option(raw_name: str, option: str) -> str:
    """
    상품명 + 옵션을 함께 보고 최종 정규화 상품명을 결정.

    규칙:
      1. 옵션에 ★SET★/[세트] 표기 → "[세트] 상품A + 상품B" 카테고리
      2. raw_name이 "[코어 밸런스 세트] A + B" 형태 → "[세트] A + B"
      3. raw_name이 프로모션 광고 + 옵션에 상품 2개 이상(증정 제외) → 세트
      4. raw_name이 프로모션 광고 + 옵션에 상품 1개 → 그 상품으로 매핑
      5. raw_name이 버전 미명시(데일리/프로) + 옵션에 V1/V2 → 해당 버전
      6. 그 외 → 기존 normalize_product_name 동작
    """
    if not isinstance(raw_name, str):
        raw_name = str(raw_name)
    option_str = option if isinstance(option, str) else ""

    # 1) raw_name 자체에 [세트] / SET 표기 있으면 세트 카테고리로
    if _SET_NAME_PATTERN.search(raw_name):
        prods = _extract_products_from_option(raw_name) or _extract_products_from_option(option_str)
        # 메인 상품 2개만 추출 (증정 제외)
        prods = [p for p in prods if "마그네슘" not in p and "코어 요추벨트" not in p][:2]
        if len(prods) >= 2:
            return "[세트] " + " + ".join(prods)
        # raw_name이 세트인데 옵션 분해 실패 시 fallback
        if prods:
            return prods[0]

    # 2) raw_name이 인플루언서 광고 → 옵션 기반 분해
    is_promo = any(p.search(raw_name) for p in _PROMO_RAW_PATTERNS)
    if is_promo and option_str:
        is_set = _is_set_option(option_str)
        prods = _extract_products_from_option(option_str)
        # 증정 상품(마그네슘, 코어 요추벨트)은 메인 카운트에서 제외
        main_prods = [p for p in prods if "마그네슘" not in p and "코어 요추벨트" not in p]
        if is_set and len(main_prods) >= 2:
            return "[세트] " + " + ".join(main_prods[:2])
        if main_prods:
            return main_prods[0]
        if prods:
            return prods[0]
        # 옵션도 텅 비어있으면 (기타 프로모션)
        return "(기타 프로모션)"

    # 3) 버전 미명시 상품 → 옵션에서 V1/V2 추출
    #    규칙: V2/프로 명시 시에만 V2, 그 외(V1 명시/명시 없음/옵션 없음) → V1 기본
    for pat, base in _VERSION_AMBIGUOUS_PATTERNS:
        if pat.search(raw_name):
            if option_str and re.search(r"V2|프로|Pro", option_str, re.IGNORECASE):
                return base + " V2"
            # V1 명시 / 명시 없음 / 옵션 없음 → V1 기본값
            return base + " V1"

    # 4) 기본 정규화
    return normalize_product_name(raw_name)


def get_product_group_key(normalized_name: str) -> str:
    """
    정규화된 상품명을 동일 상품 그룹 키로 변환.
    '목베개 플러스 특가' → '목베개 플러스'처럼 마케팅 잔재 제거.
    """
    return _MARKETING_SUFFIX_RE.sub("", normalized_name).strip()


# ────────────────────────────────────────────
# CSV 로딩 및 전처리
# ────────────────────────────────────────────

# 크리마 CSV 컬럼 → 내부 키 매핑
COLUMN_MAP: dict = {
    "리뷰ID": "review_id",
    "리뷰code": "review_code",
    "주문번호": "order_no",
    "리뷰작성일": "review_date",
    "상품구매일": "purchase_date",
    "배송완료일": "delivery_date",
    "리뷰본문": "body",
    "회원ID": "member_id",
    "회원명": "member_name",
    "회원등급": "member_grade",
    "추가수집정보": "extra_info",
    "상품번호": "product_id",
    "상품명": "product_name_raw",
    "상품가격": "product_price",
    "상품옵션": "product_option",
    "적립금": "points",
    "적립금지급일": "points_date",
    "리뷰작성경로": "review_path",
    "리뷰별점": "rating",
    "태그": "tags",
    "포토개수": "photo_count",
    "동영상개수": "video_count",
    "포토1_url": "photo1_url",
    "포토2_url": "photo2_url",
    "포토3_url": "photo3_url",
    "포토4_url": "photo4_url",
    "동영상1_url": "video1_url",
    "동영상2_url": "video2_url",
    "동영상3_url": "video3_url",
    "동영상4_url": "video4_url",
    "댓글개수": "comment_count",
    "댓글내용": "comment_body",
}

_REQUIRED_COLUMNS: List[str] = ["review_id", "review_date", "body", "product_name_raw", "rating"]
_DATE_COLUMNS: List[str] = ["review_date", "purchase_date", "delivery_date"]
_NUMERIC_COLUMNS: List[str] = ["rating", "photo_count", "video_count", "comment_count", "product_price"]


def load_csv(csv_path: Path) -> pd.DataFrame:
    """
    크리마 CSV를 읽어 전처리된 DataFrame 반환.
    인코딩은 CP949 우선, 실패 시 UTF-8-SIG, UTF-8 순으로 시도.
    """
    logger.info("CSV 파일 읽는 중: %s", csv_path)
    df: Optional[pd.DataFrame] = None

    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            df = pd.read_csv(csv_path, encoding=enc, low_memory=False)
            logger.info("  인코딩: %s, 행 수: %d", enc, len(df))
            break
        except UnicodeDecodeError:
            continue
        except Exception as exc:
            raise RuntimeError(f"CSV 읽기 중 예상치 못한 오류: {exc}") from exc

    if df is None:
        raise RuntimeError(
            f"모든 인코딩(cp949, utf-8-sig, utf-8)으로 CSV 읽기에 실패했습니다: {csv_path}"
        )

    # 컬럼명 공백 제거
    df.columns = df.columns.str.strip()

    # 내부 키로 리네임 (존재하는 컬럼만)
    rename_map = {k: v for k, v in COLUMN_MAP.items() if k in df.columns}
    df = df.rename(columns=rename_map)

    # 필수 컬럼 확인
    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        logger.warning("누락된 필수 컬럼: %s", missing)

    # 날짜 파싱 (벡터화)
    for date_col in _DATE_COLUMNS:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    # 숫자형 변환 (벡터화)
    for num_col in _NUMERIC_COLUMNS:
        if num_col in df.columns:
            df[num_col] = pd.to_numeric(df[num_col], errors="coerce")

    # 결측 리뷰 본문 처리
    if "body" in df.columns:
        df["body"] = df["body"].fillna("").astype(str)

    # 상품명 정규화 — 옵션 컬럼이 있으면 옵션 기반 분해도 적용
    if "product_name_raw" in df.columns:
        if "product_option" in df.columns:
            df["product_name"] = df.apply(
                lambda r: resolve_product_with_option(
                    r["product_name_raw"], r.get("product_option", "") or ""
                ),
                axis=1,
            )
        else:
            df["product_name"] = df["product_name_raw"].map(normalize_product_name)

    # 별점 유효값만 유지 (1~5)
    if "rating" in df.columns:
        before = len(df)
        df = df[df["rating"].between(1, 5)].copy()
        dropped = before - len(df)
        if dropped:
            logger.warning("  유효하지 않은 별점 행 제거: %d건", dropped)

    logger.info("  전처리 완료. 유효 리뷰: %d건", len(df))
    return df


# ────────────────────────────────────────────
# KPI 계산
# ────────────────────────────────────────────

def calc_kpis(df: pd.DataFrame, prev_df: Optional[pd.DataFrame] = None) -> dict:
    """전체 KPI 딕셔너리 생성."""
    total = len(df)
    avg_rating = round(float(df["rating"].mean()), 2) if total else 0.0

    # 별점 분포 (벡터화)
    rating_series = df["rating"].value_counts().reindex(range(1, 6), fill_value=0)
    rating_dist: dict = {str(r): int(rating_series[r]) for r in range(1, 6)}

    photo_count = (
        int(df["photo_count"].fillna(0).gt(0).sum())
        if "photo_count" in df.columns
        else 0
    )
    photo_rate = round(photo_count / total * 100, 1) if total else 0.0

    # 감성 분포
    if "sentiment" in df.columns:
        sentiment_counts = df["sentiment"].value_counts()
        pos = int(sentiment_counts.get("positive", 0))
        neg = int(sentiment_counts.get("negative", 0))
        neu = int(sentiment_counts.get("neutral", 0))
    else:
        # 별점 기반 간이 추정: 4~5 긍정, 3 중립, 1~2 부정
        pos = int(df["rating"].ge(4).sum())
        neu = int(df["rating"].eq(3).sum())
        neg = int(df["rating"].le(2).sum())

    pos_rate = round(pos / total * 100, 2) if total else 0.0
    neg_rate = round(neg / total * 100, 2) if total else 0.0

    kpis: dict = {
        "total_reviews": total,
        "avg_rating": avg_rating,
        "rating_distribution": rating_dist,
        "photo_review_count": photo_count,
        "photo_review_rate": photo_rate,
        "positive_count": pos,
        "neutral_count": neu,
        "negative_count": neg,
        "positive_rate": pos_rate,
        "negative_rate": neg_rate,
    }

    # 전월 대비 변화
    if prev_df is not None and len(prev_df) > 0:
        prev_total = len(prev_df)
        prev_avg = float(prev_df["rating"].mean())
        kpis["mom_review_change"] = total - prev_total
        kpis["mom_review_change_pct"] = round((total - prev_total) / prev_total * 100, 1)
        kpis["mom_rating_change"] = round(avg_rating - prev_avg, 2)
    else:
        kpis["mom_review_change"] = None
        kpis["mom_review_change_pct"] = None
        kpis["mom_rating_change"] = None

    return kpis


def calc_timeline(df: pd.DataFrame) -> List[dict]:
    """
    일별 리뷰수 + 평균별점 타임라인 (완전 벡터화 집계).
    iterrows() 를 제거하고 .to_dict('records') 로 변환.
    """
    if "review_date" not in df.columns:
        return []

    grouped = (
        df.assign(date_str=df["review_date"].dt.strftime("%Y-%m-%d"))
        .groupby("date_str", sort=True)["rating"]
        .agg(count="count", avg_rating="mean")
        .reset_index()
    )
    # 컬럼명 정규화
    grouped.columns = ["date_str", "count", "avg_rating"]

    return [
        {
            "date": r["date_str"],
            "count": int(r["count"]),
            "avg_rating": round(float(r["avg_rating"]), 2),
        }
        for r in grouped.to_dict("records")
    ]


def calc_review_path_distribution(df: pd.DataFrame) -> dict:
    """리뷰 작성 경로별 분포 (벡터화)."""
    if "review_path" not in df.columns:
        return {}
    counts = df["review_path"].fillna("미분류").value_counts()
    return {str(k): int(v) for k, v in counts.items()}


# ────────────────────────────────────────────
# 상품별 집계
# ────────────────────────────────────────────

def calc_products(
    df: pd.DataFrame,
    prev_df: Optional[pd.DataFrame] = None,
    top_review_count: int = 3,
) -> List[dict]:
    """상품별 집계 데이터 생성."""
    if "product_name" not in df.columns:
        return []

    # 전월 상품별 집계 사전 구성 (prev_df 있을 때만)
    prev_lookup: dict = {}
    if prev_df is not None and "product_name" in prev_df.columns:
        for pn, pg in prev_df.groupby("product_name"):
            prev_lookup[pn] = {
                "review_count": len(pg),
                "avg_rating": round(float(pg["rating"].mean()), 2),
            }

    products: List[dict] = []

    for product_name, group in df.groupby("product_name"):
        # 빈 그룹 방어 (GroupBy 결과는 이론상 비어 있지 않지만 안전하게 처리)
        if group.empty:
            continue

        # .iat[0] 사용으로 IndexError 방어 (iloc[0] 대체)
        pid = str(group["product_id"].iat[0]) if "product_id" in group.columns else ""
        raw_name = (
            str(group["product_name_raw"].iat[0])
            if "product_name_raw" in group.columns
            else str(product_name)
        )
        price_series = (
            group["product_price"].dropna()
            if "product_price" in group.columns
            else pd.Series(dtype=float)
        )
        price = price_series.median() if not price_series.empty else None

        # 별점 분포 (벡터화)
        rc = group["rating"].value_counts().reindex(range(1, 6), fill_value=0)
        rating_dist: dict = {str(r): int(rc[r]) for r in range(1, 6)}

        photo_cnt = (
            int(group["photo_count"].fillna(0).gt(0).sum())
            if "photo_count" in group.columns
            else 0
        )

        # 감성 분포 (벡터화)
        if "sentiment" in group.columns:
            sc = group["sentiment"].value_counts()
            sentiment: dict = {
                "positive": int(sc.get("positive", 0)),
                "neutral": int(sc.get("neutral", 0)),
                "negative": int(sc.get("negative", 0)),
            }
        else:
            sentiment = {
                "positive": int(group["rating"].ge(4).sum()),
                "neutral": int(group["rating"].eq(3).sum()),
                "negative": int(group["rating"].le(2).sum()),
            }

        # 대표 리뷰 (최신 + 별점 높은 순) — itertuples 사용으로 성능 개선
        top = group.sort_values(
            ["rating", "review_date"], ascending=[False, False]
        ).head(top_review_count)

        top_reviews: List[dict] = []
        for row in top.itertuples(index=False):
            date_str = ""
            rv_date = getattr(row, "review_date", None)
            if rv_date is not None and pd.notna(rv_date):
                date_str = pd.Timestamp(rv_date).strftime("%Y-%m-%d")
            top_reviews.append({
                "review_id": str(getattr(row, "review_id", "")),
                "text": str(getattr(row, "body", ""))[:300],
                "rating": int(getattr(row, "rating", 0)),
                "date": date_str,
            })

        # 부정 리뷰 샘플 (별점 ≤2 우선, 부족하면 ≤3 보충, 최대 5건)
        bottom_reviews: List[dict] = []
        low = group[group["rating"] <= 2].sort_values(
            ["rating", "review_date"], ascending=[True, False]
        ).head(top_review_count)
        if len(low) < 3:
            extra = group[(group["rating"] <= 3) & (group["rating"] > 2)].sort_values(
                ["rating", "review_date"], ascending=[True, False]
            ).head(top_review_count - len(low))
            low = pd.concat([low, extra], ignore_index=True)
        for row in low.itertuples(index=False):
            date_str = ""
            rv_date = getattr(row, "review_date", None)
            if rv_date is not None and pd.notna(rv_date):
                date_str = pd.Timestamp(rv_date).strftime("%Y-%m-%d")
            bottom_reviews.append({
                "review_id": str(getattr(row, "review_id", "")),
                "text": str(getattr(row, "body", ""))[:300],
                "rating": int(getattr(row, "rating", 0)),
                "date": date_str,
            })

        total = len(group)
        pos_r = round(sentiment["positive"] / total * 100, 2) if total > 0 else 0.0
        neg_r = round(sentiment["negative"] / total * 100, 2) if total > 0 else 0.0

        products.append({
            "id": pid,
            "name": str(product_name),
            "raw_name": raw_name,
            "price": int(price) if price is not None and not pd.isna(price) else None,
            "review_count": total,
            "avg_rating": round(float(group["rating"].mean()), 2),
            "rating_distribution": rating_dist,
            "photo_count": photo_cnt,
            "sentiment": sentiment,
            "positive_rate": pos_r,
            "negative_rate": neg_r,
            "prev_review_count": prev_lookup[product_name]["review_count"] if product_name in prev_lookup else None,
            "prev_avg_rating": prev_lookup[product_name]["avg_rating"] if product_name in prev_lookup else None,
            "top_reviews": top_reviews,
            "bottom_reviews": bottom_reviews,
        })

    products.sort(key=lambda x: x["review_count"], reverse=True)
    return products


# ────────────────────────────────────────────
# 키워드 분석 (AI 없는 기본 버전)
# ────────────────────────────────────────────

# 한국어 불용어 목록
STOPWORDS: frozenset = frozenset({
    # 조사
    "이", "가", "을", "를", "은", "는", "에", "의", "와", "과", "도", "로", "으로",
    "에서", "에게", "한테", "에서도", "까지", "부터",
    # 접속사/연결어
    "이고", "이며", "하고", "하여", "해서", "하지만", "그리고", "그런데", "근데",
    "그래서", "때문에", "이라서", "라서", "아서", "어서",
    # 형식어
    "있어요", "있어", "있는", "없어요", "없어", "같아요", "같아", "같은",
    "이에요", "예요", "이네요", "네요", "이죠", "죠",
    # 부사
    "정말", "너무", "매우", "아주", "좀", "더", "잘", "잘못", "그냥",
    "많이", "자주", "계속", "항상", "특히", "확실히", "진짜", "엄청",
    "약간", "조금", "조금씩", "꽤", "상당히", "다소",
    # 의존명사/일반명사
    "것", "거", "게", "수", "등", "및", "또", "다", "만", "뿐",
    "이번", "번", "개", "번째", "처음", "마지막", "이거", "이건", "이것",
    # 동사/형용사 어간
    "합니다", "했어요", "해요", "해서", "했습니다", "입니다", "됩니다",
    "했는데", "하는데", "인데", "인지", "라고", "이라고",
    "쓰면", "쓰고", "써도", "쓰는", "쓰다가", "써보니",
    "사용해", "사용하면", "사용하고", "사용하니", "사용했는데",
    "느낌이에요", "느낌이", "느낌은",
    "같이", "함께", "바로", "이미", "아직", "벌써",
    "않고", "없이", "없는", "없을",
    "받고", "받아서", "오고", "왔는데",
    # 브랜드/상품 일반어
    "슬룸", "제품", "상품", "구매", "사용", "리뷰",
    "배송", "포장", "택배",
    # 기타 빈출 무의미어
    "그", "이", "저", "것도", "거도", "되는", "되어", "되었", "됩니다",
    "분들한테", "분이", "분들", "분들도",
})

# ── 불만(complaint) 패턴 정의 ──
COMPLAINT_PATTERNS: List[tuple] = [
    ("소음",        r"소음이?|소리.*크|시끄럽|웅.*소리|귀.*울림|머리.*울",        "소음/시끄러움"),
    ("환불반품",    r"환불.*안|반품.*안|반품.*힘|환불.*못|환불.*불가|반품.*불가|개봉.*반품",  "환불·반품 불가"),
    ("효과없음",    r"효과.*없|시원하지.*않|별로|아무.*효과|효용.*못|모르겠|느껴지지.*않|느낌이.*없|약해|강도.*약",  "효과 미흡"),
    ("AS문제",     r"AS.*안|as.*안|고객.*과실|상담원.*연결.*안|전화.*연결.*안|전화.*수신.*거절|ai.*상담만",  "AS/고객서비스 문제"),
    ("고장",        r"고장났|작동.*안|작동이.*안|일주일.*고장|한달.*고장|전원.*안",   "고장/작동불량"),
    ("과장광고",    r"광고.*다르|과대광고|속아|광고와|과장",                        "과장광고"),
    ("높이불편",    r"높이.*높|높아서.*불편|너무.*높|높이.*불편",                   "높이 불편"),
    ("진동두통",    r"진동.*골|골이.*울|두통|머리.*울려|골이.*울려",               "진동 두통"),
    ("AI상담",     r"AI.*상담|ai.*상담|상담원.*연결.*안되|전화.*안됨",              "AI상담·전화 불가"),
    ("가격대비",    r"가격.*비싸|돈.*아까|돈.*비해|비싸고|가성비.*나쁜",           "가격 대비 효과"),
    ("충전불편",    r"전원코드.*연결|코드.*연결.*사용|무선.*아니|충전.*안되|배터리.*없",  "유선/충전 불편"),
]

# ── 칭찬(praise) 패턴 ──
PRAISE_PATTERNS: List[tuple] = [
    ("효과좋음",   r"효과.*좋|효과.*있|확실히.*효과|효과적|개운|시원하고|시원해요|시원합니다",  "효과 좋음"),
    ("편안함",     r"편안|편하게|편히|편한|부드럽게|부드러운",                      "편안함"),
    ("품질좋음",   r"품질.*좋|품질.*훌륭|퀄리티.*좋|만들어진",                      "품질 좋음"),
    ("디자인",     r"디자인.*예쁘|디자인.*좋|예쁘게|예쁜|고급스럽|깔끔",            "디자인"),
    ("배송빠름",   r"배송.*빠르|배송.*빠름|배송.*빨리|빠른.*배송",                  "빠른 배송"),
    ("온열기능",   r"온열.*좋|따뜻하게|따뜻한|온열이랑|온열기능",                   "온열 기능"),
    ("에어백",     r"에어백.*좋|에어백.*강도|에어백.*세게|에어백.*효과",             "에어백 마사지"),
    ("추천",       r"추천합니다|추천해요|강력.*추천|적극.*추천|꼭.*추천",            "추천"),
    ("만족",       r"만족합니다|만족해요|만족스럽|대만족|최고.*만족",               "만족"),
    ("선물추천",   r"선물.*좋|선물.*추천|부모님|어머니|아버지",                      "선물 추천"),
]

# ── 개선요청(improvement) 패턴 ──
IMPROVEMENT_PATTERNS: List[tuple] = [
    ("강도개선",   r"강도.*쎈|강도.*강하|더.*강하|강도.*높|강도.*세|세게.*해|강하게.*해줬으면",  "강도 강화 요청"),
    ("무선충전",   r"무선이라면|충전.*되면|무선.*있으면|배터리.*있으면|무선으로.*바꿔",          "무선/충전 개선"),
    ("소음개선",   r"소음.*줄|소음.*낮|조용하게|소리.*작게",                          "소음 개선"),
    ("리모컨",     r"리모컨.*있으면|리모컨.*없|리모컨.*추가|별도.*리모컨",             "리모컨 추가"),
    ("높이조절",   r"높이.*조절|높이.*다양|높이.*선택|낮은.*버전",                    "높이 조절 기능"),
    ("AS개선",    r"AS.*개선|상담원.*연결|전화.*연결|반품.*가능하게",                 "AS·반품 정책 개선"),
    ("경량화",     r"가벼웠으면|가볍게|무거워|무겁지.*않|경량",                       "경량화"),
]

# 패턴 사전 컴파일 (모듈 로드 시 1회만 실행)
_COMPILED_COMPLAINT: List[tuple] = [
    (cat, re.compile(pat, re.IGNORECASE), label)
    for cat, pat, label in COMPLAINT_PATTERNS
]
_COMPILED_PRAISE: List[tuple] = [
    (cat, re.compile(pat, re.IGNORECASE), label)
    for cat, pat, label in PRAISE_PATTERNS
]
_COMPILED_IMPROVEMENT: List[tuple] = [
    (cat, re.compile(pat, re.IGNORECASE), label)
    for cat, pat, label in IMPROVEMENT_PATTERNS
]

_KOREAN_WORD_RE: re.Pattern = re.compile(r"[가-힣]{2,8}")


def _tokenize(text: str) -> List[str]:
    """2~8자 한글 어절 추출 + 불용어 제거."""
    return [w for w in _KOREAN_WORD_RE.findall(text) if w not in STOPWORDS]


def _count_keywords(source_df: pd.DataFrame, top_n: int) -> List[dict]:
    """
    어절 빈도 카운트 + 리뷰 ID 연결 (문서 단위 TF).
    zip() 대신 itertuples()로 행 순회 — 약 3~4배 빠름.
    """
    counter: Counter = Counter()
    word_to_ids: dict = defaultdict(list)

    has_review_id = "review_id" in source_df.columns
    cols = ["body", "review_id"] if has_review_id else ["body"]
    sub = source_df[cols].copy()
    sub["body"] = sub["body"].fillna("").astype(str)
    if has_review_id:
        sub["review_id"] = sub["review_id"].astype(str)

    for row in sub.itertuples(index=False):
        rid = row.review_id if has_review_id else str(row.Index if hasattr(row, "Index") else "")
        for word in set(_tokenize(row.body)):
            counter[word] += 1
            word_to_ids[word].append(rid)

    return [
        {"word": word, "count": cnt, "reviews": word_to_ids[word][:10]}
        for word, cnt in counter.most_common(top_n)
    ]


def _match_compiled_patterns(
    compiled_patterns: List[tuple],
    texts: List[str],
    review_ids: List[str],
) -> List[dict]:
    """
    사전 컴파일된 패턴 리스트로 매칭 수행.
    벡터화: pandas Series.str.contains 활용.

    Returns 각 항목:
      - word, count, category, reviews(샘플 ID 30개), _all_review_ids(전체),
        _matched_indices(매칭된 source DataFrame 인덱스)
    """
    text_series = pd.Series(texts, dtype=str)
    results: List[dict] = []

    for category, compiled, label in compiled_patterns:
        mask = text_series.str.contains(compiled, na=False)
        matched_indices = mask[mask].index.tolist()
        matched_ids = [review_ids[i] for i in matched_indices]
        if matched_ids:
            results.append({
                "word": label,
                "count": len(matched_ids),
                "category": category,
                "reviews": matched_ids[:30],
                "_all_review_ids": matched_ids,
                "_matched_indices": matched_indices,  # source df의 row index
                "_pattern": compiled.pattern,         # 검증용
            })

    results.sort(key=lambda x: x["count"], reverse=True)
    return results


def extract_keywords_basic(df: pd.DataFrame, top_n: int = 30) -> dict:
    """
    형태소 분석기 없이 어절 빈도 + 패턴 기반 키워드 추출.

    Returns:
        {
          "negative_keywords": [...],    # 1~2점 리뷰 어절 빈도
          "low_rating_keywords": [...],  # 1~3점 리뷰 어절 빈도
          "positive_keywords": [...],    # 4~5점 리뷰 어절 빈도
          "by_intent": {
              "praise": [...],
              "complaint": [...],
              "improvement": [...]
          }
        }
    """
    low_df = df[df["rating"].le(3)].reset_index(drop=True)
    neg_df = df[df["rating"].le(2)].reset_index(drop=True)
    pos_df = df[df["rating"].ge(4)].reset_index(drop=True)

    all_texts = df["body"].fillna("").astype(str).tolist()
    low_texts = low_df["body"].fillna("").astype(str).tolist()

    # review_id 리스트 (패턴 매칭용)
    all_ids = (
        df["review_id"].astype(str).tolist()
        if "review_id" in df.columns
        else [str(i) for i in range(len(df))]
    )
    low_ids = (
        low_df["review_id"].astype(str).tolist()
        if "review_id" in low_df.columns
        else [str(i) for i in range(len(low_df))]
    )

    praise_items = _match_compiled_patterns(_COMPILED_PRAISE, all_texts, all_ids)
    complaint_items = _match_compiled_patterns(_COMPILED_COMPLAINT, low_texts, low_ids)
    improvement_items = _match_compiled_patterns(_COMPILED_IMPROVEMENT, all_texts, all_ids)

    # review_id → product_name 매핑 생성 (by_product 분포 계산용)
    if "product_name" in df.columns and "review_id" in df.columns:
        id_to_product = dict(zip(
            df["review_id"].astype(str),
            df["product_name"].astype(str),
        ))
    else:
        id_to_product = {}

    def _attach_by_product(items: List[dict]) -> List[dict]:
        """각 키워드 항목에 by_product 분포 추가."""
        for item in items:
            review_ids = item.get("_all_review_ids") or item.get("reviews", [])
            counts: dict = {}
            for rid in review_ids:
                prod = id_to_product.get(str(rid))
                if prod:
                    counts[prod] = counts.get(prod, 0) + 1
            sorted_counts = sorted(counts.items(), key=lambda x: -x[1])
            item["by_product"] = [
                {"product": p, "count": c} for p, c in sorted_counts
            ]
        return items

    def _attach_review_samples(
        items: List[dict],
        source_df: pd.DataFrame,
        max_samples: int = 15,
    ) -> List[dict]:
        """각 키워드 항목에 매칭된 리뷰의 본문/별점/날짜/상품을 추가.

        정밀도 보장:
          1. _matched_indices로 source_df에서 본문 직접 추출 (정확한 매칭만)
          2. 본문에 패턴이 실제로 포함되는지 재검증 (false positive 차단)
          3. 최대 max_samples개만 저장 (파일 크기 관리)
        """
        for item in items:
            matched_idx = item.pop("_matched_indices", [])
            pattern_str = item.pop("_pattern", "")
            samples: List[dict] = []
            if matched_idx and pattern_str:
                # 재컴파일 (검증용)
                verify_re = re.compile(pattern_str)
                # 우선순위: 다양한 상품/별점이 골고루 표시되도록 정렬
                # 일단 인덱스 순회하며 검증된 것만 채집
                seen_products: dict = {}
                for idx in matched_idx:
                    if len(samples) >= max_samples:
                        break
                    try:
                        row = source_df.iloc[idx]
                    except (IndexError, KeyError):
                        continue
                    text = str(row.get("body", "") if hasattr(row, "get") else row["body"])
                    # 패턴 재검증 — 본문에 실제 포함되는지
                    if not verify_re.search(text):
                        continue
                    # 상품별 분포 균등화 (한 상품 최대 8건 — 다양성과 전체 보기 균형)
                    prod_name = str(row["product_name"]) if "product_name" in source_df.columns else ""
                    if seen_products.get(prod_name, 0) >= 8:
                        continue
                    seen_products[prod_name] = seen_products.get(prod_name, 0) + 1
                    # 날짜 포맷
                    date_str = ""
                    rv_date = row.get("review_date") if hasattr(row, "get") else (row["review_date"] if "review_date" in source_df.columns else None)
                    if rv_date is not None and pd.notna(rv_date):
                        try:
                            date_str = pd.Timestamp(rv_date).strftime("%Y-%m-%d")
                        except Exception:
                            date_str = ""
                    samples.append({
                        "review_id": str(row["review_id"]) if "review_id" in source_df.columns else "",
                        "rating": int(row["rating"]) if "rating" in source_df.columns and pd.notna(row["rating"]) else 0,
                        "date": date_str,
                        "text": text[:300],
                        "product": prod_name,
                    })
            item["review_samples"] = samples
            # 전체 매칭 리뷰 ID 보존 — 대시보드 '전체 보기'(reviews.json 조회) + 재분류용
            all_ids = item.pop("_all_review_ids", None)
            if all_ids is None:
                all_ids = item.get("reviews", [])
            item["all_review_ids"] = [str(x) for x in all_ids]
        return items

    # 적용 순서: by_product 먼저 (_all_review_ids 사용) → review_samples (_matched_indices 사용)
    praise_top = praise_items[:top_n]
    complaint_top = complaint_items[:top_n]
    improvement_top = improvement_items[:top_n]
    _attach_by_product(praise_top)
    _attach_by_product(complaint_top)
    _attach_by_product(improvement_top)
    _attach_review_samples(praise_top, df, max_samples=50)
    _attach_review_samples(complaint_top, low_df, max_samples=50)
    _attach_review_samples(improvement_top, df, max_samples=50)

    return {
        "negative_keywords": _count_keywords(neg_df, top_n),
        "low_rating_keywords": _count_keywords(low_df, top_n),
        "positive_keywords": _count_keywords(pos_df, top_n),
        "by_intent": {
            "praise": praise_top,
            "complaint": complaint_top,
            "improvement": improvement_top,
        },
    }


# ────────────────────────────────────────────
# AI 재분류 — 키워드 리뷰 샘플 오매칭 제거
# ────────────────────────────────────────────

def reclassify_keyword_samples(
    keywords_data: dict,
    model: str,
    base_url: str,
    mode: str = "batch",
) -> None:
    """by_intent 각 키워드의 review_samples를 LLM으로 재검증해 오매칭 제거 (in-place).

    어휘(정규식) 매칭은 "강도가 적당하다"(긍정)를 "강도 불량"(부정) 키워드에
    잘못 붙이는 등 false positive가 잦다. Ollama로 각 샘플이 실제 그 키워드
    주제를 (해당 극성으로) 다루는지 판별해 통과한 것만 남긴다.

    --skip-ai 와 무관하게 동작하도록 자체 analyzer를 초기화한다.
    """
    try:
        from ollama_analysis import OllamaAnalyzer  # type: ignore[import]
    except ImportError:
        logger.warning("ollama_analysis 모듈 없음 → 재분류 건너뜀")
        return

    analyzer = OllamaAnalyzer(model=model, base_url=base_url)
    if not analyzer.health_check():
        logger.warning("Ollama 응답 없음 → 재분류 건너뜀 (ollama serve 확인)")
        return

    bi = keywords_data.get("by_intent", {})
    polarity_map = {"praise": "긍정", "complaint": "부정", "improvement": "개선"}
    total_removed = 0
    total_kept = 0
    for key, polarity in polarity_map.items():
        items = bi.get(key, [])
        for item in items:
            samples = item.get("review_samples", [])
            if not samples:
                continue
            before = len(samples)
            try:
                kept = analyzer.verify_keyword_reviews(
                    word=str(item.get("word", "")),
                    polarity=polarity,
                    samples=samples,
                    mode=mode,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("재분류 실패('%s'), 원본 유지: %s", item.get("word", ""), exc)
                continue
            item["review_samples"] = kept
            item["ai_reclassified"] = True
            removed = before - len(kept)
            total_removed += removed
            total_kept += len(kept)
            if removed:
                logger.info(
                    "  재분류 [%s] '%s': %d→%d (-%d)",
                    polarity, item.get("word", ""), before, len(kept), removed,
                )
    logger.info(
        "AI 재분류 완료: %d건 유지 / %d건 오매칭 제거 (mode=%s)",
        total_kept, total_removed, mode,
    )


def reclassify_keyword_full(
    keywords_data: dict,
    df: pd.DataFrame,
    model: str,
    base_url: str,
    mode: str = "batch",
    cand_cap: int = 400,
) -> None:
    """전체 리뷰에서 각 키워드 멤버십을 재도출 (재할당 + 카운트 재계산, in-place).

    어휘 매칭 결과(현재 all_review_ids)에 갇히지 않고, 전체 월 리뷰에서
    키워드 토큰을 포함하는 후보를 모은 뒤 AI로 귀속 여부를 판별한다.
    → 미매핑/타키워드 리뷰를 끌어오고(재할당), false positive는 제외하며,
      count·all_review_ids·review_samples·by_product 를 다시 계산한다.

    비용이 크므로(키워드×후보) cand_cap 으로 키워드별 후보 상한을 둔다.
    """
    try:
        from ollama_analysis import OllamaAnalyzer  # type: ignore[import]
    except ImportError:
        logger.warning("ollama_analysis 모듈 없음 → 전체 재분류 건너뜀")
        return
    analyzer = OllamaAnalyzer(model=model, base_url=base_url)
    if not analyzer.health_check():
        logger.warning("Ollama 응답 없음 → 전체 재분류 건너뜀")
        return

    # id → 리뷰 메타
    reviews: dict = {}
    for row in df.itertuples(index=False):
        rid = str(getattr(row, "review_id", ""))
        if not rid:
            continue
        date_str = ""
        rv_date = getattr(row, "review_date", None)
        if rv_date is not None and pd.notna(rv_date):
            try:
                date_str = pd.Timestamp(rv_date).strftime("%Y-%m-%d")
            except Exception:
                date_str = ""
        reviews[rid] = {
            "text": str(getattr(row, "body", "")),
            "product": str(getattr(row, "product_name", "")),
            "rating": int(getattr(row, "rating")) if pd.notna(getattr(row, "rating", None)) else 0,
            "date": date_str,
        }

    bi = keywords_data.get("by_intent", {})
    polarity_map = {"praise": "긍정", "complaint": "부정", "improvement": "개선"}
    for key, polarity in polarity_map.items():
        for item in bi.get(key, []):
            word = str(item.get("word", ""))
            if not word:
                continue
            toks = [t.lower() for t in re.split(r"[\s/·,()→.\-]+", word) if len(t) >= 2]
            cur = set(str(x) for x in item.get("all_review_ids", []))
            cand = set(cur)
            if toks:
                for rid, rv in reviews.items():
                    tl = rv["text"].lower()
                    if any(t in tl for t in toks):
                        cand.add(rid)
            cand_ids = [r for r in cand if r in reviews]
            if len(cand_ids) > cand_cap:
                # 현재 멤버 우선 유지 + 신규 후보 일부
                cur_in = [r for r in cand_ids if r in cur]
                new_in = [r for r in cand_ids if r not in cur]
                cand_ids = cur_in + new_in[: max(0, cand_cap - len(cur_in))]
            samples = [{"review_id": r, "text": reviews[r]["text"], "rating": reviews[r]["rating"]} for r in cand_ids]
            before = len(cur)
            try:
                kept = analyzer.verify_keyword_reviews(word, polarity, samples, mode)
            except Exception as exc:  # noqa: BLE001
                logger.warning("전체 재분류 실패('%s'), 유지: %s", word, exc)
                continue
            kept_ids = [str(s.get("review_id")) for s in kept if s.get("review_id")]
            # 재구성
            item["all_review_ids"] = kept_ids
            item["count"] = len(kept_ids)
            item["ai_reclassified"] = True
            # review_samples 재구성 (상품당 최대 8, 총 50)
            new_samples: List[dict] = []
            per_prod: dict = {}
            for rid in kept_ids:
                if len(new_samples) >= 50:
                    break
                rv = reviews.get(rid)
                if not rv:
                    continue
                pn = rv["product"]
                if per_prod.get(pn, 0) >= 8:
                    continue
                per_prod[pn] = per_prod.get(pn, 0) + 1
                new_samples.append({
                    "review_id": rid, "rating": rv["rating"], "date": rv["date"],
                    "text": rv["text"][:300], "product": pn,
                })
            item["review_samples"] = new_samples
            # by_product 재계산
            bp: dict = {}
            for rid in kept_ids:
                rv = reviews.get(rid)
                if rv and rv["product"]:
                    bp[rv["product"]] = bp.get(rv["product"], 0) + 1
            item["by_product"] = [{"product": p, "count": c} for p, c in sorted(bp.items(), key=lambda x: -x[1])]
            added = len([r for r in kept_ids if r not in cur])
            removed = len([r for r in cur if r not in set(kept_ids)])
            logger.info(
                "  전체재분류 [%s] '%s': %d→%d (신규 +%d · 제외 -%d)",
                polarity, word, before, len(kept_ids), added, removed,
            )
    logger.info("전체 재분류 완료 (mode=%s, cand_cap=%d)", mode, cand_cap)


# ────────────────────────────────────────────
# 전체 리뷰 인덱스 (reviews.json)
# ────────────────────────────────────────────

def build_reviews_index(df: pd.DataFrame, max_body: int = 600) -> dict:
    """해당 월 전체 리뷰를 대시보드용 경량 인덱스로 변환.

    review_id 로 조회 가능한 형태로 저장한다. (익명화 본문, PII 컬럼 미포함)
    형식: {"count": N, "reviews": {review_id: {rating, date, product, text}}}
    """
    reviews: dict = {}
    has_rid = "review_id" in df.columns
    has_date = "review_date" in df.columns
    has_prod = "product_name" in df.columns
    has_rating = "rating" in df.columns
    has_sent = "sentiment" in df.columns  # AI 감성분석 결과(positive/neutral/negative)
    has_src = "sentiment_src" in df.columns  # 'rating'=타임아웃 별점 폴백(나중에 재처리 대상)
    for row in df.itertuples(index=False):
        rid = str(getattr(row, "review_id", "")) if has_rid else ""
        if not rid:
            continue
        date_str = ""
        if has_date:
            rv_date = getattr(row, "review_date", None)
            if rv_date is not None and pd.notna(rv_date):
                try:
                    date_str = pd.Timestamp(rv_date).strftime("%Y-%m-%d")
                except Exception:
                    date_str = ""
        rec = {
            "rating": int(getattr(row, "rating")) if has_rating and pd.notna(getattr(row, "rating", None)) else 0,
            "date": date_str,
            "product": str(getattr(row, "product_name", "")) if has_prod else "",
            "text": str(getattr(row, "body", ""))[:max_body],
        }
        # 리뷰별 AI 감성 라벨 — 대시보드의 '감성기준' 토글이 사용(없으면 별점기준 폴백).
        if has_sent:
            sv = getattr(row, "sentiment", None)
            if sv is not None and pd.notna(sv):
                s = str(sv).strip().lower()
                rec["sentiment"] = s if s in ("positive", "neutral", "negative") else "neutral"
                # 타임아웃으로 별점 폴백된 건만 표시 → 나중에 patch_sentiment 로 재처리 대상
                if has_src and str(getattr(row, "sentiment_src", "")) == "rating":
                    rec["sentiment_src"] = "rating"
        reviews[rid] = rec
    return {"count": len(reviews), "reviews": reviews}


# ────────────────────────────────────────────
# JSON 저장
# ────────────────────────────────────────────

def save_json(data: object, path: Path) -> None:
    """JSON 파일 저장 (예쁜 형식, UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    size_kb = path.stat().st_size / 1024
    logger.info("  저장: %s (%.1f KB)", path, size_kb)


def update_index_json(brand: str, month: str, docs_root: Path) -> None:
    """
    docs/data/index.json 을 업데이트.
    브랜드/월 목록을 누적 관리한다.
    """
    index_path = docs_root / "index.json"
    index: dict = {"brands": {}}

    if index_path.exists():
        try:
            with open(index_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("brands"), dict):
                index = loaded
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("index.json 읽기 실패, 새로 생성합니다: %s", exc)

    if brand not in index["brands"]:
        index["brands"][brand] = {"months": []}

    months: List[str] = index["brands"][brand]["months"]
    if month not in months:
        months.append(month)
        months.sort(reverse=True)

    index["last_updated"] = datetime.now().isoformat()
    save_json(index, index_path)


# ────────────────────────────────────────────
# 진행률 표시 헬퍼
# ────────────────────────────────────────────

class Progress:
    """tqdm 없이 동작하는 간단한 진행률 표시기. 컨텍스트 매니저로도 사용 가능."""

    def __init__(self, total: int, desc: str = "처리 중") -> None:
        self.total = total
        self.desc = desc
        self.current = 0
        self.start = time.monotonic()

    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        # 완료 처리 — 진행 중 예외 발생 시에도 줄바꿈 보장
        if self.current < self.total:
            print()

    def update(self, n: int = 1) -> None:
        self.current = min(self.current + n, self.total)
        pct = self.current / self.total * 100 if self.total else 0.0
        elapsed = time.monotonic() - self.start
        bar_len = 30
        filled = int(bar_len * self.current / self.total) if self.total else 0
        bar = "=" * filled + "-" * (bar_len - filled)
        print(
            f"\r  {self.desc} [{bar}] {self.current}/{self.total} ({pct:.0f}%) {elapsed:.1f}s",
            end="",
            flush=True,
        )
        if self.current >= self.total:
            print()


# ────────────────────────────────────────────
# 메인 파이프라인
# ────────────────────────────────────────────

def run_pipeline(args: argparse.Namespace) -> None:
    t_start = time.monotonic()

    # ── 입력값 검증
    try:
        brand = validate_brand(args.brand)
        month = validate_month(args.month)
    except ValueError as exc:
        logger.error("입력값 오류: %s", exc)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("  크리마 리뷰 처리 파이프라인 시작")
    logger.info("  브랜드: %s | 월: %s", brand, month)
    logger.info("=" * 60)

    # ── 1. CSV 로드
    csv_path = Path(args.input).resolve()
    if not csv_path.exists():
        logger.error("CSV 파일을 찾을 수 없습니다: %s", csv_path)
        sys.exit(1)

    try:
        df = load_csv(csv_path)
    except RuntimeError as exc:
        logger.error("CSV 로드 실패: %s", exc)
        sys.exit(1)

    # ── 2. 전월 CSV (있으면 로드)
    prev_df: Optional[pd.DataFrame] = None
    if args.prev_input:
        prev_path = Path(args.prev_input).resolve()
        if prev_path.exists():
            logger.info("전월 데이터 로드: %s", prev_path)
            try:
                prev_df = load_csv(prev_path)
            except RuntimeError as exc:
                logger.warning("전월 CSV 로드 실패 (건너뜀): %s", exc)
        else:
            logger.warning("전월 CSV 없음: %s", prev_path)

    # ── 3. AI 감성 분석 (--skip-ai 없을 때)
    skip_ai: bool = args.skip_ai
    analyzer = None
    if not skip_ai:
        logger.info("AI 분석: Ollama 감성 분석 시작...")
        try:
            from ollama_analysis import OllamaAnalyzer  # type: ignore[import]

            analyzer = OllamaAnalyzer(model=args.ollama_model, base_url=args.ollama_url)
            if analyzer.health_check():
                # 배치당 하드 타임아웃 — Ollama 가 hang 해도 무한 대기 방지(과거 7시간 멈춤 재발 방지).
                # 연속 타임아웃 N회면 감성분석 중단 → 나머지는 라벨 없음(대시보드 별점 폴백).
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FT
                SENT_BATCH_TIMEOUT = 60   # 초/배치
                SENT_MAX_CONSEC = 3       # 연속 타임아웃 허용 횟수 → 초과 시 중단

                def _rating_sent(rt):
                    """타임아웃/오류 시 그 리뷰의 별점으로 감성 폴백(집계 일관성 유지)."""
                    try: rt = int(rt)
                    except Exception: rt = 0
                    return {"sentiment": "positive" if rt >= 4 else ("neutral" if rt == 3 else "negative"),
                            "score": 0.5, "reason": "별점 폴백(감성 타임아웃)", "src": "rating"}

                progress = Progress(len(df), desc="감성 분석")
                sentiments: List[dict] = []
                batch_size = 5
                _ex = ThreadPoolExecutor(max_workers=1)
                _consec = 0
                _aborted = False
                _fallback_cnt = 0

                for i in range(0, len(df), batch_size):
                    batch = df.iloc[i: i + batch_size]
                    ratings = batch["rating"].tolist()
                    if _aborted:
                        sentiments.extend([_rating_sent(rt) for rt in ratings])  # 나머지는 별점 폴백
                        _fallback_cnt += len(batch)
                        progress.update(len(batch))
                        continue
                    _fut = _ex.submit(analyzer.analyze_sentiment_batch, batch["body"].tolist())
                    try:
                        results = _fut.result(timeout=SENT_BATCH_TIMEOUT)
                        sentiments.extend(results)
                        _consec = 0
                    except _FT:
                        _consec += 1
                        logger.warning("  감성 배치 타임아웃 (연속 %d) @%d — 별점 폴백", _consec, i)
                        try: _ex.shutdown(wait=False, cancel_futures=True)
                        except Exception: pass
                        _ex = ThreadPoolExecutor(max_workers=1)   # 멈춘 스레드 버리고 새 실행기
                        try: analyzer = OllamaAnalyzer(model=args.ollama_model, base_url=args.ollama_url)
                        except Exception: pass
                        sentiments.extend([_rating_sent(rt) for rt in ratings])
                        _fallback_cnt += len(batch)
                        if _consec >= SENT_MAX_CONSEC:
                            logger.warning("  감성 연속 타임아웃 %d회 → 감성분석 중단, 나머지는 별점 폴백", SENT_MAX_CONSEC)
                            _aborted = True
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("  감성 배치 오류 @%d: %s — 별점 폴백", i, exc)
                        sentiments.extend([_rating_sent(rt) for rt in ratings])
                        _fallback_cnt += len(batch)
                    progress.update(len(batch))

                try: _ex.shutdown(wait=False)
                except Exception: pass
                df["sentiment"] = [r.get("sentiment", "neutral") for r in sentiments]
                df["sentiment_score"] = [r.get("score", 0.5) for r in sentiments]
                df["sentiment_reason"] = [r.get("reason", "") for r in sentiments]
                df["sentiment_src"] = [r.get("src", "ai") for r in sentiments]  # 'rating'=폴백(재처리 대상)
                if _fallback_cnt:
                    logger.warning("  감성 분석 완료(%d건) — 그중 %d건은 타임아웃으로 별점 폴백", len(sentiments), _fallback_cnt)
                else:
                    logger.info("  감성 분석 완료: %d건", len(sentiments))
            else:
                logger.warning("Ollama 서버 응답 없음 → 별점 기반 감성 추정으로 대체")
                skip_ai = True

        except ImportError:
            logger.warning("ollama_analysis 모듈 없음 → AI 분석 건너뜀")
            skip_ai = True
        except RuntimeError as exc:
            logger.error("AI 분석 실패: %s → 별점 기반 추정으로 대체", exc)
            skip_ai = True
        except Exception as exc:  # noqa: BLE001
            logger.error("AI 분석 중 예상치 못한 오류: %s → 별점 기반 추정으로 대체", exc)
            skip_ai = True

    # ── 4. KPI 계산
    logger.info("KPI 계산 중...")
    kpis = calc_kpis(df, prev_df)
    timeline = calc_timeline(df)
    path_dist = calc_review_path_distribution(df)

    summary = {
        "brand": brand,
        "month": month,
        "generated_at": datetime.now().isoformat(),
        "kpis": kpis,
        "timeline": timeline,
        "review_path_distribution": path_dist,
    }

    # ── 5. 상품별 집계
    logger.info("상품별 데이터 계산 중...")
    products_list = calc_products(df, prev_df=prev_df, top_review_count=30)
    products_data = {"products": products_list}

    # ── 6. 키워드 분석
    logger.info("키워드 추출 중...")
    keywords_data = extract_keywords_basic(df, top_n=args.top_n_keywords)

    # ── 6-b. AI 재분류
    if getattr(args, "reclassify_full", False):
        # 전체 리뷰에서 재도출 (재할당 + 카운트 재계산) — 정확하지만 느림
        logger.info("AI 전체 재분류 시작 (mode=%s)...", args.reclassify_mode)
        reclassify_keyword_full(
            keywords_data, df,
            model=args.ollama_model, base_url=args.ollama_url,
            mode=args.reclassify_mode,
        )
    elif getattr(args, "reclassify", False):
        # 저장된 샘플 검증 (오매칭 제거만) — 빠름
        logger.info("AI 재분류 시작 (mode=%s)...", args.reclassify_mode)
        reclassify_keyword_samples(
            keywords_data,
            model=args.ollama_model,
            base_url=args.ollama_url,
            mode=args.reclassify_mode,
        )

    # ── 7. AI 분석 JSON (AI 실행된 경우)
    ai_analysis: Optional[dict] = None
    if not skip_ai and analyzer is not None:
        try:
            logger.info("AI 분석: 키워드 추출 및 Smart Brief 생성 중...")

            neg_texts = df[df["rating"].le(2)]["body"].tolist()
            ai_keywords = analyzer.extract_keywords(neg_texts, top_n=30)

            product_briefs: List[dict] = []
            with Progress(min(10, len(products_list)), desc="상품 브리프") as prog:
                for prod in products_list[:10]:
                    prod_df = df[df["product_name"] == prod["name"]]
                    sample_texts = prod_df["body"].dropna().tolist()[:20]
                    brief = analyzer.generate_product_brief(
                        product_name=prod["name"],
                        reviews=sample_texts,
                        avg_rating=prod["avg_rating"],
                        sentiment=prod["sentiment"],
                    )
                    product_briefs.append({
                        "product_id": prod["id"],
                        "product_name": prod["name"],
                        "brief": brief.get("brief", ""),
                        "key_insights": brief.get("key_insights", []),
                    })
                    prog.update(1)

            smart_brief = analyzer.generate_smart_brief(
                brand=brand,
                month=month,
                kpis=kpis,
                top_products=products_list[:5],
                neg_keywords=ai_keywords.get("complaint", [])[:10],
            )

            # by_intent 덮어쓰기 가드: 패턴 기반 항목은 review_samples·all_review_ids·
            # by_product 를 갖고 있어 대시보드 '전체 보기'·재분류에 필수.
            # AI 키워드(맨 단어만)로 덮어쓰면 이 정보가 소실되므로,
            # 재분류를 했거나 기존 항목이 이미 풍부하면 덮어쓰지 않는다.
            did_reclassify = getattr(args, "reclassify", False) or getattr(args, "reclassify_full", False)
            existing_rich = any(
                it.get("all_review_ids") or it.get("review_samples")
                for grp in ("praise", "complaint", "improvement")
                for it in keywords_data["by_intent"].get(grp, [])
            )
            if ai_keywords and not did_reclassify and not existing_rich:
                keywords_data["by_intent"]["praise"] = ai_keywords.get("praise", [])
                keywords_data["by_intent"]["complaint"] = ai_keywords.get("complaint", [])
                keywords_data["by_intent"]["improvement"] = ai_keywords.get("improvement", [])
            elif ai_keywords:
                # AI 키워드는 ai_analysis 에만 보존 (by_intent 는 패턴/재분류 결과 유지)
                logger.info("by_intent 덮어쓰기 생략 (review_samples/all_review_ids 보존) — AI 키워드는 ai_analysis 에 기록")

            ai_analysis = {
                "smart_brief": smart_brief,
                "product_briefs": product_briefs,
                "ai_keywords": ai_keywords,
                "sentiment_summary": {
                    "positive": kpis.get("positive_count", 0),
                    "neutral": kpis.get("neutral_count", 0),
                    "negative": kpis.get("negative_count", 0),
                    "positive_rate": kpis.get("positive_rate", 0),
                    "negative_rate": kpis.get("negative_rate", 0),
                },
                "generated_at": datetime.now().isoformat(),
                "model": args.ollama_model,
            }

        except Exception as exc:  # noqa: BLE001
            logger.error("AI 분석 JSON 생성 실패: %s", exc)

    # ── 8. JSON 저장
    docs_root = (PROJECT_ROOT / "docs" / "data").resolve()
    try:
        out_dir = resolve_safe_output_dir(docs_root, brand, month)
    except ValueError as exc:
        logger.error("출력 경오류: %s", exc)
        sys.exit(1)

    # ── 전체 리뷰 인덱스 (대시보드 '전체 보기' + 재분류용)
    reviews_index = build_reviews_index(df)

    # ── 저장
    save_json(summary, out_dir / "summary.json")
    save_json(products_data, out_dir / "products.json")
    save_json(keywords_data, out_dir / "keywords.json")
    save_json(reviews_index, out_dir / "reviews.json")
    if ai_analysis is not None:
        save_json(ai_analysis, out_dir / "ai_analysis.json")

    # ── index.json 업데이트
    update_index_json(brand, month, docs_root)

    elapsed = time.monotonic() - t_start
    logger.info("=" * 60)
    logger.info("  ✅ 완료! 처리 시간: %.1fs", elapsed)


# ─────────────────────────────────────────────────────────────
# CLI 진입점
# ─────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """CLI 인수 파싱 (CLAUDE.md: 진입점 함수명은 반드시 parse_args)."""
    p = argparse.ArgumentParser(
        description="크리마 리뷰 CSV → JSON 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--brand",   required=True, help="브랜드명 (예: 슬룸)")
    p.add_argument("--month",   required=True, help="처리 월 (YYYY-MM)")
    p.add_argument("--input",   required=True, help="원본 CSV 경로")
    p.add_argument(
        "--prev-input",
        default=None,
        help="전월 CSV 경로 (MoM 비교용, 선택)",
    )
    p.add_argument(
        "--skip-ai",
        action="store_true",
        default=False,
        help="Ollama AI 분석 건너뜀",
    )
    p.add_argument(
        "--ollama-model",
        default="exaone3.5:7.8b",
        help="Ollama 모델명 (기본: exaone3.5:7.8b)",
    )
    p.add_argument(
        "--ollama-url",
        default="http://localhost:11434",
        help="Ollama 엔드포인트 URL (기본: http://localhost:11434)",
    )
    p.add_argument(
        "--top-n-keywords",
        type=int,
        default=30,
        dest="top_n_keywords",
        help="키워드 추출 상위 N개 (기본: 30)",
    )
    p.add_argument(
        "--reclassify",
        action="store_true",
        default=False,
        help="Ollama로 키워드↔리뷰 매칭을 재검증해 오매칭 샘플 제거 (--skip-ai 와 무관하게 동작)",
    )
    p.add_argument(
        "--reclassify-mode",
        choices=["batch", "item"],
        default="batch",
        dest="reclassify_mode",
        help="재분류 강도: batch(여러 건 묶음, 빠름) | item(1건씩, 정밀). 기본 batch",
    )
    p.add_argument(
        "--reclassify-full",
        action="store_true",
        default=False,
        dest="reclassify_full",
        help="전체 리뷰에서 키워드 멤버십을 재도출(재할당+카운트 재계산). 정확하지만 매우 느림. --reclassify 보다 우선",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(args)
