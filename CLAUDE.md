# CLAUDE.md — 크리마 리뷰 대시보드

이 파일은 Claude가 이 프로젝트를 작업할 때 반드시 읽어야 하는 컨텍스트, 규칙, 과거 실수 기록입니다.

---

## 프로젝트 개요

- **목적**: 크리마 리뷰 CSV → JSON 파이프라인 → GitHub Pages 대시보드 자동화
- **주요 언어**: Python 3.11, HTML/CSS/JavaScript (단일 파일)
- **AI**: 로컬 Ollama (exaone3.5:7.8b, localhost:11434)
- **호스팅**: GitHub Pages (`/docs` 폴더 기준)
- **GitHub**: https://github.com/Lyrze/crema-review-dashboard

---

## 폴더 구조

```
crema-review-dashboard/
├── .github/workflows/process-reviews.yml   # GitHub Actions (CSV push → JSON 생성)
├── data/raw/{브랜드}/{YYYY-MM}/reviews.csv  # 원본 CSV (gitignore됨)
├── docs/                                    # GitHub Pages 서빙 루트
│   ├── index.html                           # 대시보드 (단일 HTML 파일)
│   └── data/
│       ├── index.json                       # 브랜드·월 목록
│       └── {브랜드}/{YYYY-MM}/
│           ├── summary.json
│           ├── products.json
│           └── keywords.json
├── scripts/
│   ├── process_data.py                      # 메인 파이프라인
│   └── ollama_analysis.py                   # Ollama AI 모듈
├── CLAUDE.md                                # ← 이 파일
└── README.md
```

---

## 과거 실수 & 재발 방지 규칙

### 🔴 CRITICAL: Windows 배치파일 — LF 줄바꿈 금지

**발생**: Linux 샌드박스에서 Write/bash로 `.bat` 파일을 생성하면 기본적으로 LF(`\n`) 줄바꿈으로 저장됨.  
**증상**: `'exist'은(는) 내부 또는 외부 명령이 아닙니다`, `'%i.'은(는) 내부 또는 외부 명령이 아닙니다`, 한글 깨짐, 명령어가 중간에 잘려 실행됨.

**원인**: Windows CMD는 CRLF(`\r\n`)만 올바르게 파싱한다. LF만 있으면 줄 경계를 인식 못해 여러 줄이 붙거나 명령어가 단어 단위로 쪼개짐.

**규칙**:
- `.bat` 파일은 **반드시 Python으로 `newline='\r\n'`을 명시해서 저장**한다.

```python
# 올바른 배치파일 저장 패턴
with open('script.bat', 'w', newline='\r\n', encoding='utf-8') as f:
    f.write(content)
```

- 저장 후 반드시 검증:

```python
data = open('script.bat', 'rb').read()
lf_only = data.count(b'\n') - data.count(b'\r\n')
assert lf_only == 0, f"LF-only lines found: {lf_only}"
```

---

### 🔴 CRITICAL: Windows 배치파일 — for 루프 동적 변수명

**발생**: `for /l %%i in (1,1,!COUNT!) do echo !BRAND_%%i!` 패턴이 제대로 동작하지 않음.  
**원인**: 복잡한 동적 변수 접근이 배치 내에서 불안정하고 가독성이 떨어짐.

**규칙**: 목록 스캔, 모델 열거 등 **복잡한 로직은 Python 보조 스크립트로 분리**한다.  
배치파일은 Python이 출력한 `KEY=VALUE` 형식 텍스트를 `findstr` + `for /f`로 파싱한다.

```batch
:: 올바른 패턴 — Python 스크립트가 KEY=VALUE 출력
python scripts\scan_raw.py > "%TEMP%\result.tmp"
for /f "tokens=1,2 delims==" %%A in ('findstr "^COUNT=" "%TEMP%\result.tmp"') do set "COUNT=%%B"
```

```python
# scan_raw.py 출력 형식
print(f"COUNT={len(items)}")
print(f"ITEM_1_BRAND=슬룸")
print(f"SHOW_1=1. 슬룸 / 2026-03  [미처리]")
```

---

### 🟠 HIGH: Windows 배치파일 — Python inline 코드에서 dict key 직접 참조

