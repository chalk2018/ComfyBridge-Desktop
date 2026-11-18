import json

for f in ("scene_sheet", "prop_sheet", "board_story"):
    d = json.load(open(f"../prompts/{f}.json", encoding="utf-8"))
    print("=" * 12, f, list(d.keys()))
    for k in ("styles", "style_presets", "ink", "inks", "line_styles", "blockedSlots", "refSlots", "tail", "head"):
        if k in d:
            v = d[k]
            if isinstance(v, list):
                items = [x.get("name") if isinstance(x, dict) else x for x in v]
                print(f"  {k} ({len(items)}):", items[:20])
            else:
                print(f"  {k}:", str(v)[:300])
    for lay in d.get("layouts", []):
        print("  layout", lay.get("id"), lay.get("name"), "mode", lay.get("mode"), "subs",
              [(s.get("id"), s.get("name")) for s in (lay.get("subs") or [])])
