# 🌐 Cloudflare Tunnel로 Ollama 외부 공개 가이드

본인 PC의 Ollama를 다른 자리/외부에서도 사용할 수 있게 하는 가이드입니다.

---

## 📌 작동 원리

```
[다른 자리 PC] ──HTTPS──> [Cloudflare] ──Tunnel──> [본인 PC의 Ollama]
                                                    (외부 포트 노출 불필요)
```

- **본인 PC가 켜져 있을 때만** AI 기능 작동
- **무료** · HTTPS 자동 적용 · 방화벽 통과
- 매번 같은 URL 사용 가능 (고정 도메인 옵션)

---

## ✅ 사전 준비

- Windows PC 1대
- Ollama 설치 (이미 되어 있음)
- Cloudflare 계정 (무료, 가입 1분)

---

## 1단계: Ollama 외부 접근 허용 (1회 설정)

### Windows PowerShell 환경변수 설정

`Win + R` → `sysdm.cpl` → 고급 → 환경변수 클릭 →
**시스템 변수**에 다음 2개 추가:

| 변수명 | 값 |
|---|---|
| `OLLAMA_HOST` | `0.0.0.0:11434` |
| `OLLAMA_ORIGINS` | `https://lyrze.github.io,http://localhost:*` |

설정 후 **Ollama 재시작**:
```powershell
# 시스템 트레이의 Ollama 아이콘 → Quit
# 다시 Ollama 실행
```

> 💡 `OLLAMA_ORIGINS`는 CORS 허용 도메인입니다. 본인 GitHub Pages URL을 적어주세요.

---

## 2단계: Cloudflared 설치 (1회)

### Option A: winget (권장)
```powershell
winget install --id Cloudflare.cloudflared
```

### Option B: 수동 다운로드
1. https://github.com/cloudflare/cloudflared/releases 접속
2. `cloudflared-windows-amd64.exe` 다운로드
3. `cloudflared.exe`로 이름 변경 후 `C:\Tools\cloudflared\` 같은 경로에 저장
4. PATH 환경변수에 해당 폴더 추가

### 설치 확인
```powershell
cloudflared --version
# cloudflared version 2024.x.x (출력되면 OK)
```

---

## 3단계: Cloudflare 인증 (1회)

```powershell
cloudflared tunnel login
```

브라우저가 열리고 → Cloudflare 계정 로그인 → 도메인 선택 → 인증 완료

> 💡 도메인이 없으면 **임시 URL 방식**으로 건너뛸 수 있습니다 (4단계-B 참조)

---

## 4단계: 터널 실행

### Option A: 고정 도메인 (도메인 보유 시 — 권장)

#### A-1. 터널 생성 (1회)
```powershell
cloudflared tunnel create crema-ollama
# Tunnel ID 출력: abc123-... (메모해두기)
```

#### A-2. DNS 라우팅 설정 (1회)
```powershell
cloudflared tunnel route dns crema-ollama ollama.yourdomain.com
# yourdomain.com 부분을 본인 도메인으로 변경
```

#### A-3. 설정 파일 작성
`C:\Users\올릿\.cloudflared\config.yml` 생성:
```yaml
tunnel: crema-ollama
credentials-file: C:\Users\올릿\.cloudflared\<TUNNEL_ID>.json

ingress:
  - hostname: ollama.yourdomain.com
    service: http://localhost:11434
  - service: http_status:404
```

#### A-4. 터널 실행
```powershell
cloudflared tunnel run crema-ollama
```

✅ 이제 `https://ollama.yourdomain.com` 으로 접속 가능

---

### Option B: 임시 URL (도메인 없을 때 — 가장 간단)

```powershell
cloudflared tunnel --url http://localhost:11434
```

출력 예시:
```
2026-05-29T10:00:00Z INF +--------------------------------------------------------+
2026-05-29T10:00:00Z INF |  Your quick Tunnel has been created! Visit it at:      |
2026-05-29T10:00:00Z INF |  https://random-name-12345.trycloudflare.com           |
2026-05-29T10:00:00Z INF +--------------------------------------------------------+
```