**발생**: `python -c "... d['total_reviews'] ..."` 처럼 따옴표가 중첩되면 배치 파서가 깨짐.  
**규칙**: 배치 내 `python -c` 코드에서 dict key는 `chr()` 조합 대신 **변수 할당 후 사용**하거나, **별도 `.py` 파일로 분리**한다.

```batch
:: 올바른 패턴
python scripts\show_result.py "!BRAND!" "!MONTH!"
```

---

### 🔴 CRITICAL: 파일 잘림 (Truncation) 문제

**발생**: `process_data.py`, `ollama_analysis.py` 등 긴 파일을 여러 번에 나눠 쓸 때 마지막 청크가 잘렸음.  
**증상**: `SyntaxError: unexpected EOF`, 함수 중간에서 파일이 끊김, `if __name__ == "__main__"` 블록 없음.

**규칙**:
- 파일을 Write 도구로 저장한 뒤 **반드시 `wc -l`과 `python3 -m py_compile`로 검증**한다.
- 300줄 이상 파일은 저장 후 `tail -20`으로 끝 부분을 확인한다.
- 함수가 중간에 끊기면 Edit 도구로 이어서 추가한다 (Write 전체 재작성보다 안전).

```bash
# 검증 패턴 (항상 실행)
wc -l scripts/process_data.py
python3 -m py_compile scripts/process_data.py && echo "OK"
tail -20 scripts/process_data.py
```

---

### 🔴 CRITICAL: process_data.py 진입점 함수명

**발생**: `parse_args()` 대신 `build_arg_parser()`를 호출해 NameError 발생.  
**규칙**: 이 파일의 CLI 진입점은 반드시 **`parse_args()`** 이다. `build_arg_parser()`, `get_args()` 등 다른 이름 사용 금지.

```python
# 올바른 패턴
if __name__ == "__main__":
    args = parse_args()
    run_pipeline(args)
```

---

### 🔴 CRITICAL: docs/data/index.json 포맷

**발생**: `brands` 필드를 리스트(`[]`)로 작성했다가 `update_index_json()`에서 `TypeError` 발생.  
**규칙**: `brands`는 반드시 **딕셔너리** 형태여야 한다.

```json
{
  "brands": {
    "슬룸": {
      "id": "sloom",
      "display_name": "슬룸",
      "months": ["2026-04"]
    }
  },
  "last_updated": "2026-05-27"
}
```

---

### 🔴 CRITICAL: GitHub Actions — 스크립트 파일명

**발생**: 워크플로우에서 `process_reviews.py`를 호출했으나 실제 파일은 `process_data.py`.  
**규칙**: GitHub Actions YAML에서 스크립트 호출 시 **반드시 `process_data.py`** 를 사용한다.

```yaml
# 올바름
python scripts/process_data.py --brand "$BRAND" --month "$MONTH" --input "$FILE"

# 틀림 (절대 사용 금지)
python scripts/process_reviews.py ...
```

---

### 🔴 CRITICAL: GitHub Actions — CSV 경로 파싱

**발생**: CSV 경로 `data/raw/{브랜드}/{월}/reviews.csv`에서 브랜드·월 추출 시 `cut`을 쓰다가 인덱스 오류 발생.  
**규칙**: `awk -F'/'`로 필드를 추출한다. 브랜드 = `$3`, 월 = `$4`.

```bash
BRAND=$(echo "$FILE" | awk -F'/' '{print $3}')
MONTH=$(echo "$FILE"  | awk -F'/' '{print $4}')
```

---

### 🟠 HIGH: 키워드 추출 — 씨앗 단어 기반 매칭 실패

**발생**: 씨앗 단어 목록 기반 매칭은 실제 리뷰에 매칭되는 경우가 드물어 `complaint` / `improvement` 결과가 항상 비어 있었음.  
**규칙**: 키워드 추출은 반드시 **정규식 패턴 그룹** (`COMPLAINT_PATTERNS`, `PRAISE_PATTERNS`, `IMPROVEMENT_PATTERNS`) 기반으로 동작한다. 씨앗 단어 단순 포함 여부 확인 방식은 사용하지 않는다.

---

