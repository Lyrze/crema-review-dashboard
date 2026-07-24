"""
AI 서버 Cloudflare Quick Tunnel 자동 실행 스크립트

기능:
  1. cloudflared tunnel을 실행하고 stdout 캡처
  2. trycloudflare.com URL을 자동 추출
  3. URL을 클립보드에 자동 복사
  4. Windows 알림으로 사용자에게 안내
  5. 대시보드 URL을 브라우저에서 자동 오픈
  6. Ctrl+C 또는 창 닫으면 graceful 종료

사용:
  python start_tunnel.py
  또는 같은 폴더의 start-tunnel.bat 더블클릭

  대시보드의 라이브 AI 기능(AI 요약/자동 분류 등)은 Claude Code CLI로 처리한다 — GPU 불필요.

요구:
  - cloudflared (winget install Cloudflare.cloudflared)
  - Claude Code CLI 로그인만 되어 있으면 됨(미로그인 시 첫 AI 호출 때
    local_proxy.py 가 자동으로 브라우저 로그인 창을 띄움)
"""

import subprocess
import os
import re
import sys
import time
import threading
import webbrowser

# Windows 한국어 콘솔(cp949)에서 한글/특수기호(예: — U+2014) 출력 시 UnicodeEncodeError로
# 크래시하는 것을 방지 — interactive_select.py/local_proxy.py 와 동일한 관례.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DASHBOARD_URL = "https://lyrze.github.io/crema-review-dashboard/"
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def copy_to_clipboard(text: str) -> bool:
    """클립보드에 텍스트 복사 (tkinter 사용)."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        # 약간의 지연 후 종료 (Windows 클립보드 안정화)
        root.after(200, root.destroy)
        root.mainloop()
        return True
    except Exception as exc:
        print(f"[경고] 클립보드 복사 실패: {exc}")
        return False


def show_notification(title: str, message: str) -> None:
    """Windows 알림(메시지 박스)."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        # 항상 최상위로
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message, parent=root)
        root.destroy()
    except Exception as exc:
        print(f"[경고] 알림 표시 실패: {exc}")


def open_dashboard() -> None:
    """대시보드 URL을 기본 브라우저에서 오픈."""
    try:
        webbrowser.open(DASHBOARD_URL)
    except Exception as exc:
        print(f"[경고] 브라우저 오픈 실패: {exc}")


