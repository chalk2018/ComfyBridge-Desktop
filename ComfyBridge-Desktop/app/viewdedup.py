"""道具多视图去重。

模型对「只画 N 个不同视角」的服从度很差：只要几个方位看起来差不多，它就把同一张图
复制成 2~3 份填满画布。提示词层面已经压过好几轮，仍会在对称道具上复发，所以在
出图之后做一次视觉层面的兜底：

1. 用行/列投影把画面切成若干个实际画出来的视图块；
2. 两两比对（按内容裁齐、保持长宽比归一后比灰度均值差），几乎一样的判为重复；
3. 去掉重复的，把剩下的块按原尺寸居中重新排版回原来的画布。

只处理「道具设定图 · 多视图」，其余能力一律不动。

阈值说明：这里只负责「同一张图被复制了几份」这种硬重复（同样构图差几十个灰度级都算），
「圆形道具三个视角本来就长一个样」这类语义层面的重复由 compose 阶段决定画几个，
不靠像素判断——实测像素差无法把「王座正面 vs 背面」（约 18）和「圆香炉三视图」
（约 20）分开。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

WHITE = 244          # 亮度高于它的算背景
DUP_DIFF = 23.0      # 归一化后灰度平均差低于它就算同一张图（完全复制约 2~7，同角度换了位置/大小约 12~22）
SAMPLE = 72          # 比对尺寸；保持长宽比，别把侧视图拉成正面图的形状


def _mask_rows(im: Image.Image, width: int = 384) -> tuple[Image.Image, float]:
    """缩到小图并做「有内容」二值掩码，返回 (掩码图, 缩放比)。"""
    small = im.convert("L")
    ratio = width / small.width
    small = small.resize((width, max(1, int(small.height * ratio))), Image.BOX)
    return small.point(lambda v: 255 if v < WHITE else 0), ratio


def _spans(flags: list[bool], min_len: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, f in enumerate(flags):
        if f:
            if start is None:
                start = i
        elif start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(flags) - start >= min_len:
        out.append((start, len(flags)))
    return out


def find_views(im: Image.Image) -> list[tuple[int, int, int, int]]:
    """返回各视图块的外接框 (l, t, r, b)，按「先上后下、先左后右」排序。

    先看哪些行/列上有内容：一行只要有一个暗像素就算有内容，所以伞面与伞柄这种
    中间用细杆连着的不会被拆成两块；而上下两排之间的纯白横带会把画面切成两个行带。
    """
    mask, ratio = _mask_rows(im)
    w, h = mask.size
    px = mask.load()
    rows = [any(px[x, y] for x in range(w)) for y in range(h)]
    boxes: list[tuple[int, int, int, int]] = []
    for t, b in _spans(rows, max(2, int(8 * ratio))):
        cols = [any(px[x, y] for y in range(t, b)) for x in range(w)]
        for l, r in _spans(cols, max(2, int(10 * ratio))):
            boxes.append((int(l / ratio), int(t / ratio), int(r / ratio), int(b / ratio)))
    return boxes


def _signature(im: Image.Image) -> list[int]:
    """归一化签名：按内容裁齐 → 保持长宽比缩到 SAMPLE 方图。

    不能直接拉伸成方图：那样「很窄的侧视」和「正面」会被拉成同样形状，差异被抹平。
    """
    g = im.convert("L")
    box = g.point(lambda v: 255 if v < WHITE else 0).getbbox()
    if box:
        g = g.crop(box)
    w, h = g.size
    k = min(SAMPLE / max(1, w), SAMPLE / max(1, h))
    nw, nh = max(1, int(w * k)), max(1, int(h * k))
    canvas = Image.new("L", (SAMPLE, SAMPLE), 255)
    canvas.paste(g.resize((nw, nh), Image.BOX), ((SAMPLE - nw) // 2, (SAMPLE - nh) // 2))
    return [int(v) for v in canvas.getdata()]


def _diff(a: list[int], b: list[int]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


def _is_blank(sig: list[int]) -> bool:
    """纯白块（模型留的空格）不参与比对，也不该保留。"""
    return min(sig) > 240 and (max(sig) - min(sig)) < 6


def _flatten(blk: Image.Image) -> Image.Image:
    """把近白的背景压成纯白：块放大后原图那点灰底会变成一块明显的浅灰补丁。"""
    return blk.convert("RGB").point(lambda v: 255 if v >= 238 else v)


def _form(blk: Image.Image, size: int = 48) -> dict:
    """量这一块的「剪影形态」：宽高比、外接框填充率、二值掩码。

    用来认「圆的、没有正反面的东西」——球、圆罐、圆盘。不管转到哪一面，
    它在画面里都是同一个近圆剪影：宽高比≈1、填充率≈π/4。椅子（0.4）、
    书案（0.85）、微波炉（0.83）、桥和船（宽高比不对）都够不着这个带。
    """
    g = blk.convert("L")
    m = g.point(lambda v: 255 if v < WHITE else 0)
    box = m.getbbox()
    if not box:
        return {"asp": 0.0, "fill": 0.0, "sig": [0] * (size * size)}
    m = m.crop(box)
    w, h = m.size
    px = list(m.getdata())
    area = sum(1 for v in px if v > 96)
    small = m.resize((size, size), Image.BOX)
    return {"asp": w / max(1, h), "fill": area / max(1, w * h),
            "sig": [1 if v > 96 else 0 for v in small.getdata()]}


def _iou(a: list[int], b: list[int]) -> float:
    inter = sum(1 for x, y in zip(a, b) if x and y)
    union = sum(1 for x, y in zip(a, b) if x or y)
    return inter / union if union else 1.0


ROUND_ASP = (0.85, 1.18)     # 近圆剪影的宽高比带
ROUND_FILL = (0.58, 0.80)    # π/4≈0.785 落在里面；实心方块约 1.0，椅子约 0.4
ROUND_IOU = 0.90             # 两个剪影的重合度


def _same_round(a: dict, b: dict) -> bool:
    """两块是不是「同一个近圆形态」。纯几何判断，不含任何道具名词。"""
    if not (ROUND_ASP[0] <= a["asp"] <= ROUND_ASP[1]
            and ROUND_ASP[0] <= b["asp"] <= ROUND_ASP[1]):
        return False
    if not (ROUND_FILL[0] <= a["fill"] <= ROUND_FILL[1]
            and ROUND_FILL[0] <= b["fill"] <= ROUND_FILL[1]):
        return False
    return _iou(a["sig"], b["sig"]) >= ROUND_IOU


def _reflow(blocks: list[Image.Image], size: tuple[int, int]) -> Image.Image:
    """把保留下来的块重新排版到 size 大小的纯白画布上：不改尺寸，整组居中。"""
    W, H = size
    canvas = Image.new("RGB", (W, H), "white")
    k = len(blocks)
    if k == 0:
        return canvas
    per_row = k if k <= 3 else (k + 1) // 2
    rows = 1 if k <= 3 else 2
    cell_w = W / per_row
    cell_h = H / rows
    pad = 0.07
    # 各视角的块大小常常不一样（模型把背面画小了），统一到同一高度看起来才是一套图纸。
    # 高度按整格算，再用 up_cap 压住放大倍数：只剩一两块时留白太大，允许放大一些。
    target_h = cell_h * (1 - pad * 2)
    up_cap = 1.8 if k == 1 else (1.5 if k == 2 else 1.3)
    for i, blk in enumerate(blocks):
        r, c = divmod(i, per_row)
        bw, bh = blk.size
        scale = min(target_h / max(1, bh), cell_w * (1 - pad * 2) / bw, up_cap)
        nw, nh = max(1, int(bw * scale)), max(1, int(bh * scale))
        small = _flatten(blk.resize((nw, nh), Image.LANCZOS))
        x = int(c * cell_w + (cell_w - nw) / 2)
        # 底边对齐：高度统一之后一组道具落在同一条水平线上，才像一张图纸
        y = int(r * cell_h + cell_h - (cell_h - nh) / 2 - nh)
        canvas.paste(small, (x, y))
    return canvas


def dedup_views(path: Path, auto: bool = True) -> dict:
    """就地处理一张道具多视图。返回处理摘要，失败时原图不动。

    auto：这批视角是不是系统默认给的三个。用户自己列了视角清单时传 False，
    只做「同一张图被复制几份」这种硬重复的清理，绝不动用户点名的那几个视角。
    """
    try:
        im = Image.open(path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"打不开图：{exc}"}
    boxes = find_views(im)
    if len(boxes) < 2:
        return {"ok": True, "changed": False, "found": len(boxes), "kept": len(boxes)}
    if len(boxes) > 8:
        # 切出十来个碎块，说明这张图不是「几个视图」，而是模型把参考图整张抄了
        # （连格子里的文字都画上了）。这种情况重排只会更糟，原图不动。
        return {"ok": True, "changed": False, "found": len(boxes), "kept": len(boxes),
                "skip": "碎块过多"}

    blocks, sigs, forms = [], [], []
    for box in boxes:
        blk = im.crop(box)
        sig = _signature(blk)
        if _is_blank(sig):
            continue
        blocks.append(blk)
        sigs.append(sig)
        forms.append(_form(blk) if auto else None)

    kept, kept_sigs, kept_forms = [], [], []
    dropped = 0
    for blk, sig, form in zip(blocks, sigs, forms):
        dup = any(_diff(sig, s) < DUP_DIFF for s in kept_sigs)
        # 灰度比不出来、但剪影是同一个近圆的，也合并：足球三个视角拼块排布不同，
        # 灰度差能到 55~70，只有剪影能认出它们是同一个球。
        if not dup and auto and form is not None:
            dup = any(_same_round(form, f) for f in kept_forms if f)
        if dup:
            dropped += 1
            continue
        kept.append(blk)
        kept_sigs.append(sig)
        if form is not None:
            kept_forms.append(form)

    if dropped == 0 or not kept:
        return {"ok": True, "changed": False, "found": len(blocks), "kept": len(kept)}

    out = _reflow(kept, im.size)
    tmp = path.with_suffix(".tmp.png")
    out.save(tmp)
    tmp.replace(path)
    return {"ok": True, "changed": True, "found": len(blocks), "kept": len(kept), "dropped": dropped}
