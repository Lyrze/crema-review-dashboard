# GitHub Pages 설정 가이드

Lyrze 계정으로 처음 저장소를 만들고 GitHub Pages까지 올리는 전체 과정입니다.

---

## 1단계 — 로컬에서 Git 초기화

터미널(또는 PowerShell)을 열고 프로젝트 폴더로 이동합니다.

```bash
cd "C:\Users\올릿\Desktop\자동화\코워크\싱클리 대시보드 벤치마킹\crema-review-dashboard"

# Git 초기화
git init

# 브랜치 이름을 main으로 설정
git branch -M main

# 파일 전체 스테이징
git add .

# 첫 커밋
git commit -m "feat: 크리마 리뷰 대시보드 초기 구성"
```

---

## 2단계 — GitHub 저장소 생성

1. https://github.com/new 접속 (Lyrze 계정으로 로그인된 상태)
2. 아래와 같이 입력:

| 항목 | 값 |
|------|----|
| Repository name | `crema-review-dashboard` |
| Visibility | **Public** ← GitHub Pages 무료 사용 조건 |
| Initialize this repository | **체크 해제** (로컬에서 올릴 것이므로) |

3. **Create repository** 클릭
4. 생성 후 나오는 페이지에서 HTTPS 주소 복사:
   ```
   https://github.com/Lyrze/crema-review-dashboard.git
   ```

---

## 3단계 — 원격 저장소 연결 및 푸시

```bash
# 원격 저장소 연결
git remote add origin https://github.com/Lyrze/crema-review-dashboard.git

# 푸시
git push -u origin main
```

> **인증 오류가 나면?**  
> GitHub는 비밀번호 대신 Personal Access Token을 사용합니다.  
> https://github.com/settings/tokens/new 에서 **repo** 권한 토큰 생성 후,  
> 비밀번호 입력란에 토큰을 붙여넣으면 됩니다.

---

## 4단계 — GitHub Pages 활성화

1. 저장소 페이지 → **Settings** 탭 클릭
2. 왼쪽 메뉴에서 **Pages** 클릭
3. **Source** 섹션:
   - Branch: `main`
   - Folder: `/docs`
4. **Save** 클릭

잠시 후 (1~3분) 아래 주소로 대시보드가 열립니다:
```
https://lyrze.github.io/crema-review-dashboard/
```

---

## 5단계 — 새 월 데이터 추가하는 방법

매달 크리마에서 CSV를 받으면 이 방법으로 업데이트합니다.

### 방법 A: 로컬에서 직접 처리 후 푸시 (권장)

```bash
# 1. CSV 파일을 올바른 경로에 저장
mkdir -p data/raw/슬룸/2026-05
# → 이 폴더에 크리마 CSV 파일을 reviews.csv로 복사

# 2. 데이터 처리 (Claude Code CLI 로그인 필요 — 감성/키워드 재분류에 사용)
python scripts/process_data.py \
  --brand 슬룸 \
  --month 2026-05 \
  --input data/raw/슬룸/2026-05/reviews.csv \
  --prev-input data/raw/슬룸/2026-04/reviews.csv

# 3. 결과 확인
cat docs/data/슬룸/2026-05/summary.json

# 4. 커밋 & 푸시 (docs/data만 — CSV는 gitignore됨)
git add docs/data/
git commit -m "data: 슬룸 2026-05 리뷰 업데이트"
git push
```

### 방법 B: GitHub Actions 자동화 (선택)

CSV를 `data/raw/슬룸/2026-05/reviews.csv` 경로로 커밋·푸시하면  
Actions가 자동으로 처리 후 `docs/data/`에 JSON을 생성·푸시합니다.

> ⚠️ CSV에 고객 정보가 포함될 수 있으므로 Public 저장소에는 올리지 않는 것을 권장합니다.  
> `.gitignore`에 `data/raw/`가 이미 등록되어 있어 기본적으로 push되지 않습니다.

---

## 문제 해결

### "404 Not Found" — Pages가 안 열림
- Settings → Pages에서 Branch/Folder 설정 재확인
- 첫 배포는 최대 5분 소요
- `docs/index.html` 파일이 실제로 존재하는지 확인

### 차트가 안 보임 (로컬에서)
- `docs/` 폴더를 직접 파일로 열면 CORS 오류 발생
- 반드시 로컬 서버 사용:
  ```bash
  cd docs && python3 -m http.server 8080
  ```
  → http://localhost:8080

### 데이터가 기본값(슬룸 2026-04)만 나옴
- `docs/data/index.json` 파일 확인
- 브랜드 키가 딕셔너리 형태인지 확인 (`{"brands": {"슬룸": {...}}}`)
