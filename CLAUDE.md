# CLAUDE.md — 크리마 리뷰 대시보드

이 파일은 Claude가 이 프로젝트를 작업할 때 반드시 읽어야 하는 컨텍스트, 규칙, 과거 실수 기록입니다.

---

## 프로젝트 개요

- **목적**: 크리마 리뷰 CSV → JSON 파이프라인 → GitHub Pages 대시보드 자동화
- **주요 언어**: Python 3.11, HTML/CSS/JavaScript (단일 파일)
- **AI**: Claude Code CLI (구독 인증, 기본 모델 `sonnet`) — 2026-07-24 Ollama 완전 제거,
  이전 Ollama 버전은 `backup/pre-ollama-removal-2026-07-24` 브랜치에 보존
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
│           └── keywords.json                # 키워드 분석
├── scripts/
│   ├── process_data.py                      # 메인 파이프라인 (CLI: parse_args)
│   ├── claude_engine.py                     # Claude Code CLI 래퍼 + AI 분석 모듈 (verify_keyword_reviews 3단계 게이트)
│   ├── local_proxy.py                       # 대시보드 라이브 AI 기능용 로컬 프록시 (Claude CLI 중계 + GitHub 업로드)
│   ├── reverify_suspect.py                  # 의심 키워드 멤버를 재검증 (거짓양성 제거)
│   ├── anonymize_csv.py                     # PII 제거 익명화 스크립트
│   ├── interactive_select.py                # update-data.bat 대화형 메뉴 (Python)
│   ├── scan_raw.py                          # data/raw 스캔 → KEY=VALUE 출력
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

### 🔴 CRITICAL: Windows 배치파일 — `title`+한글 `echo` 조합이 뒤 줄을 깨뜨림 (2026-07-24)

**발생**: `start-ai-local.bat`/`start-tunnel.bat`에 `title`(한글 포함) + `chcp 65001` +
`color` + `set` 를 쓰고 그 뒤에 한글 `echo` 여러 줄을 두었더니, 정상적인 한글 문장 중간의
특정 단어(예: "각자 자기 PC에서" 중 "자기")가 **명령어로 오인식**되어
`'자기' is not recognized as an internal or external command` 로 배치 실행이 깨짐.

**원인 조사**: 여러 조합을 격리 테스트한 결과 — 인용부호(`"`) 유무, 괄호 유무, 문장 내용
자체는 원인이 아니었다. **`title` 명령이 파일 어딘가에 존재하기만 해도**, 그 뒤에 오는
한글 `echo` 줄(들)의 정확한 바이트 정렬에 따라 cmd.exe 파서가 라인 경계를 잘못 잡는
것으로 보인다(멀티바이트 UTF-8과 cmd 내부 read-buffer 정렬 문제로 추정 — 재현은 확실하나
"왜"까지는 명확히 규명 못 함). **`title`이 없으면 재현 안 됨.** 같은 파일이라도 문장을
아주 조금만 바꿔도(단어 순서, 길이) 증상이 사라지거나 다른 줄에서 터질 수 있어 예측 불가.

**규칙**: `title` 명령을 쓰는 배치파일에서는 **한글 안내 문구를 `echo`로 직접 찍지 말고
전부 Python이 출력하게 위임**한다(이미 확립된 "복잡한 로직은 Python으로 분리" 원칙의 연장).
`.bat`는 `title`(영문 권장)·`chcp 65001 >nul`·`color`·`python 호출`·`pause` 정도의
최소 골격만 남긴다. 대화형 선택도 `set /p`+`echo` 대신 Python의 `input()`으로 옮긴다
(`interactive_select.py`의 대화형 메뉴 처리 방식 참고).

```batch
:: 올바른 패턴 — 배치는 최소 골격만, 한글 출력은 전부 Python
@echo off
title App Name (ASCII)
chcp 65001 >nul
color 0B
python "%~dp0scripts\my_script.py"
pause
```

