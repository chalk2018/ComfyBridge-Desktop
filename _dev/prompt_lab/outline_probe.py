# -*- coding: utf-8 -*-
"""量一下「外形轮廓」能否区分「同一个东西的不同朝向」和「真的不同视角」。

足球三个方向必然一样，但拼块位置不同 → 灰度签名判为不同（实测 kept=3）。
如果轮廓掩码的 IoU 对球接近 1.0、对椅子/书案/船明显低，就可以用轮廓做第二判据。
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
    "足球": "ComfyBridge_job_4470_4472_00001",
    "弹珠": "ComfyBridge_job_4473_4475_00001",
    "台灯": "ComfyBridge_job_4479_4481_00001",
    "微波炉": "ComfyBridge_job_4476_4478_00001",
    "龙椅": "ComfyBridge_job_4203_4205_00001",
    "石拱桥": "ComfyBridge_job_4207_4209_00001",
    "乌篷船": "ComfyBridge_job_4191_4193_00001",
    "书案": "ComfyBridge_job_4183_4185_00001",
    "香炉": "ComfyBridge_job_4175_4177_00001",
    "竹帘": "ComfyBridge_job_4271_4273_00001",
}


def mask(im: Image.Image, size: int = 48) -> tuple[list[int], float, float]:
    g = im.convert("L")
    m = g.point(lambda v: 255 if v < 246 else 0)
    box = m.getbbox()
    if not box:
        return [0] * (size * size), 0.0, 0.0
    m = m.crop(box)
    w, h = m.size
    small = m.resize((size, size), Image.BOX)
    return [1 if v > 96 else 0 for v in small.getdata()], w / max(1, h), sum(
        1 for v in m.getdata() if v > 96) / max(1, w * h)


def iou(a: list[int], b: list[int]) -> float:
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
        boxes = find_views(im)
        ms = [mask(im.crop(b)) for b in boxes]
        rows = []
        for i, j in combinations(range(len(ms)), 2):
            rows.append(f"{i}-{j} IoU={iou(ms[i][0], ms[j][0]):.2f}"
                        f" 宽高比={ms[i][1]:.2f}/{ms[j][1]:.2f}")
        print(f"{name:<5} 块{len(ms)}  " + " | ".join(rows))


if __name__ == "__main__":
    main()
