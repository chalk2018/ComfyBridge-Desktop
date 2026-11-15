"""把真实界面截图压成文档内嵌用的尺寸。

CDP 截图是 devicePixelRatio=2/3 的原始像素（桌面 2880×2120），直接塞进 HTML 会让
文档膨胀到十几 MB。这里统一缩到「逻辑宽度 ×2 的一半」，再转 JPEG q86 —— UI 截图
是大片纯色，JPEG 在这个质量下肉眼看不出损失，体积却能掉到 1/6。

用法：.venv/Scripts/python.exe tools/prep_doc_images.py
输出：_reference/docs/web/*.jpg
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "_reference" / "docs"
OUT = SRC / "web"

# 桌面图按 1440 宽（逻辑宽），手机图按 585 宽（390 × 1.5），都是文档里能看清又不糊的档
DESKTOP_W = 1440
MOBILE_W = 585


def targets() -> dict[str, int]:
    """文件名 → 目标宽度。手机图以 m_ 开头，其余按桌面算。"""
    out: dict[str, int] = {}
    for p in SRC.glob("*.png"):
        out[p.name] = MOBILE_W if p.name.startswith("m_") else DESKTOP_W
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, width in sorted(targets().items()):
        src = SRC / name
        im = Image.open(src).convert("RGB")
        if im.width > width:
            h = round(im.height * width / im.width)
            im = im.resize((width, h), Image.LANCZOS)
        dst = OUT / (src.stem + ".jpg")
        im.save(dst, "JPEG", quality=86, optimize=True, progressive=True)
        total += dst.stat().st_size
        print(f"{dst.name:34s} {im.width:5d}x{im.height:<5d} {dst.stat().st_size//1024:5d} KB")
    print(f"\n合计 {total/1024/1024:.2f} MB / {len(targets())} 张 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