```python
# my_script.py 쪽 — sys.stdout.reconfigure 필수(cp949 콘솔 크래시 방지, 아래 항목도 참고)
print("한글 안내 문구는 여기서 전담 출력")
sel = input("선택 (1~2, Enter=1번): ").strip()
```

**부수 발견(같이 고침)**: `start_tunnel.py`에 `sys.stdout.reconfigure(encoding="utf-8")` 가
빠져 있어서, `chcp 65001`로 콘솔을 UTF-8로 바꿔도 **Python 쪽 stdout은 여전히 cp949라
한글이 깨지거나(모지바케) `—`(U+2014) 같은 cp949 미지원 문자에서 `UnicodeEncodeError`로
크래시**했다. `interactive_select.py`/`local_proxy.py`는 이미 이 가드가 있었는데
`start_tunnel.py`엔 없었다 — 한글 출력하는 새 스크립트를 만들 때마다 반드시 넣을 것.

**검증**: 격리 리프로로 원인을 좁힌 뒤, 실제 `start-ai-local.bat`/`start-tunnel.bat`를
고쳐서 재실행 — 파싱 오류 없이 정상 실행되고(claude 백엔드 선택 시 `AI_BACKEND` 환경변수가
`local_proxy.py` 자식 프로세스까지 정상 상속됨을 curl로 확인), 한글 출력도 깨지지 않음을 확인.

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
| AI 서버 연결 상태 | ✅ | 사이드바 하단 (Claude Code CLI 로컬 프록시 — `scripts/local_proxy.py`) |
| AI 모델 선택 | ✅ | 사이드바 하단 (온라인 시 표시, sonnet/opus/haiku) |
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

### 🟠 HIGH: Claude CLI 한도(quota) 자동화 — 예약작업 대신 순수 재시도 루프

**발생**: `reverify_suspect.py --engine claude`가 계정 세션 한도에 걸릴 때, 사람이 리셋 시각까지 기다렸다가 수동으로 재실행해야 했음. Claude Code의 예약작업(scheduled-tasks MCP, `create_scheduled_task`)으로 자동화를 시도했으나 **두 번 모두** 무인 실행 컨텍스트에서 첫 Bash tool_use 직후 무응답으로 멈춤(승인 대기로 추정). "Run now" 사전승인도 실제 예약 발동엔 적용 안 됨.

**증상**: task-notification은 "completed"로 뜨는데 실제로는 아무 명령도 실행되지 않음(데이터/마커 변화 없음).

**규칙**: 특정 시각(한도 리셋 등)에 무인으로 명령을 재시도해야 하면 **Claude Code 예약작업을 쓰지 말고** `scripts/auto_reverify_loop.py`처럼 **순수 프로세스 루프**를 지금 세션에서 `run_in_background`로 직접 띄운다. 이 스크립트는 실패 메시지에서 "resets HH:MMam/pm" 을 정규식으로 파싱해 그 시각까지 sleep 후 재시도한다.

```bash
python scripts/auto_reverify_loop.py --brand 슬룸 --months 2026-04,2026-05,2026-03 --engine claude
```

**추가로 발견한 버그(같이 고침)**: 계정 전체가 한도 소진이면 `reverify_month()`의 `analyzer.health_check()`가 실패하는데, 이걸 그냥 "건너뜀(False)"으로 처리하면 남은 모든 월도 연쇄로 스킵되다가 `main()`이 **exit 0("완료")** 로 끝나버려 사실상 아무 것도 처리 못 했는데 성공한 것처럼 보임. → `health_check` 실패 원인이 한도성 메시지(`is_quota()`)면 반드시 `"quota"`를 반환해 `main()`이 `sys.exit(3)`으로 멈추게 해야 한다(그래야 `auto_reverify_loop.py`가 올바르게 재시도 여부를 판단할 수 있음).

**부가 함정**: `python cmd; echo "EXIT=$?" >> log` 처럼 세미콜론/파이프(`| tail`)로 명령을 이어 붙이면, exit code(`$?`)는 **마지막 명령의 것**만 잡힌다(background 도구 알림도 동일). 실제 종료코드는 파이프 없이 `cmd >/dev/null 2>&1; echo $?` 로 확인할 것.

