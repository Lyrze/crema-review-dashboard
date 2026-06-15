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
├── data/
│   ├── raw/{브랜드}/{YYYY-MM}/reviews.csv   # 원본 CSV (gitignore — PII 포함)
│   └── anonymized/{브랜드}/{YYYY-MM}/       # 익명화 CSV (GitHub 업로드 O)
│       └── reviews_anon.csv                # 주문번호·회원명 제거, 회원ID→해시
├── docs/                                    # GitHub Pages 서빙 루트
│   ├── index.html                           # 대시보드 (단일 HTML 파일, ~1240줄)
│   └── data/
│       ├── index.json                       # 브랜드·월 목록 (딕셔너리 형태 필수)
│       └── {브랜드}/{YYYY-MM}/
│           ├── summary.json                 # KPI, 타임라인
│           ├── products.json                # 상품별 통계 (positive_rate 포함 필수)
│           ├── keywords.json                # 키워드 분석
│           └── ai_analysis.json             # Ollama AI 결과 (선택)
├── scripts/
│   ├── process_data.py                      # 메인 파이프라인 (CLI: parse_args)
│   ├── ollama_analysis.py                   # Ollama AI 모듈 (verify_keyword_reviews 3단계 게이트)
│   ├── reverify_suspect.py                  # 의심 키워드 멤버를 14b급으로 재검증 (거짓양성 제거)
│   ├── anonymize_csv.py                     # PII 제거 익명화 스크립트
│   ├── interactive_select.py                # update-data.bat 대화형 메뉴 (Python, [3.5/4] 정밀보정 포함)
│   ├── scan_raw.py                          # data/raw 스캔 → KEY=VALUE 출력
│   ├── scan_ollama.py                       # Ollama 모델 목록 → KEY=VALUE 출력
│   └── show_result.py                       # 처리 결과 요약 출력
├── update-data.bat                          # 월별 업데이트 배치파일
├── upload.bat                               # 최초 GitHub 업로드
├── CLAUDE.md                                # ← 이 파일
└── README.md
```

---

## 과거 실수 & 재발 방지 규칙

### 🔴 CRITICAL: Windows 배치파일 — LF 줄바꿈 금지

**발생**: Linux 샌드박스에서 Write/bash로 `.bat` 파일을 생성하면 기본적으로 LF(`\n`) 줄바꿈으로 저장됨.
**증상**: `'exist'은(는) 내부 또는 외부 명령이 아닙니다`, `'%i.'은(는) 내부 또는 외부 명령이 아닙니다`, 한글 깨짐, 명령어가 중간에 잘려 실행됨.

**원인**: Windows CMD는 CRLF(`\r\n`)만 올바르게 파싱한다. LF만 있으면 줄 경계를 인식 못해 여러 줄이 붙거나 명령어가 단어 단위로 쪼개짐.

**규칙**: `.bat` 파일은 **반드시 Python으로 `newline='\r\n'`을 명시해서 저장**한다.

```python
# 올바른 배치파일 저장 패턴
with open('script.bat', 'w', newline='\r\n', encoding='utf-8') as f:
    f.write(content)

