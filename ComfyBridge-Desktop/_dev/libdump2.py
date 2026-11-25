import json

d = json.load(open("../prompts/character_sheet.json", encoding="utf-8"))
print([ (x.get("id"), x.get("name"), str(x.get("text") or x.get("desc") or "")[:40]) for x in d.get("line_styles", []) ])
print("styles:", [x["name"] for x in d.get("styles", [])])
print("layouts:", [(l["id"], l["name"], l.get("ratio"), l.get("size")) for l in d.get("layouts", [])])
