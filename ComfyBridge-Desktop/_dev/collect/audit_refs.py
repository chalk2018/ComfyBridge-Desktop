"""对应性体检：拿素材的真实英文描述/标签，跟提示词里写的参考说明、主体、场景、标题对一遍。

只查「能自动判定的硬矛盾」：颜色说错、室内外说反。命中的逐条人工看图确认，再看要不要改。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
cat = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
INFO = {}
for kind in ("photo", "video"):
    for it in cat[kind]:
        INFO[it["id"]] = {
            # 只用 alt / desc / slug 判颜色与场景：tags 里常混进「红唇」这类无关词，噪声太大
            "en": (it.get("alt") or "") + " " + (it.get("desc") or "") + " " + (it.get("slug") or "").replace("-", " "),
            "tags": [t.lower() for t in (it.get("tags") or [])],
            "kind": kind,
            "slug": it.get("slug", ""),
        }

COLORS = {
    "白": ["white", "cream", "ivory", "snow"],
    "黑": ["black", "dark", "shadow", "silhouette", "monochrome", "grayscale", "grey scale"],
    "蓝": ["blue", "navy", "denim", "azure", "teal", "cyan"],
    "红": ["red", "crimson", "scarlet", "maroon"],
    "绿": ["green", "olive", "emerald", "forest"],
    "黄": ["yellow", "gold", "golden", "amber"],
    "橙": ["orange"],
    "粉": ["pink", "rose", "blush"],
    "紫": ["purple", "violet", "lavender", "magenta"],
    "灰": ["gray", "grey", "silver", "monochrome"],
    "棕": ["brown", "wood", "wooden", "tan", "beige"],
}
OUT = [("户外", ["outdoor", "outside", "street", "beach", "field", "park", "forest", "mountain", "sky"]),
       ("室内", ["indoor", "inside", "studio", "room", "interior", "office"])]


def rows(prefix):
    for p in sorted((HERE / "specs").glob(prefix + "*.tsv")):
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                yield [c.strip() for c in line.split("|")]


def ids_of(role, assets):
    raw = [a.strip() for a in assets.split(",") if a.strip()]
    out = []
    for a in raw:
        if a[:2] in ("p:", "v:"):
            out.append((a[0], a[2:]))
        else:
            out.append(("p", a))
    return out


def main() -> None:
    items = {i["key"]: i for i in json.loads((HERE / "items.json").read_text(encoding="utf-8"))}
    flags = []
    specs = []
    for prefix, cat_name in (("video_", "video"), ("image_", "image")):
        for r in rows(prefix):
            specs.append((cat_name, r))
    for cat_name, r in specs:
        key, role, title, assets = r[0], r[1], r[2], r[3]
        body, scene = r[4], r[5]
        descs = [d.strip() for d in r[-1].split(";") if d.strip()]
        it = items.get(key)
        if not it:
            continue
        src = ids_of(role, assets)
        # 素材文件与源 id 的顺序对应
        pairs = []
        if cat_name == "image":
            for i, (k, sid) in enumerate(src):
                if i < len(it["files"]):
                    pairs.append((it["files"][i], sid))
        else:
            if role == "i2v":
                pairs = [(it["files"][0], src[0][1])] if src else []
            elif role == "flf":
                pairs = [(f, src[0][1]) for f in it["files"]] if src else []
            elif role == "swap":
                pairs.append((it["files"][0], src[0][1]))
                if len(src) > 1 and len(it["files"]) > 1:
                    pairs.append((it["files"][1], src[1][1]))
            elif role == "board":
                pairs.append((it["files"][0], src[0][1]))
                if len(src) > 1 and len(it["files"]) > 1:
                    pairs.append((it["files"][1], src[1][1]))
        zh_all = title + " " + body + " " + scene + " " + " ".join(descs)
        for i, (fn, sid) in enumerate(pairs):
            info = INFO.get(sid)
            if not info:
                continue
            en = info["en"].lower()
            zh = descs[i] if i < len(descs) else zh_all
            # 颜色
            en_colors = {c for c, ws in COLORS.items() if any(w in en for w in ws)}
            zh_colors = {c for c in COLORS if c in zh}
            if en_colors and zh_colors and not (en_colors & zh_colors):
                flags.append((key, it["title"], fn, "颜色",
                              f"素材={sorted(en_colors)} 文案={sorted(zh_colors)}", zh[:40], info["slug"]))
            # 室内外
            en_locs = {c for c, ws in OUT if any(w in en for w in ws)}
            zh_locs = {c for c, ws in OUT if any(w in zh for w in ws)}
            if en_locs and zh_locs and not (en_locs & zh_locs):
                flags.append((key, it["title"], fn, "室内外",
                              f"素材={sorted(en_locs)} 文案={sorted(zh_locs)}", zh[:40], info["slug"]))
            # 标题里的颜色/场景跟素材对不上（标题只跟第一张素材比）
            if i == 0:
                t_zh = title
                t_colors = {c for c in COLORS if c in t_zh}
                if en_colors and t_colors and not (en_colors & t_colors):
                    flags.append((key, it["title"], fn, "标题颜色",
                                  f"素材={sorted(en_colors)} 标题={sorted(t_colors)}", t_zh, info["slug"]))
    print("命中", len(flags))
    seen = set()
    for f in flags:
        seen.add(f[0])
        print(" | ".join(str(x) for x in f))
    print("涉及条目", len(seen))


if __name__ == "__main__":
    main()
