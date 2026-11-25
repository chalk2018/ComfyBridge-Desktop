import json
import urllib.request

d = json.load(urllib.request.urlopen("http://127.0.0.1:8190/api/state", timeout=30))
for kind in ("scene", "prop"):
    combos = d["extCombos"][kind]
    print(kind, "combos", len(combos))
    keys = {}
    for c in combos:
        p = c.get("params") or c.get("default") or {}
        keys.setdefault(json.dumps(p, sort_keys=True), []).append(c["id"])
    print(" unique param sets", len(keys))
    for k, v in keys.items():
        print("  ", k[:120], "->", v[:4], "…" if len(v) > 4 else "")

print("board combos", len(d["boardCombos"]))
keys = {}
for c in d["boardCombos"]:
    keys.setdefault(json.dumps(c.get("params") or {}, sort_keys=True), []).append(c["id"])
print(" board unique", len(keys))