# 저장 후 반드시 검증
data = open('script.bat', 'rb').read()
lf_only = data.count(b'\n') - data.count(b'\r\n')
assert lf_only == 0, f"LF-only lines found: {lf_only}"
```

---

### 🔴 CRITICAL: Windows 배치파일 — 백슬래시+문자 이스케이프 손상 (bell 0x07 등)

**발생**: `.bat` 내용을 이스케이프를 처리하는 도구/문자열로 저장하면 `scripts\anonymize` 의 `\a` 가 **bell(0x07)** 바이트로, `\reverify` 의 `\r` 이 CR 로 변환됨. → cmd가 `scriptsnonymize_csv.py`(붙은 경로)로 인식해 **조용히 실패**(non-fatal warning이라 눈치 못 챔).

**증상**: 익명화/특정 python 호출이 "파일 없음"으로 건너뛰어짐. 화면상으론 정상처럼 보임.

**규칙**:
- `.bat` 는 **Python raw 문자열(r'''...''')로 작성**해 `open(p,'w',newline='\r\n')` 로 저장 (bash `python -c "..."` 인라인은 따옴표가 백슬래시를 먹으니 금지 — 반드시 `.py` 파일로 작성 후 실행).
- 저장 후 **비정상 제어문자 검증** 필수:
```python
d=open('x.bat','rb').read()
assert d.count(b'\x07')==0 and (d.count(b'\n')-d.count(b'\r\n'))==0
```
- Windows 리다이렉트는 `>nul` (`>/dev/null` 아님).

---

### 🔴 CRITICAL: Windows 배치파일 — for 루프 동적 변수 + 지연 확장 서브셸 버그

**발생**: `for /f ... in ('findstr "^ITEM_!SEL!_" ...')` 패턴에서 `!SEL!`이 서브셸에서 전개되지 않아 패턴 매칭 실패 → 변수 미설정 → 배치파일 오류 없이 그냥 종료됨.

**증상**: 번호를 입력해도 CMD 창이 그냥 꺼짐.

**규칙**: 배치 내 복잡한 파싱 로직은 **Python 스크립트로 완전히 분리**한다.
- `interactive_select.py`처럼 Python이 대화형 메뉴와 파싱을 전담
- Python은 `KEY=VALUE` 형식으로 stdout 출력
- 배치파일은 `for /f "usebackq tokens=1,* delims==" %%A in ("파일") do ...` 로 파일 읽기

```batch
:: 올바른 패턴
python scripts\interactive_select.py > "%TEMP%\crema_sel.tmp"
for /f "usebackq tokens=1,* delims==" %%A in ("%TEMP%\crema_sel.tmp") do (
  if "%%A"=="BRAND" set "BRAND=%%B"
)
```

---

### 🔴 CRITICAL: Windows 배치파일 — Python inline 코드 따옴표 충돌

**발생**: `python -c "... d['total_reviews'] ..."` 처럼 배치 내 `python -c` 코드에서 dict key를 직접 참조하면 배치 파서가 따옴표를 잘못 파싱함.

**규칙**: 배치 내 `python -c` 코드에서 dict key는 **별도 `.py` 파일로 분리**한다.

```batch
:: 올바른 패턴
python scripts\show_result.py "!BRAND!" "!MONTH!"
```

---

### 🔴 CRITICAL: products.json — positive_rate / negative_rate 필드 누락

**발생**: `process_data.py`의 `products.append({...})` 블록에 `positive_rate`, `negative_rate` 필드가 없었음.
**증상**: 대시보드 SKU 섹션(③④⑤)에서 `p.positive_rate`가 `undefined` → `0`으로 처리 → 테이블이 비어 보임.

**규칙**: products.json의 각 상품 객체에는 반드시 아래 필드가 있어야 한다.

```json
{
  "sentiment": {"positive": 1199, "neutral": 96, "negative": 5},
  "positive_rate": 92.23,
  "negative_rate": 0.38,
  "prev_review_count": null,
  "prev_avg_rating": null
}
```

`process_data.py` 내 계산 방식:
```python
total = len(group)
pos_r = round(sentiment["positive"] / total * 100, 2) if total > 0 else 0.0
neg_r = round(sentiment["negative"] / total * 100, 2) if total > 0 else 0.0
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
  "last_updated": "2026-05-28"
}
```

---

### 🔴 CRITICAL: GitHub Actions — 스크립트 파일명

**발생**: 워크플로우에서 `process_reviews.py`를 호출했으나 실제 파일은 `process_data.py`.
**규칙**: GitHub Actions YAML에서 스크립트 호출 시 **반드시 `process_data.py`** 를 사용한다.

---

### 🔴 CRITICAL: GitHub Actions — CSV 경로 파싱

**발생**: CSV 경로 `data/raw/{브랜드}/{월}/reviews.csv`에서 브랜드·월 추출 시 `cut`을 쓰다가 인덱스 오류 발생.
**규칙**: `awk -F'/'`로 필드를 추출한다. 브랜드 = `$3`, 월 = `$4`.

```bash
BRAND=$(echo "$FILE" | awk -F'/' '{print $3}')
MONTH=$(echo "$FILE"  | awk -F'/' '{print $4}')
```

---

### 🟠 HIGH: 대시보드 JS — 가짜(fake) MoM 데이터 사용 금지

**발생**: `renderSKUCombo()`와 `renderChangeTables()`에서 `p.review_count * 0.70` 등 계산으로 가짜 전월 데이터를 만들었음.

**규칙**:
- 전월 데이터는 `products.json`의 `prev_review_count`, `prev_avg_rating` 필드를 사용한다.
- `null`이면 "전월 없음"으로 표시한다. 절대 fake 계산값으로 대체하지 않는다.
- 콤보차트: prev가 `null`이면 **별점 분포(★1~★5) 바 차트**로 대체한다.

```javascript
// 올바른 패턴
var hasPrev = p.prev_review_count !== null && p.prev_review_count !== undefined;
if (hasPrev) {
  // 실제 MoM 차트
} else {
  // 별점 분포 차트 (대체)
}
```

---

### 🟠 HIGH: 키워드 추출 — 씨앗 단어 기반 매칭 실패

**발생**: 씨앗 단어 목록 기반 매칭은 실제 리뷰에 매칭되는 경우가 드물어 `complaint` / `improvement` 결과가 항상 비어 있었음.
**규칙**: 키워드 추출은 반드시 **정규식 패턴 그룹** (`COMPLAINT_PATTERNS`, `PRAISE_PATTERNS`, `IMPROVEMENT_PATTERNS`) 기반으로 동작한다.

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

### 🔴 CRITICAL: index.html JS — 단일따옴표 문자열 내 줄바꿈 금지

**발생**: `var prompt='...\n...'`처럼 단일따옴표(또는 이중따옴표) 문자열 내에 **raw 줄바꿈**이 포함되어 SyntaxError: Unexpected string.

**증상**: 브라우저 콘솔에 "Unexpected string" 1개, `STATE` undefined, 대시보드 전체 공백.

**규칙**: JS 문자열 내 줄바꿈은 반드시 `\n` 이스케이프 사용.

```javascript
// ❌ 잘못된 패턴 (raw 줄바꿈)
var prompt='[질문]
'+question;

