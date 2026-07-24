# 크리마 리뷰 대시보드

크리마(Crema) 리뷰 데이터를 기반으로 브랜드별·월별 고객 리뷰를 분석하고 시각화하는 GitHub Pages 대시보드입니다.

---

## 프로젝트 소개

- 크리마에서 내보낸 리뷰 CSV 파일을 `data/raw/`에 넣으면 자동으로 처리됩니다.
- AI 감성 분석(Claude Code CLI) 또는 규칙 기반 분석을 선택할 수 있습니다.
- 처리된 결과는 `docs/data/`에 JSON으로 저장되며, GitHub Pages로 대시보드가 서빙됩니다.

---

## 폴더 구조

```
crema-review-dashboard/
├── .github/
│   └── workflows/
│       └── process-reviews.yml   # GitHub Actions 자동 처리 워크플로우
├── data/
│   └── raw/                      # 원본 CSV 파일 보관 (gitignore 처리 가능)
├── docs/                         # GitHub Pages 서빙 루트
│   ├── index.html                # 대시보드 메인 페이지
│   ├── data/
│   │   ├── index.json            # 브랜드·월 목록 인덱스
│   │   └── {brand}_{month}.json  # 처리된 리뷰 데이터
│   └── assets/                   # CSS, JS, 이미지
├── scripts/
│   └── process_reviews.py        # 데이터 처리 Python 스크립트
├── .gitignore
└── README.md
```

---

## 사용 방법

### 새 브랜드 데이터 추가하기

1. 크리마 관리자 페이지에서 리뷰 데이터를 CSV로 내보냅니다.
2. 파일명을 `{브랜드ID}_{YYYY-MM}.csv` 형식으로 변경합니다.
   - 예: `sloom_2026-05.csv`
3. `data/raw/` 폴더에 파일을 넣고 커밋·푸시합니다.
4. GitHub Actions가 자동으로 데이터를 처리하고 `docs/data/`에 JSON을 생성합니다.

### 새 월 데이터 추가하기

위와 동일한 방법으로 새 월의 CSV 파일을 `data/raw/`에 추가합니다.

---

## Python 설치 및 실행 방법

### 요구사항

- Python 3.11 이상
- pip

### 설치

```bash
# 저장소 클론
git clone https://github.com/Lyrze/crema-review-dashboard.git
cd crema-review-dashboard

# 가상환경 생성 (선택사항, 권장)
python -m venv venv
source venv/bin/activate       # macOS/Linux
# 또는
venv\Scripts\activate          # Windows

# 의존성 설치
pip install pandas requests
```

### 수동 실행

```bash
# AI 분석 없이 실행 (기본 규칙 기반)
python scripts/process_reviews.py --skip-ai

# AI 분석 포함 실행 (Claude Code CLI 로그인 필요)
python scripts/process_reviews.py

# 특정 파일만 처리
python scripts/process_reviews.py --file data/raw/sloom_2026-05.csv
```

처리 결과는 `docs/data/` 폴더에 JSON 파일로 저장되고, `docs/data/index.json`이 자동으로 업데이트됩니다.

---

## Claude Code CLI 설정 방법

AI 기반 감성 분석을 사용하려면 [Claude Code CLI](https://docs.claude.com/en/docs/claude-code)가
설치되어 있고 구독 계정으로 로그인되어 있어야 합니다(GPU 불필요).

```bash
npm install -g @anthropic-ai/claude-code
claude auth login
```

로그인 후에는 별도 설정 없이 각 스크립트가 기본 모델(`sonnet`)로 자동 동작합니다.
필요 시 `--model` 옵션으로 다른 모델(`opus`, `haiku`)을 지정할 수 있습니다.

---

## GitHub Pages 설정 방법

1. GitHub 저장소 페이지에서 **Settings > Pages**로 이동합니다.
2. **Source**를 `Deploy from a branch`로 설정합니다.
3. **Branch**를 `main`, **Folder**를 `/docs`로 선택합니다.
4. **Save**를 클릭합니다.
5. 잠시 후 `https://lyrze.github.io/crema-review-dashboard/` 주소로 대시보드에 접근할 수 있습니다.

---

## GitHub Actions 자동화

`data/raw/` 폴더에 CSV 파일을 푸시하면 자동으로 아래 작업이 실행됩니다.

1. Python 환경 설정 및 의존성 설치
2. `--skip-ai` 플래그로 데이터 처리 (AI 없이 빠른 처리)
3. `docs/data/`에 JSON 저장
4. 변경사항 자동 커밋 및 푸시
5. GitHub Pages 배포

---

## 라이선스

MIT License