### 🟠 HIGH: 월간 감성은 Claude가 권위(update-data.bat [4/5])

**설계**: `process_data`는 **별점 폴백만으로 잠정 감성**을 만들고(로컬 AI 없음 — `skip_ai=True` 고정), 그 뒤 `[4/5]` 단계에서 `recheck_sentiment.py --full`(Claude sonnet)이 **전건 재판정해 덮어쓴다(=권위)**. 별점 폴백은 "아팠는데 풀렸어요"처럼 부정어+긍정결말을 오판하므로 Claude 전건 재판정이 최종 기준.

**규칙**:
- 이 단계는 대량(월 ~2천건)이라 세션 한도로 수 시간~하루 걸릴 수 있고, `quota_retry`가 리셋시각까지 대기 후 자동 재개한다(무인). 중단돼도 `.sentiment_progress.json` 이어받기 + 완료월 스킵.
- `recheck_sentiment`는 sentiment 만 바꾸고 products/summary 의 긍정률·부정률·감성카운트를 재계산한다(멤버십 review_count·avg_rating 등은 patch 결과 보존 — sentiment 필드만 갱신).
- **완료월 마커는 삭제 금지**(done=True 보존). 과거 unlink 시 다음 윈도우에 완료월을 처음부터 재판정 → 감성 thrash(temp=0에도 Claude ~2% 비결정) 발생했음.
- 분석은 CLI(claude) 우선(메모리 규칙).

**reverify_suspect.py 종료코드 계약(auto_reverify_loop 이 이 값으로 재시도/중단 판단)**:
- `0` = 전체 완료(또는 이미 완료돼 스킵). 루프 종료.
- `3` = 한도(quota) 소진 → 리셋 후 이어받기. 루프가 리셋시각까지 대기 후 재시도.
- `2` = 비(非)한도 실패로 한 건도 처리 못 함(로그인/파일/환경 등). 루프가 **자동 재시도 없이 중단**, 사람 확인 필요.
한도 감지는 세 겹: ① `claude_engine`이 stdout·stderr 둘 다 검사해 quota 신호 시 즉시 실패 + `_quota_seen` 기록, ② 회로차단기(연속 2회 완전실패)가 quota면 'quota' 표기/비한도면 '사람 확인' 표기로 구분, ③ `reverify_suspect`가 그 신호로 3(quota) vs 2(비한도)를 분기. 완료 판정 마커는 `__done_pol__`(엔진별 완료 polarity 집합)로 기록 — 요청 polarity가 부분집합일 때만 스킵(축소 실행 후 전체 재실행 시 미검증 polarity가 영구 스킵되던 버그 방지).

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

### 🟠 HIGH: Ollama 완전 제거 — Claude 단일 엔진화 (2026-07-24)

**배경**: 2026-07-23 세션에서 GPU가 약한 PC를 위해 파이프라인 전체에 `--engine {ollama,claude}`
선택지를 추가했었다(`make_analyzer(engine,...)` 팩토리로 Ollama/Claude 둘 다 지원). 이후
"올라마 자체는 없다고 생각하고 제거해달라"는 요청에 따라 **엔진 선택지 자체를 없애고 Claude
단일 경로로 통합**했다 — 로컬 GPU/모델 관리 부담을 아예 없애는 것이 목적.

**변경 요지**:
- `ollama_analysis.py`(`OllamaClient`, base_url/SSRF 방어 등) **삭제**. 그 안의 3단계 게이트
  (①관련성 ②의도분류 ③반대신문)·감성분석·키워드추출·브리프 생성 로직은 전부 `claude_engine.py`의
  단일 `ClaudeAnalyzer` 클래스로 흡수(연결 로직만 제거, 분석 로직은 그대로).