⚠️ **단점**: PC 재시작 또는 cloudflared 재실행 시 URL 변경됨 → 매번 대시보드에 새 URL 입력 필요

---

## 5단계: 대시보드에서 사용

1. https://lyrze.github.io/crema-review-dashboard/ 접속
2. 좌측 사이드바 하단 **AI 서버 URL** 입력란에 발급된 URL 입력
   - 예: `https://ollama.yourdomain.com` 또는 `https://random-name-12345.trycloudflare.com`
3. Enter 키 또는 입력란 외부 클릭으로 저장
4. 우측 상단 ✦ Hey Sloom 챗 또는 카드 ✦ AI 분석 클릭 → AI 응답 확인

> 💾 localStorage에 저장되어 브라우저를 닫아도 유지됩니다
> ↺ "로컬" 버튼으로 언제든 `http://localhost:11434`로 초기화 가능

---

## 🔒 보안 옵션 (선택)

기본 설정은 누구든 URL을 알면 사용 가능합니다. 보안이 필요하면:

### Option 1: Cloudflare Access (무료 50명까지)
1. Cloudflare Dashboard → Zero Trust → Access → Applications
2. 본인 터널 도메인 추가
3. **Email OTP** 정책 추가 (특정 이메일만 허용)
4. → 접근 시 이메일 인증 필요

### Option 2: 간단한 토큰 (다음 작업 필요)
대시보드에서 `?token=xxx` 헤더 자동 추가
→ 필요하면 알려주세요. 코드 추가해드립니다.

---

## 🔄 자동 시작 (Windows 부팅 시 자동 실행)

`C:\Users\올릿\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\`에
`start-tunnel.bat` 파일 생성:

```batch
@echo off
cd /d C:\Users\올릿\.cloudflared
cloudflared tunnel run crema-ollama
```

→ PC 재부팅 후 자동으로 터널 시작

---

## ❓ 문제 해결

### Q1. "CORS error" 발생
- `OLLAMA_ORIGINS` 환경변수에 정확한 도메인 추가 후 Ollama 재시작
- 콘솔 에러 메시지의 도메인을 그대로 추가

### Q2. "Connection refused"
- Ollama가 실행 중인지 확인: `ollama serve`
- `OLLAMA_HOST=0.0.0.0:11434` 설정 후 재시작했는지 확인

### Q3. 임시 URL이 계속 바뀜
- 고정 도메인(Option A)으로 전환 필요
- 도메인 없으면 Freenom 등에서 무료 도메인 발급 가능

### Q4. 다른 사람도 사용하려면?
- 현재 URL을 동료에게 공유 → 동료가 대시보드 사이드바에 입력
- 보안 필요 시 Cloudflare Access로 이메일 제한 적용

### Q5. 응답이 너무 느림
- 인터넷 업로드 속도가 병목 (집/회사 회선에 따라 다름)
- 모델을 작은 것으로 변경: `ollama pull qwen2.5:3b` (1.9GB, 더 빠름)

---

## 🎯 요약

| 단계 | 시간 | 빈도 |
|---|---|---|
| 1. Ollama 환경변수 설정 | 5분 | 1회 |
| 2. cloudflared 설치 | 2분 | 1회 |
| 3. Cloudflare 인증 | 1분 | 1회 |
| 4. 터널 실행 | 30초 | PC 켤 때마다 (자동화 가능) |
| 5. 대시보드 URL 입력 | 10초 | 사용자별 1회 |

**총 1회 셋업 ~10분, 이후 PC 켜기만 하면 어디서든 사용 가능!**

---

## 📞 다음 단계 추천

1. **임시 URL부터 테스트** (Option B, 도메인 불필요)
2. 잘 되면 **고정 도메인 설정** (Option A)
3. 외부 공유 시 **Cloudflare Access** 이메일 인증 추가

문제 발생 시 콘솔 에러 메시지와 함께 알려주세요!
