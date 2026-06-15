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

    # 2. Ollama 모델 선택
    eprint()
    eprint("  [2/4] AI 분석 설정...")
    models = get_ollama_models()
    ai_flag = "--skip-ai"

    if models:
        eprint()
        eprint("  Ollama 온라인! 설치된 모델:")
        eprint()
        for i, m in enumerate(models, 1):
            eprint(f"    {i}. {m}")
        eprint("    0. AI 분석 건너뛰기")
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

    anon_out = f"data/anonymized/{brand}/{month}/reviews_anon.csv"

    eprint()
    eprint("  [4/4] 데이터 처리를 시작합니다...")
    eprint()

    # stdout에 결과 출력 (배치파일이 파싱)
    print(f"BRAND={brand}")
    print(f"MONTH={month}")
    print(f"CSV={csv_path}")
    print(f"PREV_FLAG={prev_flag}")
    print(f"AI_FLAG={ai_flag}")
    print(f"RECLASS_FLAG={reclass_flag}")
    print(f"REVERIFY_FLAG={reverify_flag}")
    print(f"ANON_OUT={anon_out}")


if __name__ == "__main__":
    main()
