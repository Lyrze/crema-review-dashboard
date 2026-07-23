"""
interactive_select.py
~~~~~~~~~~~~~~~~~~~~~
update-data.bat 에서 호출되어 대화형 메뉴를 처리합니다.
사용자 입력(메뉴 선택)은 stderr, 결과 KEY=VALUE는 stdout에 출력합니다.

배치파일이 읽는 KEY:
  BRAND, MONTH, CSV, PREV_FLAG, AI_FLAG, ANON_OUT
"""

import os
import sys
import json
import urllib.request

# update-data.bat 는 chcp 65001(UTF-8) 로 KEY=VALUE 임시파일을 파싱한다.
# 그런데 stdout 이 파일로 리다이렉트되면 Python 은 locale(cp949)로 쓰기 때문에
# 한글 BRAND/경로 줄이 깨져 for /f 가 그 줄을 건너뛴다(→ "선택 값 없음").
# stdout/stderr 을 UTF-8 로 강제해 배치 파싱과 인코딩을 일치시킨다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def ask(prompt=""):
    # input() 프롬프트는 기본적으로 stdout 으로 나가 KEY=VALUE 출력을 오염시킨다.
    # 프롬프트는 stderr(화면)로 보내고 stdin 만 읽어 stdout 을 깨끗하게 유지.
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return input()


def scan_raw():
    items = []
    raw = "data/raw"
    if not os.path.isdir(raw):
        return items
    for brand in sorted(os.listdir(raw)):
        bp = os.path.join(raw, brand)
        if not os.path.isdir(bp):
            continue
        for month in sorted(os.listdir(bp)):
            mp = os.path.join(bp, month)
            csv_path = os.path.join(mp, "reviews.csv")
            if not os.path.isfile(csv_path):
                continue
            done = os.path.isfile(f"docs/data/{brand}/{month}/summary.json")
            items.append({"brand": brand, "month": month, "csv": csv_path, "done": done})
    return items


def find_prev(brand, month):
    try:
        y, m = int(month[:4]), int(month[5:7])
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        prev = f"{y}-{m:02d}"
        p = f"data/raw/{brand}/{prev}/reviews.csv"
        return p if os.path.isfile(p) else ""
    except Exception:
        return ""


