import os, sys

raw = 'data/raw'
items = []

if os.path.isdir(raw):
    for brand in sorted(os.listdir(raw)):
        bp = os.path.join(raw, brand)
        if not os.path.isdir(bp):
            continue
        for month in sorted(os.listdir(bp)):
            mp = os.path.join(bp, month)
            csv_path = os.path.join(mp, 'reviews.csv')
            if not os.path.isfile(csv_path):
                continue
            done = os.path.isfile(f'docs/data/{brand}/{month}/summary.json')
            items.append({'brand': brand, 'month': month, 'csv': csv_path, 'done': done})

if not items:
    print("COUNT=0")
    sys.exit(0)

print(f"COUNT={len(items)}")
for i, x in enumerate(items, 1):
    print(f"ITEM_{i}_BRAND={x['brand']}")
    print(f"ITEM_{i}_MONTH={x['month']}")
    print(f"ITEM_{i}_CSV={x['csv']}")
    print(f"ITEM_{i}_DONE={1 if x['done'] else 0}")
    status = "[완료]" if x['done'] else "[미처리]"
    print(f"SHOW_{i}={i}. {x['brand']} / {x['month']}  {status}")

