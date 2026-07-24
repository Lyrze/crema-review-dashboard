"""
크리마 대시보드 로컬 프록시

한 개의 로컬 포트(기본 8799)로 두 가지를 처리한다:
  1. /api/*, / (health)  → AI 백엔드로 중계 (AI_BACKEND=ollama: 로컬 Ollama(11434) 중계 /
     AI_BACKEND=claude: Claude Code CLI 구독 인증으로 대신 처리 — GPU 불필요)
  2. /gh/upload|list|load → GitHub Contents API로 Taxonomy 분류값 커밋/목록/조회

대시보드(docs/index.html)는 두 백엔드 모두에게 동일한 Ollama API 모양
(GET / · GET /api/tags · POST /api/generate)으로 말을 걸므로, 브라우저 쪽 코드는
백엔드가 무엇이든 수정 없이 그대로 동작한다.

GitHub 토큰은 이 PC에만 보관(공개 저장소·브라우저에 노출 안 됨):
  - 환경변수 GH_TOKEN, 또는
  - scripts/.gh_token 파일, 또는
  - ~/.crema_gh_token 파일

이 파일은 start_tunnel.py 가 자동 실행하며, Cloudflare 터널이 이 포트를 외부에 노출한다.

설정(환경변수, 선택):
  AI_BACKEND   'ollama'(기본) | 'claude' — claude면 GPU 없이 Claude CLI로 AI 기능 처리
  GH_TOKEN     GitHub 개인 토큰 (contents:write 권한)
  GH_REPO      기본 'Lyrze/crema-review-dashboard'
  GH_BRANCH    기본 'main'
  OLLAMA_URL   기본 'http://localhost:11434' (AI_BACKEND=ollama 일 때만 사용)
  PROXY_PORT   기본 8799

AI_BACKEND=claude 사용 예:
  set AI_BACKEND=claude
  python scripts/local_proxy.py
  → 대시보드 사이드바 "AI 서버 URL"에 이 프록시 주소(예: http://localhost:8799)를 넣으면
    AI 관련 버튼들이 전부 Claude CLI로 동작한다. Claude 로그인이 안 돼 있으면
    /api/generate 호출 시 자동으로 브라우저 로그인 창을 띄운다(최초 1회, 최대 3분 대기).
"""
from __future__ import annotations

import base64
import datetime
import http.server
import json
import os
import subprocess
import sys
import time
import urllib.parse

import requests

# Windows 한국어 콘솔(cp949)에서 유니코드 기호 출력 시 크래시 방지 → 표준출력을 UTF-8로 강제
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
REPO = os.environ.get("GH_REPO", "Lyrze/crema-review-dashboard")
BRANCH = os.environ.get("GH_BRANCH", "main")
PORT = int(os.environ.get("PROXY_PORT", "8799"))
GH_API = "https://api.github.com"
AI_BACKEND = os.environ.get("AI_BACKEND", "ollama").strip().lower()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if AI_BACKEND == "claude":
    from claude_engine import ClaudeClient, _find_claude, _looks_like_quota  # noqa: E402

CLAUDE_MODELS = ["sonnet", "opus", "haiku"]  # /api/tags 가 대시보드 모델 드롭다운에 제공하는 고정 목록
_claude_client = None  # 지연 생성 (AI_BACKEND=claude 일 때만)


def _get_claude_client():
    global _claude_client
    if _claude_client is None:
        _claude_client = ClaudeClient(timeout=90)
    return _claude_client


def claude_logged_in() -> bool:
    """claude auth status 로 로그인 여부 확인(빠름 · 비파괴적)."""
    try:
        exe = _find_claude()
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                            timeout=10, shell=exe.lower().endswith(".cmd"))
        return bool(json.loads(r.stdout or "{}").get("loggedIn"))
    except Exception:
        return False


