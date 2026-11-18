import json
import urllib.request

d = json.load(urllib.request.urlopen("http://127.0.0.1:8190/api/state", timeout=30))
ids = [c["id"] for c in d["extCombos"]["scene"]]
print(len(ids))
for i in ids:
    print(i)