- 모든 스크립트(`process_data.py`, `discover_keywords.py`, `classify_pvoc_intent.py`,
  `reverify_pvoc_intent.py`, `reverify_suspect.py`, `classify_unclassified.py`)에서
  `--engine`/`--base-url`/`--ollama-model` 인자를 제거하고 `--model`(기본 `sonnet`) 하나로 통일.
  `make_analyzer(engine,...)` 팩토리도 제거 — 항상 `ClaudeAnalyzer(model=...)` 직접 생성.
- `interactive_select.py`: "AI 엔진 선택"·Ollama 모델 스캔(`get_ollama_models()`) 프롬프트 전부
  제거. 단계별 포함/건너뛰기 Y/N도 제거하고 항상 전체(정밀 재분류 등 포함)로 진행 — Claude는
  세션 한도가 있을 뿐 로컬 자원 제약이 없어 매번 물어볼 이유가 없다는 판단.
- `update-data.bat`: `ENGINE`/`AI_FLAG`/`REVERIFY_FLAG`/`PVOC_INTENT_FLAG`/`PVOC_REVERIFY_FLAG`
  파싱ㆍ분기 제거. 재검증/PVOC 감성판정/키워드발굴/Taxonomy 분류 제안 단계는 이제 조건 없이
  항상 실행(과거엔 "AI 분석을 건너뛰었으면 생략" 이었으나, AI를 건너뛰는 선택지 자체가 사라짐).
- `scripts/local_proxy.py`(대시보드 라이브 AI 15개 기능용 프록시): `AI_BACKEND` 환경변수와
  Ollama 중계(`proxy_get`/`proxy_post`, `OLLAMA_URL`) 완전 삭제 — Claude Code CLI 중계가
  무조건 유일한 동작이 됨. `scripts/start_tunnel.py`의 `pick_ai_backend()`(1.Ollama/2.Claude
  선택)와 `check_ollama_running()`도 제거 — 항상 Claude로 로컬 프록시를 띄운다.
- `docs/index.html` 사이드바: 기능은 그대로 두되(내부 함수명 `pingOllama()`/`ollamaStream()`
  등은 변경 안 함 — Ollama API 모양을 그대로 흉내내는 프록시 규약이라 이름이 곧 프로토콜),
  사용자에게 보이는 문구만 "Ollama 연결됨/오프라인" → "AI 서버 연결됨/오프라인" 등으로 일반화.
- `docs/data/{브랜드}/{월}/ai_analysis.json` 생성 완전 삭제(이미 죽은 파일이었음 — 대시보드
  어디서도 안 읽음). `scripts/scan_ollama.py`, `docs/OLLAMA_TUNNEL_GUIDE.md`, 미사용 레거시
  `scripts/patch_sentiment.py`(→ `recheck_sentiment.py --full`로 대체됨)도 함께 삭제.

**이전 Ollama 지원 코드는 `backup/pre-ollama-removal-2026-07-24` 브랜치에 보존돼 있다**
(되돌리거나 참고가 필요하면 그 브랜치를 확인).

---

## 자주 쓰는 명령어