// ✅ 올바른 패턴
var prompt='[질문]\n'+question;
```

---

### 🔴 CRITICAL: index.html JS — onclick 속성에서 인접 문자열 리터럴 금지

**발생**: `'...onclick="fn(''+var+'')"...'` 패턴에서 단일따옴표 안에 단일따옴표가 사용되면 문자열이 조기 종료되어 인접 문자열 리터럴 `''` 발생 → SyntaxError.

**규칙**: onclick 내 함수 인수에 따옴표가 필요하면 `\'` 이스케이프 사용.

```javascript
// ❌ 잘못된 패턴
'<button onclick="fn(''+id+'')">'+

// ✅ 올바른 패턴
'<button onclick="fn(\''+id+'\')">'+
```

**검증**: 편집 후 아래 명령으로 반드시 구문 검증.

```bash
node -e "
const fs=require('fs'),vm=require('vm'),html=fs.readFileSync('docs/index.html','utf8');
const re=/<script(?![^>]*src)[^>]*>([\s\S]*?)<\/script>/gi;
let m,s=[]; while((m=re.exec(html))!==null) s.push(m[1]);
fs.writeFileSync('_t.js',s[1]);
" && node --check _t.js && echo OK && node -e "require('fs').unlinkSync('_t.js')"
```

---

### 🟡 MEDIUM: 대시보드 index.html — 최상위 `let`/`const` 금지

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

## 익명화 파이프라인

**목적**: raw CSV(PII 포함)는 로컬에만 보관, 익명화 버전을 GitHub에 누적해 AI 분석용으로 활용

| 컬럼 처리 | 컬럼명 |
|---|---|
| 완전 제거 (PII) | 주문번호, 회원명, 추가수집정보, 적립금, 적립금지급일, 포토/동영상 URL, 리뷰code |
| 해시로 대체 | 회원ID → `사용자_익명ID` (SHA-256 12자리, 동일 솔트로 월간 추적 가능) |
| 유지 | 리뷰본문, 별점, 상품명, 상품옵션, 회원등급, 작성일 등 17개 |

**솔트**: `crema-anon-v1` (변경 시 기존 월과 ID가 달라짐 — 변경 금지)

```bash
python scripts/anonymize_csv.py \
  --input data/raw/슬룸/2026-03/reviews.csv \
  --output data/anonymized/슬룸/2026-03/reviews_anon.csv
```

---

## 대시보드 기능 현황 (2026-05-28 기준)