### 🟠 HIGH: 상품명 정규화 — 프로모션 패턴 부족

**발생**: `normalize_product_name()`의 `PROMO_PATTERNS`가 8개뿐이어서 39개 중복 상품명이 생성됨.  
**규칙**: 패턴은 현재 21개. 새 이상한 상품명이 발생하면 `PROMO_PATTERNS` 리스트에 추가한다.  
처리 후 `products.json`의 상품 수가 비정상적으로 많으면 (예: 100개 이상) 정규화 패턴을 점검한다.

---

### 🟡 MEDIUM: ollama_analysis.py — 172.x SSRF 방어

**발생**: `172.x.x.x` 전체를 사설망으로 허용하면 실제로 172.16~31 범위가 아닌 주소도 통과됨.  
**수정**: `ipaddress.ip_address(host).is_private` (Python 표준 라이브러리) 사용으로 RFC 1918 정확히 적용.

---

### 🟡 MEDIUM: dashboard index.html — `let`/`const` 금지

**규칙**: 최상위 레벨 JS 변수는 **모두 `var`** 를 사용한다. `let`/`const`는 함수 내부에서만 허용.  
이유: 테마 변경·차트 재초기화 시 TDZ(Temporal Dead Zone) 오류 방지.

---

### 🟡 MEDIUM: Chart.js — `maintainAspectRatio: false` 필수

**규칙**: 모든 Chart.js 인스턴스에 `responsive: true, maintainAspectRatio: false` 를 설정한다.  
캔버스 컨테이너에 명시적 높이(`min-height`) 없으면 차트가 0px로 렌더링됨.

```javascript
options: {
  responsive: true,
  maintainAspectRatio: false,   // 반드시 포함
  plugins: { tooltip: { enabled: true } }
}
```

---

### 🟡 MEDIUM: `data/raw/` CSV 파일 경로 규칙

**규칙**: 원본 CSV는 반드시 `data/raw/{브랜드명}/{YYYY-MM}/reviews.csv` 구조로 저장.  
브랜드명은 한글 그대로 사용 (예: `슬룸`, `넥케어`). 파일명은 항상 `reviews.csv`.

```
data/raw/슬룸/2026-04/reviews.csv   ✅
data/raw/sloom_2026-04.csv          ❌ (구 방식, 사용 금지)
```

---

## 자주 쓰는 명령어

```bash
# 데이터 처리 (AI 없이)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-04 \
  --input data/raw/슬룸/2026-04/reviews.csv \
  --skip-ai

# 데이터 처리 (Ollama AI 포함)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-04 \
  --input data/raw/슬룸/2026-04/reviews.csv \
  --ollama-model exaone3.5:7.8b

# 전월 비교 포함
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-05 \
  --input data/raw/슬룸/2026-05/reviews.csv \
  --prev-input data/raw/슬룸/2026-04/reviews.csv \
  --skip-ai

# Python 구문 검증
python3 -m py_compile scripts/process_data.py && echo "OK"
python3 -m py_compile scripts/ollama_analysis.py && echo "OK"

# 로컬 대시보드 미리보기 (Python 내장 서버)
cd docs && python3 -m http.server 8080
# → http://localhost:8080
```

---

## Ollama 설정

- 모델: `exaone3.5:7.8b`
- 엔드포인트: `http://localhost:11434`
- `--skip-ai` 플래그로 건너뛸 수 있음 (GitHub Actions 기본값)
- 대시보드 우상단에서 연결 상태 실시간 표시

```bash
# 모델 설치
ollama pull exaone3.5:7.8b

# 서버 실행
ollama serve
```

---

## 데이터 처리 출력 파일

| 파일 | 설명 |
|------|------|
| `docs/data/index.json` | 브랜드·월 목록 (딕셔너리 형태 필수) |
| `docs/data/{브랜드}/{월}/summary.json` | KPI, 타임라인 |
| `docs/data/{브랜드}/{월}/products.json` | 상품별 통계 |
| `docs/data/{브랜드}/{월}/keywords.json` | 키워드 분석 (칭찬/불만/개선) |
| `docs/data/{브랜드}/{월}/ai_analysis.json` | Ollama AI 결과 (선택) |