```bash
# 데이터 처리 (기본 — 로컬 AI 호출 없음, 감성은 별점 폴백·키워드는 정규식 추출)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-05 \
  --input data/raw/슬룸/2026-05/reviews.csv \
  --prev-input data/raw/슬룸/2026-04/reviews.csv

# 키워드↔리뷰 오매칭 AI 재분류 (정규식 false positive 제거, Claude 사용)
#   --reclassify        : keywords.json의 review_samples를 Claude로 재검증(샘플 정제)
#   --reclassify-full   : 전체 리뷰에서 멤버십 재도출 = 재할당 + count/all_review_ids/
#                         review_samples/by_product 재계산 (정확↑·매우 느림, --reclassify보다 우선)
#   --reclassify-mode   : batch(빠름, 8건 묶음) | item(정밀, 1건씩)  기본 batch
#   --model             : Claude 모델 (기본 sonnet)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-04 \
  --input data/raw/슬룸/2026-04/reviews.csv \
  --prev-input data/raw/슬룸/2026-03/reviews.csv \
  --reclassify-full --reclassify-mode batch

# 출력 파일에 reviews.json 추가됨 (해당 월 전체 리뷰 인덱스)
#   대시보드 인사이트 '전체 보기' + 키워드 모달 'AI 재분류(전체 리뷰)'가 이 파일을 사용
#   {"count":N,"reviews":{review_id:{rating,date,product,text}}}  (익명화 본문, PII 미포함)

# 재분류 후 의심 키워드만 다시 한번 정밀 재검증 (거짓양성 제거, Claude)
#   현재 멤버만 verify_keyword_reviews 3단계 게이트로 재판정 → 제거만 발생(추가 X), 빠름
#   update-data.bat [3.5/4] 에서 항상 자동 호출됨
python scripts/reverify_suspect.py --brand 슬룸 --month 2026-04
#   기본 대상: complaint,improvement (--polarities 로 변경, praise 포함 가능)

# 3단계 게이트 = ①관련성 ②의도분류(칭찬/불만/개선요청) ③반대신문
#   claude_engine.ClaudeAnalyzer.verify_keyword_reviews + 대시보드 aiVerifyKeywordCandidates 가 동일 로직
#   "가성비 좋다→가격불만", "강도 높이면 시원→강도강화요청" 같은 긍정 누수를 ②에서 차단

# 익명화
python scripts/anonymize_csv.py \
  --input data/raw/슬룸/2026-05/reviews.csv \
  --output data/anonymized/슬룸/2026-05/reviews_anon.csv

# Python 구문 검증
python3 -m py_compile scripts/process_data.py && echo "OK"
python3 -m py_compile scripts/claude_engine.py && echo "OK"

# 로컬 대시보드 미리보기 (Python 내장 서버)
cd docs && python3 -m http.server 8080
# → http://localhost:8080
```

---

### 🟠 HIGH: 대시보드 라이브 AI 기능 — local_proxy.py (Claude 단일 백엔드)

**배경**: `docs/index.html`에는 월간 파이프라인과 완전히 별개인 **브라우저 라이브 AI 기능 15개**가
있다 — AI 커스텀 질문(⑧)/각 섹션 "AI 분석"/KPI 인사이트 "AI 요약"/SKU 인사이트 "AI 심층분석"/
키워드 리뷰 모달 "AI 요약"·"AI 재분류"/SKU 1vs1 "AI 비교 브리프"/AI 채팅 패널("Hey Sloom")/
Taxonomy의 "🤖 AI 자동 분류(검토형)"·신규 키워드 후보 검증·키워드 학습·Topic 자동 생성·전체
재분류 — 이들은 전부 브라우저가 사이드바 "AI 서버 URL"로 직접 `fetch()`를 날려 실행되는
**파이프라인과 무관한 별도 시스템**이다.

**설계**: `scripts/local_proxy.py`(로컬 HTTP 프록시, 기본 포트 8799)가 대시보드의 요청을 받아
Claude Code CLI로 처리한다. 대시보드는 Ollama 고유 API 3개(`GET /`, `GET /api/tags`,
`POST /api/generate`) 모양으로만 말을 걸므로, 프록시가 **같은 모양**으로 응답하돼 내부적으로
`claude_engine.ClaudeClient`(백엔드에서 이미 쓰던 것 재사용)를 호출한다. 이 덕분에
**`docs/index.html` JS는 한 줄도 안 건드리고** 그대로 동작한다(단, 사용자에게 보이는 상태 문구는
"Ollama 연결됨/오프라인" → "AI 서버 연결됨/오프라인" 등으로 일반화했다 — 위 Ollama 완전 제거
항목 참고. 내부 함수명 `pingOllama()`/`ollamaStream()` 등은 프로토콜 규약이라 그대로 둠).

```bash
# GPU 불필요 — Claude Code CLI 로그인만 되어 있으면 됨
python scripts/local_proxy.py
# 또는: start-ai-local.bat (로컬 전용, 계정 공유 위험 없음, 기본 권장)
# → 대시보드 사이드바 "AI 서버 URL"에 http://localhost:8799 (또는 Cloudflare Tunnel URL) 입력
```

