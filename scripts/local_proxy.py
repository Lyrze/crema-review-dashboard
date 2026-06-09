"""
크리마 대시보드 로컬 프록시

한 개의 로컬 포트(기본 8799)로 두 가지를 처리한다:
  1. /api/*, / (health)  → 로컬 Ollama(11434)로 중계 (AI 기능, 스트리밍 지원)
  2. /gh/upload|list|load → GitHub Contents API로 Taxonomy 분류값 커밋/목록/조회

GitHub 토큰은 이 PC에만 보관(공개 저장소·브라우저에 노출 안 됨):
  - 환경변수 GH_TOKEN, 또는
  - scripts/.gh_token 파일, 또는
  - ~/.crema_gh_token 파일

이 파일은 start_tunnel.py 가 자동 실행하며, Cloudflare 터널이 이 포트를 외부에 노출한다.

설정(환경변수, 선택):
  GH_TOKEN   GitHub 개인 토큰 (contents:write 권한)
  GH_REPO    기본 'Lyrze/crema-review-dashboard'
  GH_BRANCH  기본 'main'
  OLLAMA_URL 기본 'http://localhost:11434'
  PROXY_PORT 기본 8799
"""
from __future__ import annotations

import base64
import datetime
import http.server
import json
import os
import urllib.parse

import requests

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
REPO = os.environ.get("GH_REPO", "Lyrze/crema-review-dashboard")
BRANCH = os.environ.get("GH_BRANCH", "main")
PORT = int(os.environ.get("PROXY_PORT", "8799"))
GH_API = "https://api.github.com"


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
        return self.proxy_get()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/gh/upload"):
            return self.gh_upload()
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
    print("  - Ollama 중계 : %s" % OLLAMA)
    print("  - GitHub 저장소: %s (%s)" % (REPO, BRANCH))
    print("  - GitHub 토큰 : %s" % ("설정됨 ✓" if get_token() else "없음 ✗ (업로드 불가, 가이드 참고)"))
    print("=" * 64)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