| 기능 | 상태 | 위치 |
|---|---|---|
| 브랜드/월 선택 | ✅ | 사이드바 |
| Ollama 연결 상태 | ✅ | 사이드바 하단 |
| Ollama 모델 선택 | ✅ | 사이드바 하단 (온라인 시 표시) |
| Executive Summary | ✅ | 섹션 ① |
| 리뷰 지표 KPI | ✅ | 섹션 ② |
| **리뷰 지표 AI 분석** | ✅ | 섹션 ② 우측 버튼 |
| **AI 인라인 추가 질문** | ✅ | 각 AI 분석 패널 하단 (분석 결과에 이어 추가 질문) |
| SKU 테이블 | ✅ | 섹션 ③ |
| **SKU 테이블 필터/정렬** | ✅ | 섹션 ③ 상단 (리뷰수/별점/긍정률 정렬, 10/50/100건+ 필터) |
| **SKU AI 분석** | ✅ | 섹션 ③ 우측 버튼 |
| SKU 콤보차트 | ✅ | 섹션 ④ (prev 있으면 MoM, 없으면 별점분포) |
| SKU 변화 테이블 | ✅ | 섹션 ⑤ (prev null이면 "전월 없음" 표시) |
| 상품 VOC | ✅ | 섹션 ⑥ |
| 구매경험 VOC | ✅ | 섹션 ⑦ |
| **상품 포커스 필터** | ✅ | 헤더 바 (특정 상품만 필터링) |
| **AI 커스텀 질문** | ✅ | 섹션 ⑧ (KPI·SKU·VOC 컨텍스트 선택 후 자유 질문) |
| **커스텀 차트 빌더** | ✅ | 섹션 ⑨ + 우하단 FAB (+) 버튼으로 모달 추가 |
| **차트 타입 7종** | ✅ | 막대/가로막대/라인/영역/점선/도넛/레이더 |
| **섹션 드래그 리오더** | ✅ | 섹션 헤더 ⠿ 핸들로 순서 변경, localStorage 저장 |
| 데이터 로드 실패 배너 | ✅ | 상단 (JSON 로드 실패 시 경고 표시) |
| 다크 테마 | ✅ | 사이드바 하단 🌙 버튼 |

---

### 🔴 CRITICAL: loadIndex — await reload() 반드시 호출

**발생**: `loadIndex()`가 `populateMonths()`만 호출하고 `reload()`를 호출하지 않아 페이지 초기 렌더링이 전혀 안 됨. KPI, SKU 등 모든 섹션이 "—" 또는 빈 상태.

**규칙**: `loadIndex()` 마지막에 반드시 `await reload();` 호출.

```javascript
// 올바른 패턴
async function loadIndex(){
  try{
    // ... 인덱스 로드 및 셀렉터 설정
  }catch(e){ /* 폴백 */ }
  populateMonths();
  await reload();  // ← 반드시 있어야 함
}
```

---

### 🔴 CRITICAL: index.html — 파일 잘림 방지

**발생**: Linux 샌드박스에서 index.html 편집 후 renderTimeline 함수 내부에서 파일이 잘렸음. process_data.py 에서도 동일하게 발생 (2회).

**규칙**: 300줄 이상 HTML/Python 파일 수정 후 반드시 검증한다.

```bash
# HTML 잘림 검증
python3 -c "
html = open('docs/index.html', encoding='utf-8').read()
assert '</html>' in html, 'HTML 닫힘 태그 없음 — 파일 잘림!'
assert 'DOMContentLoaded' in html, '초기화 핸들러 없음!'
print('OK:', len(html.splitlines()), '줄')
"

# Python 잘림 검증
python3 -m py_compile scripts/process_data.py && echo "OK"
tail -20 scripts/process_data.py  # if __name__ == '__main__' 블록 확인
```

---

### 🟠 HIGH: loadData — 로드 실패 시 사용자에게 반드시 알릴 것

**발생**: `Promise.allSettled` 사용 시 JSON 로드 실패가 조용히 REAL_DATA 폴백으로 처리됨. 사용자가 잘못된 데이터를 보고 있음을 알 수 없었음.

**규칙**: 로드 실패 시 `data-load-banner` 요소를 `display:block`으로 전환해 경고 표시.

```javascript
// 올바른 패턴
var anyFailed = res.some(function(r){ return r.status === 'rejected'; });
var banner = document.getElementById('data-load-banner');
if(banner) banner.style.display = anyFailed ? 'block' : 'none';
```