각자 자기 PC에서 실행해야 각자의 Claude 계정이 쓰인다 — 터널(`start-tunnel.bat`) URL을
팀원과 공유하면 그 팀원도 터널을 켠 사람의 계정/한도를 쓰게 되므로 팀원과 공유 금지(터널은
"내가 다른 기기에서 내 PC로 접속"하는 용도로만 권장). `start-ai-local.bat`은 터널 없이
로컬(localhost)로만 붙는 방식이라 계정 공유 위험이 아예 없어 이쪽을 기본으로 권장한다.

**설계 포인트**:
- `GET /`(온라인 점검)은 **로그인 여부와 무관하게 즉시 200**을 반환한다. 대시보드의 `pingOllama()`가
  이 요청에 3초 타임아웃을 걸어두므로, 여기서 `claude auth login`(브라우저 로그인, 최대 3분 대기)을
  타면 항상 타임아웃 → "오프라인"으로 잘못 표시된다. 로그인 체크/유도는 타임아웃 없는
  `POST /api/generate`에서만 한다.
- `GET /api/tags`는 로컬 모델 목록 개념이 없으므로 `["sonnet","opus","haiku"]` 고정 반환.
- `POST /api/generate`: `claude auth status`(빠름·비파괴적)로 로그인 확인 → 안 돼있으면
  `claude auth login` 실행(브라우저 로그인 창이 뜸, 최대 3분 대기) → 로그인 후 정상 처리.
  세션 한도 소진(`is_quota`)이나 로그인 실패는 예외를 던지는 대신 `{"response": "⏳/⚠️ 안내 문구"}`
  로 반환해, 기존 AI 요약/분석 UI가 그 문구를 그대로 답변 자리에 표시하게 한다(별도 에러 처리 UI 불필요).
- Claude CLI는 실시간 토큰 스트리밍이 없다(`-p`는 완료 후 전체 텍스트 반환). `stream:true` 요청엔
  전체 응답을 받은 뒤 6자 단위로 잘라 Ollama와 동일한 NDJSON(`{"response":"...","done":false}` 줄들 +
  마지막 `{"done":true}`)으로 흘려보내 타이핑 효과를 흉내낸다(실제 스트리밍 아님, 체감 UX만 동일).
- **세션 한도 공유 주의**: 이 라이브 기능들과 월간 파이프라인이 같은 Claude 계정 한도를 나눠
  쓴다. 대시보드에서 AI 버튼을 많이 누르면 파이프라인 처리용 한도가 줄어든다.

**검증**: 로컬에서 프록시를 띄워 `GET /`·`GET /api/tags`·`POST /api/generate`(스트리밍/
논스트리밍 둘 다)를 curl로 호출해 확인 — 논스트리밍은 `{"response":"2","done":true}`(1+1 질문),
스트리밍은 Ollama와 동일한 NDJSON 청크로 정상 응답함을 확인. 이전 Ollama 지원 버전은
`backup/pre-ollama-removal-2026-07-24`(및 그 이전 `backup/pre-claude-live-proxy-2026-07-24`)
브랜치에 보존.

---

## 데이터 처리 출력 파일

| 파일 | 설명 |
|------|------|
| `docs/data/index.json` | 브랜드·월 목록 (**딕셔너리 형태 필수**) |
| `docs/data/{브랜드}/{월}/summary.json` | KPI, 타임라인 |
| `docs/data/{브랜드}/{월}/products.json` | 상품별 통계 (**positive_rate, negative_rate 필수**) |
| `docs/data/{브랜드}/{월}/keywords.json` | 키워드 분석 (칭찬/불만/개선) |
| `docs/data/{브랜드}/{월}/reviews.json` | 해당 월 전체 리뷰 인덱스 (익명화 본문, PII 미포함) |
| `data/anonymized/{브랜드}/{월}/reviews_anon.csv` | 익명화 원본 (GitHub 보관) |