def claude_ensure_login() -> bool:
    """로그인 안 돼 있으면 'claude auth login' 실행 → 브라우저 로그인 창이 뜬다(최대 3분 대기).
    이미 로그인돼 있으면 아무것도 하지 않고 즉시 True 반환."""
    if claude_logged_in():
        return True
    print("  [AUTH] Claude 로그인이 필요합니다 — 브라우저 로그인 창을 여는 중...")
    try:
        exe = _find_claude()
        subprocess.run([exe, "auth", "login"], timeout=180,
                        shell=exe.lower().endswith(".cmd"))
    except Exception as exc:
        print("  [AUTH] 로그인 프로세스 실행 실패:", exc)
        return False
    ok = claude_logged_in()
    print("  [AUTH] 로그인 " + ("완료" if ok else "실패/시간초과"))
    return ok


def get_token() -> str:
    """GitHub 토큰을 환경변수 또는 로컬 파일에서 읽는다 (공개 저장소에 절대 커밋 금지)."""
    t = os.environ.get("GH_TOKEN", "").strip()
    if t:
        return t
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".gh_token"),
        os.path.join(os.path.expanduser("~"), ".crema_gh_token"),
    ]
    for p in candidates:
        try:
            if os.path.exists(p):
                return open(p, encoding="utf-8").read().strip()
        except OSError:
            pass
    return ""


def gh_headers(raw: bool = False) -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "crema-proxy",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tok = get_token()
    if tok:
        h["Authorization"] = "token " + tok
    return h


