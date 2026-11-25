"""视频生成（1.04）：模式、档位（比例 / 分辨率 / 步数）、提示词拼装、参考注入。

为什么单独一层：H3 这条链的参数不是「工作流里随便哪个控件」，而是三件事的组合——
画幅 + 时长 + 加速策略（4/8 步走 Turbo LoRA，20 步走原模型）。把这三件事翻成节点值
只应该有一处实现，页面档位表、画布节点、创作台提交都走这里，才不会出现
「页面写着 4 步、实际跑 20 步」这类口径不一致。

提示词方面：界面里用 [图 1] / [视频 1] / [音频 1] 这种好认的写法，
真正提交前在这里统一翻成 H3 认的 <Picture 1> / <Video 1> / <Audio 1>；
首尾帧、故事板还会自动补上「哪张是首帧」「这是第几格」的说明。
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .config import config
from .workflows import registry

# 模式清单：顺序即页面上的切换顺序，enabled=False 的暂时灰着
MODES: list[dict[str, Any]] = [
    {"id": "t2v", "name": "文生视频", "enabled": True,
     "wf": "视频生成-文生视频-h3", "desc": "只写提示词，不上传任何素材"},
    {"id": "fl2v", "name": "首尾帧生视频", "enabled": True,
     "wf": "视频生成-首尾帧生视频-h3", "desc": "给首帧和尾帧（至少一张），中间过程交给模型"},
    {"id": "ref2v", "name": "参考生视频", "enabled": True,
     "wf": "视频生成-参考生视频-h3", "desc": "用图 / 视频 / 音频作参考，配合提示词生成"},
    {"id": "board", "name": "故事板生视频", "enabled": True,
     "wf": "视频生成-故事板生视频-h3", "desc": "一张四/六/九宫格故事板，按格生成镜头"},
    {"id": "swap", "name": "角色替换", "enabled": False, "wf": "", "desc": "后续版本开放"},
    {"id": "motion", "name": "动作迁移", "enabled": False, "wf": "", "desc": "后续版本开放"},
    {"id": "white", "name": "白膜生视频", "enabled": False, "wf": "", "desc": "后续版本开放"},
    # 需求3：挡位表按用户口径显示 7 个子功能（4 可用 + 3 禁用），这里保持同一份清单。
]

MODE_IDS = {m["id"]: m for m in MODES}

# 参考槽上限：H3 节点自身最多 9 图 / 3 视频 / 3 音频，这里按实际可用性收紧
MAX_IMAGES = 6
MAX_VIDEOS = 1
MAX_AUDIOS = 2

MASTER = "默认"
DEFAULT_GRADES: list[dict[str, Any]] = [
    # 需求5：创作台视频初始只有「默认」一档（16:9 / 8 步 / 480P），
    # 快速 / 精细 / 竖屏等由用户在管理台挡位表里手动添加。
    {"name": "默认", "ratio": "16:9", "res": "480P", "steps": 8, "locked": True,
     "note": "16:9 · 480P · 8 步加速 LoRA"},
]

RATIOS = ("16:9", "9:16")
RESES = ("480P", "768P")
STEPS = (4, 8, 20)

# 尺寸表：H3 的原生短边是 768，长边封顶 1344；480P 按短边 480 等比推
SIZES: dict[tuple[str, str], tuple[int, int]] = {
    ("16:9", "768P"): (1344, 768),
    ("9:16", "768P"): (768, 1344),
    ("16:9", "480P"): (864, 480),
    ("9:16", "480P"): (480, 864),
}

BOARD_GRID_LABEL = {4: "四宫格（2×2）", 6: "六宫格（3×2）", 9: "九宫格（3×3）"}


def _cfg() -> dict[str, Any]:
    raw = config.raw.get("videoGrades")
    return raw if isinstance(raw, dict) else {}


def _clean_grade(row: dict[str, Any]) -> dict[str, Any] | None:
    """把一条档位规整成合法值；不合法的整条丢掉（不让坏配置拖垮页面）。"""
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or "").strip()[:20]
    if not name:
        return None
    ratio = str(row.get("ratio") or "16:9")
    res = str(row.get("res") or "768P")
    try:
        steps = int(row.get("steps") or 8)
    except (TypeError, ValueError):
        steps = 8
    if ratio not in RATIOS:
        ratio = "16:9"
    if res not in RESES:
        res = "768P"
    if steps not in STEPS:
        steps = min(STEPS, key=lambda s: abs(s - steps))
    return {
        "name": name, "ratio": ratio, "res": res, "steps": steps,
        "locked": bool(row.get("locked")),
        "note": str(row.get("note") or ""),
    }


def grades(mode: str) -> list[dict[str, Any]]:
    """某个模式的档位：配置里有就用配置，没有就给内置三档。默认档永远在最前。"""
    rows = [g for g in (_clean_grade(r) for r in (_cfg().get(mode) or [])) if g]
    if not rows:
        rows = copy.deepcopy(DEFAULT_GRADES)
    rows.sort(key=lambda g: 0 if g["name"] == MASTER else 1)
    rows[0]["locked"] = True
    return rows


def find_grade(mode: str, name: str) -> dict[str, Any] | None:
    return next((g for g in grades(mode) if g["name"] == name), None)


def save_grades(mode: str, rows: list[dict[str, Any]]) -> None:
    """写回视频档位（整表替换）。默认档必须存在且叫「默认」。"""
    if mode not in MODE_IDS:
        raise ValueError("没有这个视频模式")
    cleaned = [g for g in (_clean_grade(r) for r in rows) if g]
    if not cleaned:
        raise ValueError("至少要留一个档位")
    cleaned = [g for g in cleaned if g["name"] != MASTER]
    master = next((g for g in (rows or []) if str(g.get("name")) == MASTER), None)
    master_row = _clean_grade(master or DEFAULT_GRADES[0]) or copy.deepcopy(DEFAULT_GRADES[0])
    master_row["name"] = MASTER
    master_row["locked"] = True
    names = {master_row["name"]}
    for row in cleaned:
        if row["name"] in names:
            raise ValueError(f"档位名「{row['name']}」重复了")
        names.add(row["name"])
    out = [master_row] + cleaned
    raw = copy.deepcopy(config.raw)
    raw.setdefault("videoGrades", {})[mode] = out
    config.replace(raw)


def opts(mode: str, grade: str | None = None, ratio: str | None = None,
         res: str | None = None, steps: int | None = None) -> dict[str, Any]:
    """把「模式 + 档位（或三个显式参数）」翻成可直接喂工作流的出片参数。"""
    info = MODE_IDS.get(mode) or MODE_IDS["ref2v"]
    base = find_grade(mode, (grade or MASTER)) or grades(mode)[0]
    r = ratio if ratio in RATIOS else base["ratio"]
    s = res if res in RESES else base["res"]
    st = steps if steps in STEPS else base["steps"]
    width, height = SIZES[(r, s)]
    bridge = registry.bridge(info["wf"]) if info.get("wf") else {}
    spec = bridge.get("video_ref") or {}
    if st >= 20:
        lora = ""
    else:
        lora = spec.get("lora8" if st >= 8 else "lora4", "")
    return {
        "mode": mode, "modeName": info["name"], "wf": info["wf"],
        "grade": base["name"], "note": base["note"],
        "ratio": r, "res": s, "steps": st,
        "width": width, "height": height, "lora": lora,
        "size": f"{width}×{height}",
    }


def grade_row(mode: str, name: str) -> dict[str, Any] | None:
    """档位表用：档位 + 这一档算出来的实际尺寸 / 步数 / LoRA。"""
    g = find_grade(mode, name)
    if not g:
        return None
    o = opts(mode, name)
    return {**g, **{k: o[k] for k in ("width", "height", "size", "lora", "wf")}}


# ------------------------------------------------------------------ 提示词
TOKEN = re.compile(r"\[(图|视频|音频)\s*(\d+)\]")


def to_h3(text: str) -> str:
    """界面里的 [图 1] → H3 的 <Picture 1>（视频 / 音频同理）。"""
    kind_map = {"图": "Picture", "视频": "Video", "音频": "Audio"}

    def repl(match: re.Match[str]) -> str:
        return f"<{kind_map[match.group(1)]} {int(match.group(2))}>"

    return TOKEN.sub(repl, text or "")


def refs_summary(images: int, videos: int, audios: int) -> str:
    parts = []
    if images:
        parts.append(f"{images} 张图")
    if videos:
        parts.append(f"{videos} 段视频")
    if audios:
        parts.append(f"{audios} 段音频")
    return "、".join(parts) or "无参考素材"


def compose(mode: str, text: str, *, images: int = 0, videos: int = 0,
            audios: int = 0, board: dict[str, Any] | None = None) -> str:
    """把界面输入拼成 H3 认的整条提示词。

    - 首尾帧：自动声明哪张是首帧 / 尾帧，用户不用自己在文字里说明；
    - 故事板：先声明这是几宫格、阅读顺序，再把每一格的画面接上；
      故事板允许不写文字（只看宫格），但至少要有一格内容或一句提示。
    - 参考生视频：素材靠 <Picture N> / <Video N> / <Audio N> 引用，原样保留。
    """
    body = to_h3(text).strip()
    head: list[str] = []
    if mode == "fl2v":
        if images >= 1:
            head.append("<Picture 1> 是本片的首帧（视频从这里开始）。")
        if images >= 2:
            head.append("<Picture 2> 是本片的尾帧（视频在这里结束）。")
        if images == 1:
            head.append("尾帧不做限制，按提示词自然发展。")
    elif mode == "board":
        grid = int((board or {}).get("grid") or 4)
        label = BOARD_GRID_LABEL.get(grid, BOARD_GRID_LABEL[4])
        cells = [str(c or "").strip() for c in ((board or {}).get("cells") or [])]
        head.append(f"<Picture 1> 是一张{label}分镜故事板，按从左到右、从上到下的顺序依次是 "
                    f"{grid} 个连续镜头；请把它当成这 {grid} 个镜头的画面依据，"
                    "人物、服装、场景、光线在镜头之间保持一致。")
        filled = [(i + 1, c) for i, c in enumerate(cells) if c]
        if filled:
            lines = [f"第 {i} 格：{c}" for i, c in filled]
            head.append("各格内容：" + "；".join(lines) + "。")
        head.append("成片是一条连贯的短视频，镜头之间自然衔接，不要出现分格边框或文字。")
    elif mode == "t2v" and body:
        head.append("生成一段带环境声与配乐的短视频。")

    # 实测 H3 会把台词「烧」进画面当字幕：明确要求不出字，比事后裁掉省事
    if body or head:
        head.append("画面里不要出现字幕、水印、台标或任何文字。")
    joined = "\n".join([h for h in head if h] + ([body] if body else []))
    return joined.strip()


def brief(mode: str, text: str, images: int = 0, videos: int = 0,
          audios: int = 0, board: dict[str, Any] | None = None) -> str:
    name = (MODE_IDS.get(mode) or {}).get("name", "视频生成")
    tail = refs_summary(images, videos, audios)
    if mode == "board":
        grid = int((board or {}).get("grid") or 4)
        return f"{name} · {BOARD_GRID_LABEL.get(grid, '')} · {tail} · {(text or '').strip()[:40]}"
    return f"{name} · {tail} · {(text or '').strip()[:60]}"


# ------------------------------------------------------------------ 提交
def build(api: dict[str, Any], spec: dict[str, Any], *, text: str, opt: dict[str, Any],
          refs: list[dict[str, Any]], seed: int, prefix: str) -> dict[str, Any]:
    """把提示词 / 尺寸 / 步数 / 参考素材注入一条视频工作流。

    Args:
        api: registry.load() 给出的母版（会被就地改）。
        spec: bridge 里的 video_ref 注入点。
        refs: [{"kind": "image"/"video"/"audio", "comfyName": ...}]，按用户上传顺序。
    """
    node_id = spec.get("node", "10")
    node = api.setdefault(node_id, {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}})
    node["inputs"]["prompt"] = [spec.get("prompt", "11"), 0]
    node["inputs"]["width"] = int(opt["width"])
    node["inputs"]["height"] = int(opt["height"])
    node["inputs"]["length"] = int(opt.get("length") or 124)
    node["inputs"]["ref_image_size"] = "match"

    # 加速策略：4 步挂 4 步 LoRA、8 步挂 8 步 LoRA、20 步走原模型
    steps = int(opt.get("steps") or 8)
    use_lora = steps < 20
    api[spec.get("switch", "15")]["inputs"]["value"] = use_lora
    api[spec.get("steps", "17")]["inputs"]["value"] = steps if use_lora else 4
    api[spec.get("base_steps", "16")]["inputs"]["value"] = 20
    if spec.get("use8"):
        api[spec["use8"]]["inputs"]["value"] = steps >= 8

    api[spec.get("prompt", "11")]["inputs"]["value"] = text
    for target in ("31",):
        if target in api and "filename_prefix" in api[target]["inputs"]:
            api[target]["inputs"]["filename_prefix"] = prefix
    if "22" in api:
        api["22"]["inputs"]["noise_seed"] = seed

    # 参考素材：先清空母版里的占位槽，再按顺序重建，避免残留老文件名
    for key in list(node["inputs"]):
        if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.")):
            node["inputs"].pop(key, None)
    for nid in [k for k, v in api.items()
                if v.get("class_type") in ("LoadImage", "LoadVideo", "GetVideoComponents", "LoadAudio")]:
        api.pop(nid, None)

    images = [r for r in refs if r.get("kind", "image") == "image"]
    videos = [r for r in refs if r.get("kind") == "video"]
    audios = [r for r in refs if r.get("kind") == "audio"]
    for index, ref in enumerate(images[:MAX_IMAGES]):
        nid = f"in_img_{index}"
        api[nid] = {"class_type": "LoadImage", "inputs": {"image": ref["comfyName"]}}
        node["inputs"][f"ref_images.ref_image_{index}"] = [nid, 0]
    for index, ref in enumerate(videos[:MAX_VIDEOS]):
        nid = f"in_vid_{index}"
        api[nid] = {"class_type": "LoadVideo", "inputs": {"file": ref["comfyName"]}}
        cid = f"in_vidc_{index}"
        api[cid] = {"class_type": "GetVideoComponents", "inputs": {"video": [nid, 0]}}
        node["inputs"][f"ref_videos.ref_video_{index}"] = [cid, 0]
        node["inputs"][f"ref_video_audios.ref_video_audio_{index}"] = [cid, 1]
    for index, ref in enumerate(audios[:MAX_AUDIOS]):
        nid = f"in_aud_{index}"
        api[nid] = {"class_type": "LoadAudio", "inputs": {"audio": ref["comfyName"]}}
        node["inputs"][f"ref_audios.ref_audio_{index}"] = [nid, 0]
    return api


def state_payload() -> dict[str, Any]:
    """创作台 / 画布 / 管理台共用的视频元数据。"""
    out = []
    for mode in MODES:
        row = dict(mode)
        row["grades"] = grades(mode["id"]) if mode.get("wf") else []
        out.append(row)
    return {
        "modes": out,
        "master": MASTER,
        "ratios": list(RATIOS), "reses": list(RESES), "steps": list(STEPS),
        "sizes": {f"{r}|{s}": {"w": w, "h": h} for (r, s), (w, h) in SIZES.items()},
        "maxImages": MAX_IMAGES, "maxVideos": MAX_VIDEOS, "maxAudios": MAX_AUDIOS,
    }