def main() -> int:
    print("=" * 70)
    print("   AI 서버 Cloudflare Quick Tunnel (백엔드: Claude Code CLI)")
    print("=" * 70)
    print()

    # 로컬 프록시 실행 (Claude CLI 중계 + GitHub 저장소 업로드)
    proxy_port = os.environ.get("PROXY_PORT", "8799")
    proxy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_proxy.py")
    proxy_proc = None
    if os.path.exists(proxy_path):
        try:
            proxy_proc = subprocess.Popen(
                [sys.executable, proxy_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            # 프록시 기동 로그 한두 줄 출력
            time.sleep(1.2)
            print("[OK] 로컬 프록시 시작 (127.0.0.1:%s) — AI 중계 + 저장소 업로드" % proxy_port)
            # GitHub 토큰 안내
            from local_proxy import get_token  # type: ignore[import]
            if not get_token():
                print("[!] GitHub 토큰 미설정 → 업로드는 비활성(불러오기는 가능). "
                      "scripts/.gh_token 파일에 토큰을 넣으면 업로드됩니다.")
        except Exception as exc:  # noqa: BLE001
            print("[!] 로컬 프록시 시작 실패 (%s) — AI 기능을 쓸 수 없습니다." % exc)
            proxy_proc = None
    print()
    print("Cloudflare Tunnel 시작 중...")
    print("(잠시만 기다려주세요, 약 3~10초 소요)")
    print()

    # 2. cloudflared 실행 — 항상 로컬 프록시 포트를 대상으로 한다(AI 처리는 프록시가 전담)
    # localhost는 Windows에서 IPv6(::1)로 먼저 해석되어 프록시(127.0.0.1 바인딩)에 못 붙는다 → 127.0.0.1 명시
    tunnel_target = "http://127.0.0.1:%s" % proxy_port
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", tunnel_target],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            universal_newlines=True,
        )
    except FileNotFoundError:
        print("[X] cloudflared가 PATH에 없습니다.")
        print()
        print("    설치 명령:")
        print("      winget install --id Cloudflare.cloudflared")
        print()
        show_notification(
            "cloudflared 미설치",
            "cloudflared를 먼저 설치해주세요.\n\n"
            "PowerShell에서 실행:\n"
            "winget install --id Cloudflare.cloudflared\n\n"
            "설치 후 PowerShell을 닫고 다시 실행하세요.",
        )
        input("\n[Enter] 키를 눌러 종료...")
        return 1

    url_found = None

    # 3. stdout을 한 줄씩 읽으며 URL 매칭
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()

            if not url_found:
                match = URL_PATTERN.search(line)
                if match:
                    url_found = match.group(0)
                    print()
                    print("=" * 70)
                    print(f"  [O] 터널 URL 발급 완료!")
                    print(f"      {url_found}")
                    print("=" * 70)
                    print()

                    # 클립보드 복사
                    if copy_to_clipboard(url_found):
                        print("  [O] 클립보드에 URL 복사 완료")
                    print()

                    print("  ⚠️  이 URL은 '이 PC에 로그인된 Claude 계정'을 그대로 씁니다.")
                    print("      팀원과 공유하면 그 팀원도 당신의 계정/한도를 쓰게 됩니다!")
                    print("      팀원은 각자 자기 PC에서 start-ai-local.bat(또는 이 터널)을")
                    print("      직접 실행해 자기 계정으로 쓰게 하세요. 이 URL은 '내가 다른")
                    print("      기기(폰 등)에서 내 PC로 접속'하는 용도로만 쓰는 걸 권장합니다.")
                    print()

                    # 별도 스레드에서 알림 + 대시보드 오픈
                    def notify_thread():
                        time.sleep(1.5)  # 터널 안정화 대기
                        open_dashboard()
                        warn = ("\n\n⚠️ 팀원과 공유 금지: 이 URL은 이 PC의 Claude 계정을 그대로 씁니다.\n"
                                "팀원은 각자 자기 PC에서 실행하세요.")
                        show_notification(
                            "AI Tunnel 시작됨 ✓",
                            f"터널 URL이 클립보드에 복사되었습니다:\n\n"
                            f"{url_found}\n\n"
                            "[다음 단계]\n"
                            "1. 열린 대시보드에서 좌측 사이드바 하단 'AI 서버 URL'에\n"
                            "   Ctrl+V로 붙여넣기 후 Enter\n"
                            "2. ✦ Hey Sloom 클릭하여 AI 응답 확인\n\n"
                            "⚠️ 이 터널 창을 닫으면 AI 기능이 중단됩니다." + warn,
                        )

                    threading.Thread(target=notify_thread, daemon=True).start()

    except KeyboardInterrupt:
        print()
        print("[!] Ctrl+C 감지 - 터널 종료 중...")
        proc.terminate()
        proc.wait(timeout=5)
        if proxy_proc:
            proxy_proc.terminate()
        print("[OK] 터널 정상 종료")
        return 0
    except Exception as exc:
        print(f"\n[X] 오류 발생: {exc}")
        proc.terminate()
        if proxy_proc:
            proxy_proc.terminate()
        return 1

    # 4. cloudflared가 자체 종료된 경우
    if proxy_proc:
        proxy_proc.terminate()
    rc = proc.wait()
    print()
    print(f"[!] cloudflared가 종료되었습니다 (exit code: {rc})")
    input("\n[Enter] 키를 눌러 창을 닫으세요...")
    return rc


if __name__ == "__main__":
    sys.exit(main())