def _safe_seg(s: str, fallback: str) -> str:
    """경로 한 세그먼트로 안전하게: 슬래시·금지문자 제거 + 상위경로(..) 차단."""
    s = "".join(c for c in (s or "") if c not in '/\\:*?"<>|').strip()
    s = s.replace("..", "")          # 상위 경로 이동 차단
    s = s.strip(". ")                # 선행/후행 점·공백 제거
    return s or fallback


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # 콘솔 조용히
        pass

    # ── CORS ──
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path.startswith("/gh/list"):
            return self.gh_list(q)
        if u.path.startswith("/gh/load"):
            return self.gh_load(q)
        if u.path == "/gh/health":
            return self._json(200, {"ok": True, "repo": REPO, "token": bool(get_token())})
        if AI_BACKEND == "claude":
            # health(/)·모델목록(/api/tags) 은 로그인 여부와 무관하게 즉시 응답한다.
            # (대시보드의 온라인 점검은 3초 타임아웃이 있어, 여기서 로그인 절차로 블로킹하면
            #  항상 타임아웃돼 "오프라인"으로 보임 — 실제 로그인 확인/유도는 /api/generate 에서 처리)
            if u.path == "/":
                return self._json(200, {"ok": True, "backend": "claude"})
            if u.path.startswith("/api/tags"):
                return self._json(200, {"models": [{"name": m} for m in CLAUDE_MODELS]})
            return self._json(404, {"error": "not found"})
        return self.proxy_get()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/gh/upload"):
            return self.gh_upload()
        if AI_BACKEND == "claude" and u.path.startswith("/api/generate"):
            return self.claude_generate()
        return self.proxy_post()

    # ── Ollama 중계 ──
    def proxy_get(self):
        try:
            r = requests.get(OLLAMA + self.path, timeout=15)
            self.send_response(r.status_code)
            self._cors()
            self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(r.content)))
            self.end_headers()
            self.wfile.write(r.content)
        except requests.RequestException as e:
            self._json(502, {"error": "ollama: " + str(e)})

    def proxy_post(self):
        ln = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(ln) if ln else b""
        try:
            r = requests.post(
                OLLAMA + self.path, data=body, stream=True,
                headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
                timeout=600,
            )
            self.send_response(r.status_code)
            self._cors()
            self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
            self.send_header("Connection", "close")  # HTTP/1.0: 연결 종료까지 스트리밍
            self.end_headers()
            for chunk in r.iter_content(chunk_size=None):
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except requests.RequestException as e:
            try:
                self._json(502, {"error": "ollama: " + str(e)})
            except OSError:
                pass
        except OSError:
            pass  # 클라이언트 연결 끊김

    # ── Claude 중계 (AI_BACKEND=claude) ──
    def claude_generate(self):
        ln = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(ln) if ln else b""
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "잘못된 요청 본문"})

        model = payload.get("model") or "sonnet"
        if model not in CLAUDE_MODELS:
            model = "sonnet"  # Ollama 모델명이 남아있는 경우(구 localStorage) 등 폴백
        prompt = payload.get("prompt") or ""
        system = payload.get("system") or ""
        stream = bool(payload.get("stream", True))
        temperature = ((payload.get("options") or {}).get("temperature"))
        if temperature is None:
            temperature = 0.3

        # 최초 호출 또는 세션 만료 시 브라우저 로그인 창을 띄우고 완료까지 대기(최대 3분).
        # /api/generate 는 브라우저 쪽에 타임아웃이 없어(GET / 과 달리) 여기서 블로킹해도 안전하다.
        if not claude_ensure_login():
            return self._respond_text(
                stream,
                "⚠️ Claude 로그인이 완료되지 않았습니다. 브라우저에서 로그인 후 버튼을 다시 눌러주세요.",
            )

        try:
            text = _get_claude_client().generate(model, prompt, system=system, temperature=temperature)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if _looks_like_quota(msg):
                return self._respond_text(
                    stream, "⏳ Claude 세션 사용량 한도에 도달했습니다. 잠시 후(리셋 시각 이후) 다시 시도해주세요.",
                )
            return self._respond_text(stream, "❌ Claude 호출 실패: " + msg[:200])

        return self._respond_text(stream, text)

    def _respond_text(self, stream: bool, text: str):
        """Ollama /api/generate 응답 모양으로 맞춰 반환.
        stream=False: 단일 JSON {"response": text}
        stream=True : NDJSON 여러 줄로 잘게 나눠 흘려보내 타이핑 효과 유지(Claude CLI는
                      실시간 토큰 스트리밍을 지원하지 않아 전체 텍스트를 받은 뒤 흉내만 낸다)."""
        if not stream:
            return self._json(200, {"response": text, "done": True})
        try:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Connection", "close")
            self.end_headers()
            CHUNK = 6
            for i in range(0, len(text), CHUNK):
                line = json.dumps({"response": text[i:i + CHUNK], "done": False}, ensure_ascii=False)
                self.wfile.write((line + "\n").encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.015)
            self.wfile.write((json.dumps({"response": "", "done": True}, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except OSError:
            pass  # 클라이언트 연결 끊김

    # ── GitHub ──
    def gh_upload(self):
        if not get_token():
            return self._json(400, {"error": "GH_TOKEN 미설정 — 가이드(GITHUB_UPLOAD_GUIDE.md) 참고"})
        ln = int(self.headers.get("Content-Length", "0") or 0)
        try:
            payload = json.loads(self.rfile.read(ln) or b"{}")
        except (ValueError, OSError):
            return self._json(400, {"error": "잘못된 요청 본문"})
        # 경로 주입 방지: brand/month/owner 전부 안전 세그먼트로 정제 (.. / 슬래시 차단)
        brand = _safe_seg(payload.get("brand"), "슬룸")
        month = _safe_seg(payload.get("month"), "unknown")
        owner = _safe_seg(payload.get("owner"), "담당자")
        data = payload.get("data")
        if data is None:
            return self._json(400, {"error": "분류 데이터(data) 없음"})
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = "docs/data/%s/%s/taxonomy/%s__%s.json" % (brand, month, owner, ts)
        content_obj = {
            "brand": brand, "month": month, "owner": payload.get("owner") or owner,
            "uploaded_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "taxonomies": data,
            "reclassify": payload.get("reclassify"),  # AI 전체 재분류 결과(있으면)
        }
        b64 = base64.b64encode(
            json.dumps(content_obj, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii")
        url = GH_API + "/repos/" + REPO + "/contents/" + urllib.parse.quote(path)
        sha = None
        try:
            g = requests.get(url, headers=gh_headers(), params={"ref": BRANCH}, timeout=15)
            if g.status_code == 200:
                sha = g.json().get("sha")
        except requests.RequestException:
            pass
        commit = {
            "message": "taxonomy: %s %s %s (%s)" % (brand, month, owner, ts),
            "content": b64, "branch": BRANCH,
        }
        if sha:
            commit["sha"] = sha
        try:
            put = requests.put(url, headers=gh_headers(), json=commit, timeout=30)
            if put.status_code in (200, 201):
                return self._json(200, {"ok": True, "path": path, "owner": content_obj["owner"], "ts": ts})
            return self._json(put.status_code, {"error": "GitHub %d" % put.status_code, "detail": put.text[:300]})
        except requests.RequestException as e:
            return self._json(502, {"error": str(e)})

    def gh_list(self, q):
        brand = _safe_seg(q.get("brand", ["슬룸"])[0], "슬룸")
        month = _safe_seg(q.get("month", [""])[0], "unknown")
        path = "docs/data/%s/%s/taxonomy" % (brand, month)
        url = GH_API + "/repos/" + REPO + "/contents/" + urllib.parse.quote(path)
        try:
            r = requests.get(url, headers=gh_headers(), params={"ref": BRANCH}, timeout=15)
            if r.status_code == 404:
                return self._json(200, {"files": []})
            if r.status_code != 200:
                return self._json(r.status_code, {"error": "GitHub %d" % r.status_code})
            files = []
            for it in r.json():
                if it.get("type") == "file" and it.get("name", "").endswith(".json"):
                    nm = it["name"][:-5]
                    owner, _, ts = nm.partition("__")
                    files.append({"name": it["name"], "path": it["path"],
                                  "owner": owner, "ts": ts.replace("_", " ")})
            files.sort(key=lambda x: x["ts"], reverse=True)
            return self._json(200, {"files": files})
        except requests.RequestException as e:
            return self._json(502, {"error": str(e)})

    def gh_load(self, q):
        path = q.get("path", [""])[0]
        if not path:
            return self._json(400, {"error": "path 없음"})
        # 화이트리스트: taxonomy 폴더의 .json 만 허용 (.. 차단)
        if (".." in path or not path.startswith("docs/data/")
                or "/taxonomy/" not in path or not path.endswith(".json")):
            return self._json(400, {"error": "허용되지 않는 경로"})
        url = GH_API + "/repos/" + REPO + "/contents/" + urllib.parse.quote(path)
        try:
            r = requests.get(url, headers=gh_headers(), params={"ref": BRANCH}, timeout=15)
            if r.status_code != 200:
                return self._json(r.status_code, {"error": "GitHub %d" % r.status_code})
            j = r.json()
            raw = base64.b64decode(j.get("content", "")).decode("utf-8") if j.get("content") else "{}"
            return self._json(200, json.loads(raw))
        except (requests.RequestException, ValueError) as e:
            return self._json(502, {"error": str(e)})


def main():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 64)
    print("  크리마 로컬 프록시  http://127.0.0.1:%d" % PORT)
    if AI_BACKEND == "claude":
        print("  - AI 백엔드   : Claude Code CLI (GPU 불필요) — 로그인 상태: %s" %
              ("로그인됨 [OK]" if claude_logged_in() else "미로그인 (첫 AI 호출 시 브라우저 로그인 창)"))
    else:
        print("  - AI 백엔드   : Ollama 중계 → %s" % OLLAMA)
    print("  - GitHub 저장소: %s (%s)" % (REPO, BRANCH))
    print("  - GitHub 토큰 : %s" % ("설정됨 [OK]" if get_token() else "없음 [X] (업로드 불가, 가이드 참고)"))
    print("=" * 64)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
