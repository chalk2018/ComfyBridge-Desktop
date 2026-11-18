"""去重阈值标定：在已标注的样本上比较几种相似度算法。

标注（人工看过图）：
  censer  0-1 / 0-2 / 1-2  → 全部重复（同一圆香炉，仅微转）
  desk    0-2 重复；0-1、1-2 不同
  lowdesk 1-2 重复；0-1、0-2 不同
  litchi  全部重复
  throne  全部不同（正 / 极窄侧 / 背）
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.viewdedup import find_views  # noqa: E402

CASES = {
    "censer": (Path(r"D:\ComfyUI-0.37.1\ComfyUI\output\ComfyBridgeOutput\道具设定图"
                    r"\ComfyBridge_job_4155_4157_00001.png"),
               {(0, 1): 1, (0, 2): 1, (1, 2): 1}),
    "desk": (ROOT / "_dev/prompt_lab/out/b02_desk__LAB_b02_desk_00001.png",
             {(0, 1): 0, (0, 2): 1, (1, 2): 0}),
    "lowdesk": (ROOT / "_dev/prompt_lab/out/d04_brush_v2__LAB_d04_brush_v2_00001.png",
                {(0, 1): 0, (0, 2): 0, (1, 2): 1}),
    "litchi": (ROOT / "_dev/prompt_lab/out/b01_litchi__LAB_b01_litchi_00001.png",
               {(0, 1): 1, (0, 2): 1, (1, 2): 1}),
    "throne": (ROOT / "_dev/prompt_lab/out/b03_throne__LAB_b03_throne_00001.png",
               {(0, 1): 0, (0, 2): 0, (1, 2): 0}),
}


def content_sig(im: Image.Image, size: int = 64) -> list[int]:
    g = im.convert("L")
    mask = g.point(lambda v: 255 if v < 246 else 0)
    box = mask.getbbox()
    if box:
        g = g.crop(box)
    # 保持长宽比缩到 size×size 的白底方图：只差一点透视时不至于整张错位，
    # 而真正的侧视（很窄）会被压成中间一条窄带，差异自然拉大
    w, h = g.size
    k = min(size / max(1, w), size / max(1, h))
    nw, nh = max(1, int(w * k)), max(1, int(h * k))
    canvas = Image.new("L", (size, size), 255)
    canvas.paste(g.resize((nw, nh), Image.BOX), ((size - nw) // 2, (size - nh) // 2))
    return [int(v) for v in canvas.getdata()]


def mean_abs(a, b) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


def diff_plain(a: Image.Image, b: Image.Image) -> float:
    return mean_abs(content_sig(a), content_sig(b))


def diff_rot(a: Image.Image, b: Image.Image, angles=(-9, -6, -3, 0, 3, 6, 9)) -> float:
    sa = content_sig(a)
    best = 1e9
    for ang in angles:
        rb = b.rotate(ang, resample=Image.BILINEAR, fillcolor=(255, 255, 255))
        best = min(best, mean_abs(sa, content_sig(rb)))
    return best


def main() -> int:
    print(f"{'case':9} {'pair':7} {'label':6} {'plain':>7} {'rot':>7}")
    for name, (path, labels) in CASES.items():
        if not path.exists():
            print(f"{name}: 缺文件 {path}")
            continue
        im = Image.open(path).convert("RGB")
        boxes = find_views(im)
        blocks = [im.crop(b) for b in boxes]
        for (i, j), lab in labels.items():
            if j >= len(blocks):
                continue
            print(f"{name:9} {i}-{j}     {'DUP' if lab else 'diff':6} "
                  f"{diff_plain(blocks[i], blocks[j]):7.1f} "
                  f"{diff_rot(blocks[i], blocks[j]):7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