---

## 자주 쓰는 명령어

```bash
# 데이터 처리 (AI 없이)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-05 \
  --input data/raw/슬룸/2026-05/reviews.csv \
  --prev-input data/raw/슬룸/2026-04/reviews.csv \
  --skip-ai

# 데이터 처리 (Ollama AI 포함)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-05 \
  --input data/raw/슬룸/2026-05/reviews.csv \
  --ollama-model exaone3.5:7.8b

# 키워드↔리뷰 오매칭 AI 재분류 (정규식 false positive 제거)
#   --reclassify        : keywords.json의 review_samples를 Ollama로 재검증(샘플 정제)
#   --reclassify-full   : 전체 리뷰에서 멤버십 재도출 = 재할당 + count/all_review_ids/
#                         review_samples/by_product 재계산 (정확↑·매우 느림, --reclassify보다 우선)
#   --reclassify-mode   : batch(빠름, 8건 묶음) | item(정밀, 1건씩)  기본 batch
#   --skip-ai 와 무관하게 동작 (재분류만 단독 실행 가능)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-04 \
  --input data/raw/슬룸/2026-04/reviews.csv \
  --prev-input data/raw/슬룸/2026-03/reviews.csv \
  --skip-ai --reclassify-full --reclassify-mode batch

# 출력 파일에 reviews.json 추가됨 (해당 월 전체 리뷰 인덱스)
#   대시보드 인사이트 '전체 보기' + 키워드 모달 'AI 재분류(전체 리뷰)'가 이 파일을 사용
#   {"count":N,"reviews":{review_id:{rating,date,product,text}}}  (익명화 본문, PII 미포함)

# [3.5단계] 7b 재분류 후 의심 키워드만 14b급으로 정밀 재검증 (거짓양성 제거)
#   현재 멤버만 verify_keyword_reviews 3단계 게이트로 재판정 → 제거만 발생(추가 X), 빠름
#   update-data.bat [3.5/4] 에서 자동 호출됨 (14b 모델 감지 시)
python scripts/reverify_suspect.py --brand 슬룸 --month 2026-04 --model qwen2.5:14b
#   기본 대상: complaint,improvement (--polarities 로 변경, praise 포함 가능)

# 3단계 게이트 = ①관련성 ②의도분류(칭찬/불만/개선요청) ③반대신문
#   ollama_analysis.verify_keyword_reviews + 대시보드 aiVerifyKeywordCandidates 가 동일 로직
#   "가성비 좋다→가격불만", "강도 높이면 시원→강도강화요청" 같은 긍정 누수를 ②에서 차단

# 익명화
python scripts/anonymize_csv.py \
  --input data/raw/슬룸/2026-05/reviews.csv \
  --output data/anonymized/슬룸/2026-05/reviews_anon.csv

# Python 구문 검증
python3 -m py_compile scripts/process_data.py && echo "OK"
python3 -m py_compile scripts/ollama_analysis.py && echo "OK"

# 로컬 대시보드 미리보기 (Python 내장 서버)
cd docs && python3 -m http.server 8080
# → http://localhost:8080
```

---

## Ollama 설정

- 모델: `exaone3.5:7.8b` (기본값, 대시보드에서 변경 가능)
- 엔드포인트: `http://localhost:11434`
- `--skip-ai` 플래그로 건너뛸 수 있음 (GitHub Actions 기본값)
- 대시보드 사이드바 하단에서 연결 상태 + 모델 선택 가능

```bash
ollama pull exaone3.5:7.8b
ollama serve
```

---

## 데이터 처리 출력 파일

| 파일 | 설명 |
|------|------|
| `docs/data/index.json` | 브랜드·월 목록 (**딕셔너리 형태 필수**) |
| `docs/data/{브랜드}/{월}/summary.json` | KPI, 타임라인 |
| `docs/data/{브랜드}/{월}/products.json` | 상품별 통계 (**positive_rate, negative_rate 필수**) |
| `docs/data/{브랜드}/{월}/keywords.json` | 키워드 분석 (칭찬/불만/개선) |
| `docs/data/{브랜드}/{월}/ai_analysis.json` | Ollama AI 결과 (선택) |
| `data/anonymized/{브랜드}/{월}/reviews_anon.csv` | 익명화 원본 (GitHub 보관) |
