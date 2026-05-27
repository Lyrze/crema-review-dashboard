import sys
try:
    import urllib.request, json
    r = urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2)
    data = json.loads(r.read())
    models = [m['name'] for m in data.get('models', [])]
    if not models:
        print("COUNT=0")
        sys.exit(0)
    print(f"COUNT={len(models)}")
    for i, m in enumerate(models, 1):
        print(f"MODEL_{i}={m}")
        print(f"SHOW_{i}={i}. {m}")
except Exception as e:
    print("COUNT=0")
    sys.exit(0)
