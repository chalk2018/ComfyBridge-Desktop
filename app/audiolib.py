"""音频生成（1.04）：音乐、参考生成音乐、语音、声音克隆。

四条链的「参数」性质差别很大：
- 音乐 / 复刻 只需要「风格一句话 + 词 + 时长」，其余交给模型；
- 语音 / 克隆 的参数是「性别、年龄、音高、语速、温度」，这些才是页面上该给的档位。

所以档位统一走 `audioGrades.<mode>`（管理台可改），每档就是一组参数覆盖；
页面上的「时长」「歌词」是每张任务自己的输入，不占档位。
"""

from __future__ import annotations

import copy
from typing import Any

from .config import config
from .workflows import registry

MODES: list[dict[str, Any]] = [
    {"id": "music", "name": "音乐生成", "em": "🎵", "wf": "音频-音乐生成",
     "needsAudio": False, "hasLyrics": True, "hasDuration": True, "enabled": True,
     "desc": "一句话风格 + 歌词，生成带人声的成品音乐"},
    {"id": "cover", "name": "参考生成音乐", "em": "🎼", "wf": "音频-参考生成音乐",
     "needsAudio": True, "hasLyrics": True, "hasDuration": True, "enabled": True,
     "desc": "上传一段参考音频，按它的旋律线重做一首"},
    {"id": "tts", "name": "语音生成", "em": "🎙", "wf": "音频-语音生成",
     "needsAudio": False, "hasLyrics": False, "hasDuration": False, "enabled": True,
     "desc": "文字转语音，可选性别、年龄、音高、语速"},
    {"id": "clone", "name": "声音克隆", "em": "🎤", "wf": "音频-声音克隆",
     "needsAudio": True, "hasLyrics": False, "hasDuration": False, "enabled": True,
     "desc": "给一段参考音频，用它的音色念新台词"},
]

MODE_IDS = {m["id"]: m for m in MODES}
MASTER = "默认"
DURATIONS = (10, 15, 24, 30, 45, 60, 90, 120)

DEFAULT_GRADES: dict[str, list[dict[str, Any]]] = {
    "music": [
        {"name": "默认", "locked": True, "duration": 30,
         "note": "30 秒左右，模型按歌词长度自行收尾", "params": {}},
        {"name": "短句 15 秒", "duration": 15, "note": "15 秒，适合片头片尾", "params": {}},
        {"name": "完整 60 秒", "duration": 60, "note": "60 秒，段落更完整", "params": {}},
    ],
    "cover": [
        {"name": "默认", "locked": True, "duration": 30, "note": "30 秒复刻", "params": {}},
        {"name": "短句 15 秒", "duration": 15, "note": "15 秒复刻", "params": {}},
    ],
    "tts": [
        {"name": "默认", "locked": True, "note": "青年女声，中等音高与语速",
         "params": {"1": {"gender": "female", "age": "Youth-Adult",
                          "pitch": "moderate", "speed": "moderate",
                          "temperature": 0.8}}},
        {"name": "青年男声", "note": "青年男声，语速稍快",
         "params": {"1": {"gender": "male", "age": "Youth-Adult",
                          "pitch": "moderate", "speed": "fast", "temperature": 0.8}}},
        {"name": "沉稳老者", "note": "老年男声，低音高、慢语速",
         "params": {"1": {"gender": "male", "age": "Senior",
                          "pitch": "low", "speed": "slow", "temperature": 0.7}}},
        {"name": "柔和少女", "note": "少年女声，高音高",
         "params": {"1": {"gender": "female", "age": "Child",
                          "pitch": "high", "speed": "moderate", "temperature": 0.9}}},
    ],
    "clone": [
        {"name": "默认", "locked": True, "note": "按参考音频的音色原样克隆", "params": {}},
        {"name": "更稳一点", "note": "降低采样随机性，语气更平",
         "params": {"2": {"temperature": 0.6}}},
    ],
}

# 语音参数：档位里能改的字段 → 页面上的选项
VOICE_FIELDS = {
    "gender": ["female", "male"],
    "age": ["Child", "Youth-Adult", "Senior"],
    "pitch": ["low", "moderate", "high"],
    "speed": ["slow", "moderate", "fast"],
}


def _cfg() -> dict[str, Any]:
    raw = config.raw.get("audioGrades")
    return raw if isinstance(raw, dict) else {}


