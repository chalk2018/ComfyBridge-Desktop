"""把 1.04 新增的工作流 / 能力写进 config.yaml（幂等，可反复跑）。

做四件事：
1. 登记 8 条新工作流（4 视频 + 4 音频）+ 画布用的两条；
2. 能力表整理：删掉三个占位图像能力与视频的两个旧占位，视频合并成「视频生成」，
   音频四个能力打开，画布挂「图片生成 / 视频生成」两个子功能；
3. 写入 videoGrades / audioGrades（已有就不覆盖，保留管理台改过的值）；
4. 版本号升到 1.04。

用法：
    .\\.venv\\Scripts\\python.exe tools\\migrate_104.py
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config.yaml"


def wf_entry(file: str, bridge: str, media: str, note: str = "") -> dict:
    return {
        "file": f"workflows/{file}.json",
        "bridge": f"workflows/{bridge}.bridge.json",
        "status": "ok",
        "media": media,
        "note": note,
        "fields": [],
        "grades": [{"name": "默认", "locked": True, "note": "参数由能力页的比例/分辨率/步数决定"}],
    }


NEW_WORKFLOWS = {
    "视频生成-文生视频-h3": wf_entry("视频生成-文生视频-h3", "视频生成-文生视频-h3", "video",
                                "H3 fl2va · 纯提示词出片"),
    "视频生成-首尾帧生视频-h3": wf_entry("视频生成-首尾帧生视频-h3", "视频生成-首尾帧生视频-h3", "video",
                                 "H3 fl2va · 首帧/尾帧两张图驱动"),
    "视频生成-参考生视频-h3": wf_entry("视频生成-参考生视频-h3", "视频生成-参考生视频-h3", "video",
                                "H3 ref2va · 图/视频/音频参考"),
    "视频生成-故事板生视频-h3": wf_entry("视频生成-故事板生视频-h3", "视频生成-故事板生视频-h3", "video",
                                 "H3 ref2va · 四/六/九宫格故事板驱动"),
    "音频-音乐生成": wf_entry("音频-音乐生成", "音频-音乐生成", "audio", "MiniMax Music 3 · 风格+歌词"),
    "音频-参考生成音乐": wf_entry("音频-参考生成音乐", "音频-参考生成音乐", "audio", "YuE2 cover · 参考音频复刻"),
    "音频-语音生成": wf_entry("音频-语音生成", "音频-语音生成", "audio", "Spark-TTS · 文字转语音"),
    "音频-声音克隆": wf_entry("音频-声音克隆", "音频-声音克隆", "audio", "Spark-TTS · 音色克隆"),
    "画布-图片生成": wf_entry("图片生成-参考生图-qwen2.1turbo", "图片生成-参考生图-qwen2.1turbo", "image",
                          "画布图片生成节点：0 张参考图走纯提示词，接了图当参考"),
    "画布-视频生成": wf_entry("视频生成-参考生视频-h3", "视频生成-参考生视频-h3", "video",
                         "画布视频生成节点：可无参考，纯提示词出片"),
}

DROP_CAPS = {"image.reference", "image.lineart", "image.sketch",
             "video.reference", "video.firstlast"}

VIDEO_CAP = {
    "id": "video.generate", "name": "视频生成", "em": "🎬", "group": "视频",
    "enabled": True, "media": "video",
    "desc": "文生视频 / 首尾帧 / 参考生视频 / 故事板生视频，四种模式共用一页",
    "maxRefs": 6,
    "requiredRef": False,
    "workflows": ["视频生成-文生视频-h3", "视频生成-首尾帧生视频-h3",
                  "视频生成-参考生视频-h3", "视频生成-故事板生视频-h3"],
}

AUDIO_CAPS = [
    {"id": "audio.music", "name": "音乐生成", "em": "🎵", "group": "音频", "enabled": True,
     "media": "audio", "desc": "按风格与情绪生成音乐，可带歌词", "maxRefs": 0,
     "workflows": ["音频-音乐生成"]},
    {"id": "audio.music.ref", "name": "参考生成音乐", "em": "🎼", "group": "音频", "enabled": True,
     "media": "audio", "desc": "上传一段参考音频，按它的旋律重做一首", "maxRefs": 1,
     "workflows": ["音频-参考生成音乐"]},
    {"id": "audio.tts", "name": "语音生成", "em": "🎙", "group": "音频", "enabled": True,
     "media": "audio", "desc": "文字转语音，可选性别/年龄/音高/语速", "maxRefs": 0,
     "workflows": ["音频-语音生成"]},
    {"id": "audio.clone", "name": "声音克隆", "em": "🎤", "group": "音频", "enabled": True,
     "media": "audio", "desc": "用一段参考音频克隆音色念新台词", "maxRefs": 1,
     "workflows": ["音频-声音克隆"]},
]

CANVAS_CAP = {
    "id": "canvas.video", "name": "视频生成画布", "em": "🎬", "group": "视频",
    "enabled": True, "kind": "canvas",
    "desc": "无限画布：图片/视频/音频节点连成一条线，引用素材直接生成",
    "maxRefs": 6,
    "workflows": ["画布-图片生成", "画布-视频生成"],
}

VIDEO_GRADES = {
    "t2v": [
        {"name": "默认", "ratio": "16:9", "res": "768P", "steps": 8, "locked": True,
         "note": "16:9 · 768P · 8 步加速 LoRA"},
        {"name": "快速", "ratio": "16:9", "res": "480P", "steps": 4, "note": "480P · 4 步，最快出片"},
        {"name": "精细", "ratio": "16:9", "res": "768P", "steps": 20, "note": "768P · 20 步原模型"},
        {"name": "竖屏", "ratio": "9:16", "res": "768P", "steps": 8, "note": "9:16 · 768P · 8 步"},
    ],
    "fl2v": [
        {"name": "默认", "ratio": "16:9", "res": "768P", "steps": 8, "locked": True,
         "note": "16:9 · 768P · 8 步 fl2v LoRA"},
        {"name": "快速", "ratio": "16:9", "res": "480P", "steps": 4, "note": "480P · 4 步 fl2v LoRA"},
        {"name": "精细", "ratio": "16:9", "res": "768P", "steps": 20, "note": "768P · 20 步原模型"},
        {"name": "竖屏", "ratio": "9:16", "res": "768P", "steps": 8, "note": "9:16 · 768P · 8 步"},
    ],
    "ref2v": [
        {"name": "默认", "ratio": "16:9", "res": "768P", "steps": 8, "locked": True,
         "note": "16:9 · 768P · 8 步 ref2v LoRA"},
        {"name": "快速", "ratio": "16:9", "res": "480P", "steps": 4, "note": "480P · 4 步 ref2v LoRA"},
        {"name": "精细", "ratio": "16:9", "res": "768P", "steps": 20, "note": "768P · 20 步原模型"},
        {"name": "竖屏", "ratio": "9:16", "res": "768P", "steps": 8, "note": "9:16 · 768P · 8 步"},
    ],
    "board": [
        {"name": "默认", "ratio": "16:9", "res": "768P", "steps": 8, "locked": True,
         "note": "16:9 · 768P · 8 步 ref2v LoRA"},
        {"name": "快速", "ratio": "16:9", "res": "480P", "steps": 4, "note": "480P · 4 步"},
        {"name": "精细", "ratio": "16:9", "res": "768P", "steps": 20, "note": "768P · 20 步"},
        {"name": "竖屏", "ratio": "9:16", "res": "768P", "steps": 8, "note": "9:16 · 768P · 8 步"},
    ],
}


def main() -> int:
    raw = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    raw["version"] = "1.04"

    wfs = raw.setdefault("workflows", {})
    for name, entry in NEW_WORKFLOWS.items():
        old = wfs.get(name) or {}
        # 保留管理台改过的档位，其余字段按新定义走
        merged = copy.deepcopy(entry)
        if old.get("grades"):
            merged["grades"] = old["grades"]
        wfs[name] = merged

    caps = raw.get("capabilities") or []
    keep = [c for c in caps if c.get("id") not in DROP_CAPS]
    ids = [c.get("id") for c in keep]
    # 视频生成放在画布前面（菜单顺序：图像 / 设定图 / 故事板 / 视频 / 音频）
    if "video.generate" in ids:
        idx = ids.index("video.generate")
        keep[idx] = copy.deepcopy(VIDEO_CAP)
    else:
        keep.insert(len(keep), copy.deepcopy(VIDEO_CAP))
    ids = [c.get("id") for c in keep]
    if "canvas.video" in ids:
        keep[ids.index("canvas.video")] = copy.deepcopy(CANVAS_CAP)
    for cap in AUDIO_CAPS:
        if cap["id"] in ids:
            keep[[c.get("id") for c in keep].index(cap["id"])] = copy.deepcopy(cap)
        else:
            keep.append(copy.deepcopy(cap))
    raw["capabilities"] = keep

    raw.setdefault("videoGrades", {})
    for mode, rows in VIDEO_GRADES.items():
        raw["videoGrades"].setdefault(mode, rows)
    raw.setdefault("audioGrades", {})
    raw.setdefault("canvasFuncs", {})
    canvas_funcs = raw["canvasFuncs"]
    canvas_funcs.setdefault("image", {"name": "图片生成", "wf": "画布-图片生成"})
    canvas_funcs.setdefault("video", {"name": "视频生成", "wf": "画布-视频生成"})

    CFG.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120),
                   encoding="utf-8")
    print(f"写回 {CFG}")
    print("能力：", [c.get("id") for c in raw["capabilities"]])
    print("新增工作流：", list(NEW_WORKFLOWS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
