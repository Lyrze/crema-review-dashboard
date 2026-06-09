# ☁️ Taxonomy 분류값 GitHub 저장소 업로드 가이드

Taxonomy 수동 분류 결과를 **GitHub 저장소에 저장**하고 팀원이 **불러올** 수 있게 하는 기능입니다.
기존 `start-tunnel.bat`(Cloudflare 터널)에 통합되어 있습니다.

```
[대시보드] ──터널──> [내 PC: 로컬 프록시] ──> Ollama(AI) + GitHub(분류 저장)
                                            토큰은 내 PC에만 보관 (공개 저장소·브라우저에 노출 X)
```

- **업로드/삭제**: 내 PC + 배치가 켜져 있을 때만 (토큰 보유)
- **불러오기/읽기**: 공개 저장소라 항상 가능
- 저장 형식: `docs/data/{브랜드}/{월}/taxonomy/{담당자}__{일시}.json`
- 대시보드 표기: **[슬룸][2026-04][홍길동][2026-06-09 14:30]**

---

## 1단계: GitHub 토큰 발급 (1회, 약 2분)

1. https://github.com/settings/tokens?type=beta (Fine-grained tokens) 접속
2. **Generate new token**
   - Token name: `crema-taxonomy`
   - Expiration: 원하는 기간 (예: 90일)
   - **Repository access** → *Only select repositories* → `crema-review-dashboard` 선택
   - **Permissions** → Repository permissions → **Contents: Read and write**
3. **Generate token** → 생성된 토큰(`github_pat_...`) 복사

## 2단계: 토큰을 내 PC에 저장 (1회)

`scripts\.gh_token` 파일을 만들고 토큰만 한 줄 붙여넣기:

```
프로젝트폴더\scripts\.gh_token
```
> 이 파일은 `.gitignore`에 등록되어 **저장소에 절대 올라가지 않습니다.**
> (또는 시스템 환경변수 `GH_TOKEN` 에 넣어도 됩니다.)

## 3단계: 평소처럼 배치 실행

```
start-tunnel.bat  더블클릭
```
- 콘솔에 `GitHub 토큰: 설정됨 ✓` 가 보이면 준비 완료
- 발급된 터널 URL을 대시보드 좌측 **AI 서버 URL**에 붙여넣기 (기존과 동일)
  - 이 URL 하나로 **AI + 저장소 업로드** 모두 동작

## 4단계: 대시보드에서 사용

좌측 **📂 Taxonomy** 페이지 상단:
- **☁️ 저장소 업로드** → 담당자 이름 입력 → 커밋
- **☁️ 불러오기** → 업로드 목록에서 선택 → 현재 분류에 **병합**(include/exclude 합집합)

---

## 참고
- 업로드 후 GitHub Pages 반영까지 수십 초~1분 걸릴 수 있습니다(실시간 협업 아님).
- 저장되는 건 `review_id ↔ 분류(topic)` 매핑이라 리뷰 본문/PII는 포함되지 않습니다.
- 같은 PC에서 로컬로만 쓸 경우: AI 서버 URL을 `http://localhost:8799`(프록시 포트)로 설정.
- 토큰 만료 시 1~2단계 반복.
- 환경변수로 저장소 변경 가능: `GH_REPO`(기본 Lyrze/crema-review-dashboard), `GH_BRANCH`(기본 main).
