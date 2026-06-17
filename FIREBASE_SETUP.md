# Firebase 실시간 Taxonomy 협업 설정 가이드

여러 담당자가 Taxonomy(분류)를 편집할 때 **실시간으로 서로 동기화**되도록 하는 설정입니다.
한 번만 설정하면 팀 전체가 같은 분류를 공유합니다. (무료 티어로 충분)

> 설정 안 해도 대시보드는 정상 동작합니다. 이 기능을 켜면 `🌐 실시간` 버튼이 활성화됩니다.

---

## 1단계 — Firebase 프로젝트 만들기 (약 2분)

1. https://console.firebase.google.com 접속 → 구글 계정 로그인
2. **「프로젝트 만들기」** 클릭
3. 프로젝트 이름: 예) `crema-taxonomy` 입력 → 계속
4. **Google 애널리틱스: 사용 안 함**(끄기) → **프로젝트 만들기** 클릭
5. "새 프로젝트가 준비되었습니다" → **계속**

## 2단계 — Firestore 데이터베이스 생성

1. 왼쪽 메뉴 **「빌드 → Firestore Database」** 클릭
2. **「데이터베이스 만들기」** 클릭
3. 위치: **asia-northeast3 (서울)** 선택 → 다음
4. 시작 모드: **「테스트 모드에서 시작」** 선택 → 사용 설정
   - (테스트 모드 = 누구나 읽기/쓰기 30일. 아래 4단계에서 영구 규칙으로 교체합니다)

## 3단계 — 웹 앱 등록 + 설정값(config) 복사

1. 왼쪽 상단 **⚙️(설정) → 프로젝트 설정** 클릭
2. 「내 앱」 섹션에서 **`</>` (웹) 아이콘** 클릭
3. 앱 닉네임: 예) `dashboard` 입력 → **앱 등록** (호스팅은 체크 안 함)
4. 화면에 나오는 **`firebaseConfig` 객체**를 복사합니다. 이렇게 생겼습니다:
   ```js
   const firebaseConfig = {
     apiKey: "AIza............",
     authDomain: "crema-taxonomy.firebaseapp.com",
     projectId: "crema-taxonomy",
     storageBucket: "crema-taxonomy.appspot.com",
     messagingSenderId: "1234567890",
     appId: "1:1234567890:web:abcdef123456"
   };
   ```
   ※ 이 값들은 비밀이 아니라 공개돼도 되는 클라이언트 설정입니다.

## 4단계 — 대시보드에 config 붙여넣기

`docs/index.html` 파일에서 아래 줄을 찾습니다 (Ctrl+F: `FIREBASE_CONFIG`):
```js
var FIREBASE_CONFIG = null; // 예: {apiKey:"…",authDomain:"…",projectId:"…",appId:"…"} — 가이드 참고
```
이 `null` 자리에 3단계에서 복사한 객체를 넣습니다:
```js
var FIREBASE_CONFIG = {
  apiKey: "AIza............",
  authDomain: "crema-taxonomy.firebaseapp.com",
  projectId: "crema-taxonomy",
  storageBucket: "crema-taxonomy.appspot.com",
  messagingSenderId: "1234567890",
  appId: "1:1234567890:web:abcdef123456"
};
```
> 직접 편집이 어려우면, 복사한 config를 저(클로드)에게 붙여주시면 제가 넣고 커밋해 드립니다.

## 5단계 — 보안 규칙 설정 (테스트 모드 만료 방지)

테스트 모드는 30일 후 막힙니다. 영구 규칙으로 바꿉니다.

1. Firestore Database → **「규칙」** 탭
2. 내용을 아래로 교체 → **게시**:
   ```
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /crema_taxonomy/{doc} {
         allow read, write: if true;
       }
     }
   }
   ```
   - 이 규칙은 `crema_taxonomy` 컬렉션만 읽기/쓰기 허용합니다(다른 데이터는 차단).
   - ⚠️ 보안 참고: URL을 아는 사람은 누구나 분류를 수정할 수 있습니다(내부 팀 도구라 보통 무방). 더 잠그려면 Firebase Authentication 추가가 필요하니 필요 시 요청하세요.

## 6단계 — 배포

- config를 넣은 `docs/index.html`을 커밋/푸시하면 GitHub Pages에 반영됩니다.
- (제가 작업 중이면 "config 넣고 커밋해줘"라고 하시면 됩니다)

---

## 사용법

1. 대시보드 → **Taxonomy** 페이지 상단의 **`⚪ 실시간 OFF`** 버튼 클릭 → **`🟢 실시간 ON`**
2. 이제 분류(Topic·키워드 편집, 수동 분류)를 바꾸면 **자동으로 클라우드에 저장**되고,
   다른 담당자 화면에도 **실시간 반영**됩니다.
3. 버튼을 다시 누르면 OFF (이 브라우저만 로컬 작업).
4. 켜둔 상태는 브라우저에 기억되어 다음에 열 때 자동 연결됩니다.

### 동작 방식 / 병합 규칙
- 두 사람이 **다른 리뷰**를 분류하면 → 둘 다 보존(자동 병합).
- 같은 항목을 동시에 수정하면 → 나중 저장이 반영(last-write-wins).
- 분류 "추가/포함"은 잘 합쳐지지만, "되돌리기(삭제)"는 동시 편집 시 복원될 수 있으니, 큰 정리는 한 명이 할 때 하는 걸 권장합니다.

## 무료 한도
- Firestore 무료(Spark): 일 50,000 읽기 / 20,000 쓰기 / 1GB 저장.
- 이 용도(담당자 몇 명, 분류 문서 1개)는 한도의 1%도 안 쓰니 **사실상 무료**입니다.

## 문제 해결
- `🌐 실시간 미설정`: FIREBASE_CONFIG가 비어있음 → 4단계 확인.
- 버튼에 마우스 올렸을 때 "오류: ..." 표시: 보안 규칙(5단계) 또는 인터넷 연결 확인.
- 반영이 안 됨: 강력 새로고침(Ctrl+Shift+R) 후 다시 `🟢 실시간 ON`.
