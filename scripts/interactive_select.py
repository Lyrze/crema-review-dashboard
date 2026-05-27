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

    eprint("  [1/3] 처리 가능한 데이터:")
    eprint()
    for i, x in enumerate(items, 1):
        status = "[완료]" if x["done"] else "[미처리]"
        eprint(f"    {i}. {x['brand']} / {x['month']}  {status}")
    eprint()

    # 번호 선택
    while True:
        try:
            raw_input = input(f"  처리할 번호 입력 (1~{len(items)}): ")
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
    eprint("  [2/3] AI 분석 설정...")
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
                raw_input = input(f"  모델 번호 선택 (0~{len(models)}, Enter=1번): ")
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

    anon_out = f"data/anonymized/{brand}/{month}/reviews_anon.csv"

    eprint()
    eprint("  [3/3] 데이터 처리를 시작합니다...")
    eprint()

    # stdout에 결과 출력 (배치파일이 파싱)
    print(f"BRAND={brand}")
    print(f"MONTH={month}")
    print(f"CSV={csv_path}")
    print(f"PREV_FLAG={prev_flag}")
    print(f"AI_FLAG={ai_flag}")
    print(f"ANON_OUT={anon_out}")


if __name__ == "__main__":
    main()
