"""往返一致性检查：把现有 md 解析再原样写回，必须逐字节不变。

做法：把 data/prompt_templates 拷到临时目录，BASE_DIR 指过去，跑一遍
_save_category(cat, _load_category(cat))，然后把每个文件与原始文件做字节比较。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from app import prompttemplates as pt  # noqa: E402

SRC = APP / "data" / "prompt_templates"
TMP = APP / "_dev" / "_pt_roundtrip_tmp"

shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True)
for f in SRC.glob("*.md"):
    shutil.copy2(f, TMP / f.name)

pt.BASE_DIR = TMP

counts = {}
for cat in pt.CATEGORIES:
    items = pt._load_category(cat)
    counts[cat] = len(items)
    pt._save_category(cat, items)
print("counts:", counts)

ok = True
for f in sorted(SRC.glob("*.md")):
    a = f.read_bytes()
    b = (TMP / f.name).read_bytes()
    same = a == b
    ok = ok and same
    print(f"{f.name}: {'一致' if same else '不一致'} ({len(a)} vs {len(b)} bytes)")

# 含参文件是新增的，写出来不该影响老文件
for name in ("图片-含参.md", "视频-含参.md", "音频-含参.md"):
    p = TMP / name
    print(f"{name}: {'已生成' if p.exists() else '未生成'} ({p.stat().st_size if p.exists() else 0} bytes)")

# 带素材的记录能否原样解析回来
fake = {"id": "tpl_test", "title": "含参往返", "content": "正文\n第二行",
        "category": "image", "tags": ["tag_role"], "media": [
            {"id": "m_1", "kind": "image", "name": "a.png", "file": "ptpl_abcdef123456.png", "size": 12},
            {"id": "m_2", "kind": "video", "name": "b.mp4", "file": "ptpl_abcdef123457.mp4", "size": 34,
             "duration": 4.5},
        ], "created": "2026-11-23 00:00:00", "updated": "2026-11-23 00:00:00"}
items = pt._load_category("image")
items.append(fake)
pt._save_category("image", items)
back = [t for t in pt._load_category("image") if t["id"] == "tpl_test"]
print("含参记录往返:", "一致" if back and back[0] == fake else f"不一致 -> {back}")
print("老文件是否被含参记录污染:", "否" if pt._parse_templates(
    (TMP / "图片.md").read_text(encoding="utf-8"), "image").__len__() == counts["image"] else "是")
print("含参文件条数:", len(pt._parse_templates(
    (TMP / "图片-含参.md").read_text(encoding="utf-8"), "image")))
print("ALL_BYTES_SAME" if ok else "BYTES_CHANGED")
