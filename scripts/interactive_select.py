"""
interactive_select.py
~~~~~~~~~~~~~~~~~~~~~
update-data.bat 에서 호출되어 대화형 메뉴를 처리합니다.
사용자 입력(메뉴 선택)은 stderr, 결과 KEY=VALUE는 stdout에 출력합니다.

배치파일이 읽는 KEY:
  BRAND, MONTH, CSV, PREV_FLAG, RECLASS_FLAG, ANON_OUT

AI 분석은 전부 Claude Code CLI(구독 인증)로 진행한다 — GPU/로컬 모델 선택 불필요.
세션 한도 도달 시 quota_retry.py 가 리셋시각까지 자동 대기 후 재개하므로, 단계별로
포함 여부를 물어보지 않고 항상 전체(정밀 키워드 재분류 등)를 포함한다.
"""

import os
import sys

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

    eprint("  [1/2] 처리 가능한 데이터:")
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

    eprint()
    eprint("  AI 분석: Claude Code CLI 로 감성분석·키워드 정밀분류·PVOC 감성판정을")
    eprint("  전부 자동 진행합니다 (세션 한도 도달 시 자동 대기 후 재개).")

    reclass_flag = "--reclassify-full --reclassify-mode batch"
    anon_out = f"data/anonymized/{brand}/{month}/reviews_anon.csv"

    eprint()
    eprint("  [2/2] 데이터 처리를 시작합니다...")
    eprint()

    # stdout에 결과 출력 (배치파일이 파싱)
    print(f"BRAND={brand}")
    print(f"MONTH={month}")
    print(f"CSV={csv_path}")
    print(f"PREV_FLAG={prev_flag}")
    print(f"RECLASS_FLAG={reclass_flag}")
    print(f"ANON_OUT={anon_out}")


if __name__ == "__main__":
    main()