def get_ollama_models():
    try:
        r = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def main():
    eprint()
    eprint("  ==========================================")
    eprint("   Crema Review Dashboard - Data Update")
    eprint("  ==========================================")
    eprint()

    # 1. data/raw 스캔
    items = scan_raw()
    if not items:
        eprint("  [ERROR] data\\raw\\ 에 처리 가능한 reviews.csv 없음")
        eprint("          경로 형식: data\\raw\\{브랜드}\\{YYYY-MM}\\reviews.csv")
        sys.exit(1)

    eprint("  [1/4] 처리 가능한 데이터:")
    eprint()
    for i, x in enumerate(items, 1):
        status = "[완료]" if x["done"] else "[미처리]"
        eprint(f"    {i}. {x['brand']} / {x['month']}  {status}")
    eprint()

    # 번호 선택
    while True:
        try:
            raw_input = ask(f"  처리할 번호 입력 (1~{len(items)}): ")
            sel = int(raw_input.strip())
            if 1 <= sel <= len(items):
                break
            eprint(f"  [ERROR] 1~{len(items)} 범위로 입력하세요.")
        except ValueError:
            eprint("  [ERROR] 숫자를 입력하세요.")
        except EOFError:
            eprint("  [ERROR] 입력 없음.")
            sys.exit(1)

    item = items[sel - 1]
    brand = item["brand"]
    month = item["month"]
    csv_path = item["csv"]
    eprint(f"  선택: {brand} / {month}")

    # 전월 감지
    prev = find_prev(brand, month)
    if prev:
        eprint(f"  전월 데이터 발견: {prev}  [MoM 비교 활성화]")
        prev_flag = f'--prev-input "{prev}"'
    else:
        eprint("  전월 데이터 없음  [전월 비교 생략]")
        prev_flag = ""

    # 1.5 AI 엔진 선택 — Claude(구독 CLI, GPU 불필요, 한도 도달 시 자동 대기/재개) vs Ollama(로컬 GPU 필요)
    eprint()
    eprint("  [1.5/4] AI 엔진 선택")
    eprint("    1. Claude Code CLI (구독 인증 — GPU 불필요. 세션 한도 도달 시 자동 대기 후 재개)")
    eprint("    2. Ollama (로컬 설치 — GPU 필요, 무료/빠름)")
    eprint()
    while True:
        try:
            raw_in = ask("  선택 (1~2, Enter=1번): ").strip()
            engsel = 1 if raw_in == "" else int(raw_in)
            if engsel in (1, 2):
                break
            eprint("  1 또는 2를 입력하세요.")
        except ValueError:
            eprint("  1 또는 2를 입력하세요.")
        except EOFError:
            engsel = 1
            break
    engine = "claude" if engsel == 1 else "ollama"

    if engine == "claude":
        # Claude 경로 — GPU/모델 스캔 불필요. 세션 한도는 quota_retry.py 가 자동으로 흡수하므로
        # 매 단계를 굳이 물어보지 않고 전부 포함(품질 우선). 필요하면 update-data.bat 재실행 시
        # 이미 완료된 항목은 각 스크립트의 진행 마커로 건너뛴다(과금/시간 중복 없음).
        eprint()
        eprint("  Claude 선택됨 — 감성분석·키워드 정밀분류·PVOC 감성판정을 모두 Claude로 진행합니다.")
        eprint("  (세션 한도 도달 시 자동으로 대기했다가 재개 — 창을 닫아도 다음 update-data.bat 실행 시 이어집니다)")
        ai_flag = "--engine claude"
        reclass_flag = "--reclassify-full --reclassify-mode batch --engine claude"
        reverify_flag = "--engine claude"
        pvoc_intent_flag = "--engine claude"
        pvoc_reverify_flag = "--engine claude"

        anon_out = f"data/anonymized/{brand}/{month}/reviews_anon.csv"
        eprint()
        eprint("  [4/4] 데이터 처리를 시작합니다...")
        eprint()
        print(f"BRAND={brand}")
        print(f"MONTH={month}")
        print(f"CSV={csv_path}")
        print(f"PREV_FLAG={prev_flag}")
        print(f"ENGINE={engine}")
        print(f"AI_FLAG={ai_flag}")
        print(f"RECLASS_FLAG={reclass_flag}")
        print(f"REVERIFY_FLAG={reverify_flag}")
        print(f"PVOC_INTENT_FLAG={pvoc_intent_flag}")
        print(f"PVOC_REVERIFY_FLAG={pvoc_reverify_flag}")
        print(f"ANON_OUT={anon_out}")
        return

    # ── 이하 Ollama 경로 (기존 로직 그대로) ──
    eprint()
    eprint("  [2/4] AI 분석 설정 (리뷰별 감성분석 — 긍정/중립/부정 라벨 생성)")
    eprint("        ※ 모델을 고르면 리뷰마다 AI 감성이 reviews.json 에 기록되어")
    eprint("          대시보드 '감성기준' 토글이 정확히 동작합니다. 건너뛰면 별점기준만.")
    models = get_ollama_models()
    ai_flag = "--skip-ai"

    if models:
        eprint()
        eprint("  Ollama 온라인! 설치된 모델:")
        eprint()
        for i, m in enumerate(models, 1):
            eprint(f"    {i}. {m}")
        eprint("    0. AI 분석 건너뛰기 (감성분석 없음 → 별점기준만)")
        eprint()

        while True:
            try:
                raw_input = ask(f"  모델 번호 선택 (0~{len(models)}, Enter=1번): ")
                raw_input = raw_input.strip()
                msel = 1 if raw_input == "" else int(raw_input)
                if 0 <= msel <= len(models):
                    break
                eprint(f"  0~{len(models)} 범위로 입력하세요.")
            except ValueError:
                msel = 0
                break
            except EOFError:
                msel = 0
                break

        if msel == 0:
            eprint("  AI 분석 건너뜀.")
        else:
            chosen = models[msel - 1]
            ai_flag = f"--ollama-model {chosen}"
            eprint(f"  선택된 모델: {chosen}")
    else:
        eprint("  Ollama 오프라인 — AI 분석 건너뜀")

    # 3. AI 정밀 키워드 분류 (재분류 + 감성 판정) — 업로드 전에 정확하게
    reclass_flag = ""
    if models:
        eprint()
        eprint("  [3/4] AI 정밀 키워드 분류 (오매칭 제거 + 긍/부정 감성 판정)")
        eprint()
        eprint("    1. 포함 - 전체 리뷰 기준 정밀 분류 (권장, 약 20~40분 소요)")
        eprint("    2. 건너뛰기 - 정규식 분류 그대로 (빠르지만 부정확할 수 있음)")
        eprint()
        while True:
            try:
                raw_in = ask("  선택 (1~2, Enter=1번 권장): ").strip()
                rsel = 1 if raw_in == "" else int(raw_in)
                if rsel in (1, 2):
                    break
                eprint("  1 또는 2를 입력하세요.")
            except ValueError:
                eprint("  1 또는 2를 입력하세요.")
            except EOFError:
                rsel = 2
                break
        if rsel == 1:
            reclass_flag = "--reclassify-full --reclassify-mode batch"
            if ai_flag == "--skip-ai":
                # AI 분석은 건너뛰어도 재분류에는 모델이 필요 → 설치된 1번 모델 사용
                reclass_flag += f" --ollama-model {models[0]}"
                eprint(f"  재분류 모델: {models[0]}")
            eprint("  정밀 분류 포함 — 처리 시간이 깁니다. 창을 닫지 마세요.")
        else:
            eprint("  정밀 분류 건너뜀 — 대시보드에서 나중에 'AI 전체 재분류' 가능.")

    # 3.5 AI 정밀 보정 (의심 키워드만 더 큰 모델로 재검증 — 긍정·희망 누수 제거)
    #   7b 전체 재분류 후, 부정·개선 키워드의 현재 멤버만 14b급 모델로 재판정.
    #   거짓양성만 제거(추가 X)하므로 빠르고 안전. reverify_suspect.py 가 수행.
    reverify_flag = ""
    if reclass_flag and models:
        big = [m for m in models if any(t in m.lower() for t in ("14b", "32b", "70b", "72b"))]
        if big:
            eprint()
            eprint("  [3.5/4] AI 정밀 보정 (의심 키워드를 더 큰 모델로 재검증)")
            eprint("    7b가 놓치는 '가성비 좋다->불만' 같은 긍정 누수를 제거합니다.")
            eprint()
            eprint(f"    1. 포함 - {big[0]} 로 부정·개선 키워드 재검증 (권장, 약 5~15분)")
            eprint("    2. 건너뛰기")
            eprint()
            while True:
                try:
                    raw_in = ask("  선택 (1~2, Enter=1번 권장): ").strip()
                    vsel = 1 if raw_in == "" else int(raw_in)
                    if vsel in (1, 2):
                        break
                    eprint("  1 또는 2를 입력하세요.")
                except ValueError:
                    eprint("  1 또는 2를 입력하세요.")
                except EOFError:
                    vsel = 2
                    break
            if vsel == 1:
                reverify_flag = f"--model {big[0]}"
                eprint(f"  정밀 보정 모델: {big[0]}")
            else:
                eprint("  정밀 보정 건너뜀.")
        else:
            eprint()
            eprint("  [3.5/4] 14b 이상 모델 없음 → 정밀 보정 생략 (7b 3단계 결과 사용)")

    # 3.6 구매경험 VOC '감성기반' 데이터 (PVOC 토픽별 칭찬/불만 AI 판정)
    #   대시보드 ⑦구매경험 VOC '감성기반' 토글이 pvoc_intent.json 을 사용. 없으면 별점 폴백.
    pvoc_intent_flag = ""
    if models:
        eprint()
        eprint("  [3.6/4] 구매경험 VOC '감성기반' 데이터 생성 (PVOC 토픽별 칭찬/불만 AI 판정)")
        eprint("    별점이 아닌 '그 항목에 대한' 실제 의도로 긍/부정 분류 → 감성기반 토글에 사용")
        eprint()
        eprint("    1. 포함 (권장, 약 5~15분)")
        eprint("    2. 건너뛰기 (감성기반은 별점으로 폴백)")
        eprint()
        while True:
            try:
                raw_in = ask("  선택 (1~2, Enter=1번 권장): ").strip()
                psel = 1 if raw_in == "" else int(raw_in)
                if psel in (1, 2):
                    break
                eprint("  1 또는 2를 입력하세요.")
            except ValueError:
                eprint("  1 또는 2를 입력하세요.")
            except EOFError:
                psel = 2
                break
        if psel == 1:
            pvoc_model = models[msel - 1] if msel > 0 else models[0]
            pvoc_intent_flag = f"--model {pvoc_model}"
            eprint(f"  감성 데이터 모델: {pvoc_model}")
        else:
            eprint("  감성 데이터 생략 — 대시보드는 별점기반으로 표시됩니다.")

    # 3.7 PVOC 의도(감성) 14b 재검증 — 감성 데이터 생성 시 + 14b 이상 모델 있으면 자동
    #   7b가 '부정'으로 본 건만 큰 모델로 엄격 재판정해 거짓 부정 완화(제거형, 빠름·안전).
    pvoc_reverify_flag = ""
    if pvoc_intent_flag:
        big = [m for m in models if any(t in m.lower() for t in ("14b", "32b", "70b", "72b"))]
        if big:
            pvoc_reverify_flag = f"--model {big[0]}"
            eprint(f"  PVOC 의도 14b 재검증 자동 적용: {big[0]} (부정 거짓양성 완화)")

    anon_out = f"data/anonymized/{brand}/{month}/reviews_anon.csv"

    eprint()
    eprint("  [4/4] 데이터 처리를 시작합니다...")
    eprint()

    # stdout에 결과 출력 (배치파일이 파싱)
    print(f"BRAND={brand}")
    print(f"MONTH={month}")
    print(f"CSV={csv_path}")
    print(f"PREV_FLAG={prev_flag}")
    print(f"ENGINE={engine}")
    print(f"AI_FLAG={ai_flag}")
    print(f"RECLASS_FLAG={reclass_flag}")
    print(f"REVERIFY_FLAG={reverify_flag}")
    print(f"PVOC_INTENT_FLAG={pvoc_intent_flag}")
    print(f"PVOC_REVERIFY_FLAG={pvoc_reverify_flag}")
    print(f"ANON_OUT={anon_out}")


if __name__ == "__main__":
    main()