def _clean(row: dict[str, Any], mode: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or "").strip()[:20]
    if not name:
        return None
    out: dict[str, Any] = {"name": name, "note": str(row.get("note") or ""),
                           "locked": bool(row.get("locked")), "params": row.get("params") or {}}
    if (MODE_IDS.get(mode) or {}).get("hasDuration"):
        try:
            duration = float(row.get("duration") or 30)
        except (TypeError, ValueError):
            duration = 30.0
        out["duration"] = max(5.0, min(180.0, duration))
    return out


def grades(mode: str) -> list[dict[str, Any]]:
    rows = [g for g in (_clean(r, mode) for r in (_cfg().get(mode) or [])) if g]
    if not rows:
        rows = copy.deepcopy(DEFAULT_GRADES.get(mode) or DEFAULT_GRADES["tts"])
    rows.sort(key=lambda g: 0 if g["name"] == MASTER else 1)
    rows[0]["locked"] = True
    return rows


def find_grade(mode: str, name: str) -> dict[str, Any] | None:
    return next((g for g in grades(mode) if g["name"] == name), None)


def save_grades(mode: str, rows: list[dict[str, Any]]) -> None:
    if mode not in MODE_IDS:
        raise ValueError("没有这个音频功能")
    cleaned = [g for g in (_clean(r, mode) for r in rows) if g]
    if not cleaned:
        raise ValueError("至少要留一个档位")
    master = next((g for g in cleaned if g["name"] == MASTER), None)
    if master is None:
        master = next((g for g in (copy.deepcopy(DEFAULT_GRADES.get(mode) or [])) if g["name"] == MASTER),
                      {"name": MASTER})
    master["name"], master["locked"] = MASTER, True
    rest = [g for g in cleaned if g["name"] != MASTER]
    names = {MASTER}
    for row in rest:
        if row["name"] in names:
            raise ValueError(f"档位名「{row['name']}」重复了")
        names.add(row["name"])
    raw = copy.deepcopy(config.raw)
    raw.setdefault("audioGrades", {})[mode] = [master] + rest
    config.replace(raw)


def opts(mode: str, grade: str | None = None) -> dict[str, Any]:
    """模式 + 档位 → 出片参数（时长、参数覆盖、工作流）。"""
    info = MODE_IDS.get(mode) or MODE_IDS["music"]
    row = find_grade(mode, grade or MASTER) or grades(mode)[0]
    return {
        "mode": mode, "modeName": info["name"], "wf": info["wf"], "grade": row["name"],
        "note": row["note"], "duration": row.get("duration"), "params": row.get("params") or {},
        "needsAudio": info["needsAudio"],
    }


def compose(mode: str, text: str) -> str:
    """把界面输入整理成提示词。音频这边不做格式转换，只补必要的收尾说明。"""
    body = (text or "").strip()
    if mode == "music" and body:
        return f"{body}。成品音乐，带人声与伴奏，结尾自然收尾，不要突然截断。"
    return body


def build(api: dict[str, Any], spec: dict[str, Any], *, text: str, lyrics: str,
          duration: float | None, seed: int, prefix: str,
          ref: dict[str, Any] | None = None) -> dict[str, Any]:
    """把提示词 / 歌词 / 时长 / 参考音频 / 文件名前缀注进音频工作流。"""
    for target in spec.get("prompt_targets") or []:
        node = api.get(target["node"])
        if node:
            node["inputs"][target["field"]] = text
    if spec.get("lyrics_target") and lyrics:
        node = api.get(spec["lyrics_target"]["node"])
        if node:
            node["inputs"][spec["lyrics_target"]["field"]] = lyrics
    if spec.get("duration_target") and duration:
        node = api.get(spec["duration_target"]["node"])
        if node:
            node["inputs"][spec["duration_target"]["field"]] = float(duration)
    if spec.get("audio_ref") and ref and ref.get("comfyName"):
        node = api.get(spec["audio_ref"]["load"])
        if node:
            node["inputs"][spec["audio_ref"]["field"]] = ref["comfyName"]
    for target in spec.get("seed_targets") or []:
        node = api.get(target["node"])
        if node:
            node["inputs"][target["field"]] = seed
    for node_id in spec.get("output_nodes") or []:
        node = api.get(node_id)
        if node and "filename_prefix" in node["inputs"]:
            node["inputs"]["filename_prefix"] = prefix
    return api


def state_payload() -> dict[str, Any]:
    out = []
    for mode in MODES:
        row = dict(mode)
        row["grades"] = grades(mode["id"])
        out.append(row)
    return {
        "modes": out, "master": MASTER,
        "durations": list(DURATIONS),
        "voiceFields": VOICE_FIELDS,
    }


def bridge(mode: str) -> dict[str, Any]:
    info = MODE_IDS.get(mode) or MODE_IDS["music"]
    return registry.bridge(info["wf"])
