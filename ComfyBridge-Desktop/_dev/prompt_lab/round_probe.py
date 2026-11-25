# -*- coding: utf-8 -*-
"""量「这一块在画面上是不是一个近圆形」。纯几何，不含任何道具名。

思路：真正的球/圆罐，不管转到哪一面，画面里的剪影都是同一个近似圆盘——
宽高比接近 1、外接框填充率接近 π/4≈0.785、轮廓两两高度重合。
椅子、柜子、船、帘子的剪影填充率与宽高比都差得远，不会被误判。
"""
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image  # noqa: E402

from app.viewdedup import find_views  # noqa: E402

OUT = Path(r"D:\ComfyUI-0.37.1\ComfyUI\output\ComfyBridgeOutput\道具设定图")

CASES = {
    "足球": "ComfyBridge_job_4482_4484_00001",
    "弹珠": "ComfyBridge_job_4473_4475_00001",
    "台灯": "ComfyBridge_job_4479_4481_00001",
    "微波炉": "ComfyBridge_job_4476_4478_00001",
    "太师椅": "ComfyBridge_job_4488_4490_00001",
    "方鼎": "ComfyBridge_job_4491_4493_00001",
    "龙椅": "ComfyBridge_job_4203_4205_00001",
    "石拱桥": "ComfyBridge_job_4207_4209_00001",
    "乌篷船": "ComfyBridge_job_4191_4193_00001",
    "书案": "ComfyBridge_job_4183_4185_00001",
    "竹帘": "ComfyBridge_job_4271_4273_00001",
}


def stats(im: Image.Image, size: int = 48):
    g = im.convert("L")
    m = g.point(lambda v: 255 if v < 246 else 0)
    box = m.getbbox()
    if not box:
        return None
    m = m.crop(box)
    w, h = m.size
    pix = list(m.getdata())
    area = sum(1 for v in pix if v > 96)
    fill = area / max(1, w * h)
    small = m.resize((size, size), Image.BOX)
    sig = [1 if v > 96 else 0 for v in small.getdata()]
    return {"asp": w / max(1, h), "fill": fill, "sig": sig}


def iou(a, b):
    inter = sum(1 for x, y in zip(a, b) if x and y)
    union = sum(1 for x, y in zip(a, b) if x or y)
    return inter / union if union else 1.0


def main() -> None:
    for name, f in CASES.items():
        p = OUT / f"{f}.png"
        if not p.exists():
            print(name, "缺文件")
            continue
        im = Image.open(p).convert("RGB")
        st = [stats(im.crop(b)) for b in find_views(im)]
        st = [x for x in st if x]
        head = "  ".join(f"[{i}] asp={x['asp']:.2f} fill={x['fill']:.2f}"
                         for i, x in enumerate(st))
        pairs = " | ".join(f"{i}-{j} IoU={iou(st[i]['sig'], st[j]['sig']):.2f}"
                           for i, j in combinations(range(len(st)), 2))
        print(f"{name:<5} {head}")
        print(f"      {pairs}")


if __name__ == "__main__":
    main()
