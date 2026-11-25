"""视频生成扩展（独立模块）：多参极速生视频。

为什么单独一个文件：这个模块和 app/videolib.py 的「视频生成」虽然界面像，机制完全不同——

  视频生成：一档 = 一组参数（比例/分辨率/步数），所有档共用一条工作流，运行时注入参数；
  本模块：  一档 = 一份独立的工作流文件，档位之间连加速链都不同（4/8/20 步走不同的 LoRA 链），
            切档 = 换整条工作流，而不是改几个数字。

所以这里不复用 videolib 的 grades/opts/build，只复用它的提示词记号翻译（[图 1] → <Picture 1>）。
任何对本模块的改动都不会影响原来的视频生成。

运行时注入的东西只有 6 类，全部来自用户输入：
  1. 提示词   -> CR Prompt Text (250)
  2. 种子     -> RandomNoise (129)
  3. 宽高比   -> ResolutionSelector (115).aspect_ratio，用户选 16:9 / 9:16
  4. 参考素材 -> H3 节点的 autogrow 槽，按用户实际传了几张就接几张（其余槽不连线）
  5. 时长     -> DF_Int_to_Float (164).Value（秒），经 ComfyMathExpression 折算成帧数
  6. 参考模式 -> 按「档位 × 模式」换模型：首尾帧生成 / 参考生成用不同 UNET（+LoRA）。
               只改该档真正接到 sampler 的那条链，20 采没有 LoRA 就不碰 LoRA。
               这里改的是内存中的工作流副本，磁盘上的工作流文件始终原样不动。
"""

from __future__ import annotations

import copy
import re
from typing import Any

from .config import config
from .workflows import registry

CAP_ID = "video.ext"
MASTER_FALLBACK = "8采480P"
RATIOS = ("16:9", "9:16")
# ResolutionSelector 的 COMBO 选项必须和 object_info 完全一致，否则 ComfyUI 校验拒收
RATIO_COMBO = {
    "16:9": "16:9 (Widescreen)",
    "9:16": "9:16 (Portrait Widescreen)",
}
TOKEN = re.compile(r"\[(图|视频|音频)\s*(\d+)\]")


def _raw() -> dict[str, Any]:
    raw = config.raw.get("videoExt")
    return raw if isinstance(raw, dict) else {}


def funcs() -> list[dict[str, Any]]:
    rows = _raw().get("funcs") or []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def find_func(func_id: str) -> dict[str, Any] | None:
    return next((f for f in funcs() if f["id"] == func_id), None)


def default_func() -> dict[str, Any] | None:
    return funcs()[0] if funcs() else None


def state_payload() -> dict[str, Any]:
    """/api/state 里给前端的元数据：只读、不参与任何配置写回。"""
    out: list[dict[str, Any]] = []
    for f in funcs():
        row = copy.deepcopy(f)
        row["grades"] = list(f.get("grades") or [])
        row["ratios"] = list(f.get("ratios") or RATIOS)
        out.append(row)
    return {"funcs": out, "capId": CAP_ID}


def wf_for(func_id: str, grade: str) -> str:
    """档位名 -> 那一档自己的工作流名。找不到就报错，绝不静默跑别的档。"""
    f = find_func(func_id)
    if not f:
        raise ValueError("没有这个子功能")
    grades = list(f.get("grades") or [])
    if grade not in grades:
        raise ValueError(f"没有档位「{grade}」")
    name = f"{f.get('wfPrefix') or '视频扩展-' + f['id']}-{grade}"
    if not config.workflow(name):
        raise ValueError(f"档位「{grade}」的工作流还没配置：{name}")
    return name


def grades(func_id: str) -> list[str]:
    return list((find_func(func_id) or {}).get("grades") or [])


def default_grade(func_id: str) -> str:
    f = find_func(func_id) or {}
    return str(f.get("defaultGrade") or MASTER_FALLBACK)


def modes(func_id: str) -> list[dict[str, Any]]:
    """参考模式列表（首尾帧生成 / 参考生成），只读元数据。"""
    rows = (find_func(func_id) or {}).get("modes") or []
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def norm_mode(func_id: str, mode: str | None) -> str:
    """非法/缺失模式回退到配置默认值，不让脏值漏进模型映射。"""
    f = find_func(func_id) or {}
    ids = [str(m["id"]) for m in modes(func_id)]
    if mode in ids:
        return str(mode)
    return str(f.get("defaultMode") or (ids[0] if ids else "fl2v"))


def mode_name(func_id: str, mode: str | None) -> str:
    mid = norm_mode(func_id, mode)
    hit = next((m for m in modes(func_id) if m["id"] == mid), None)
    return str((hit or {}).get("name") or mid)


def to_h3(text: str) -> str:
    kind_map = {"图": "Picture", "视频": "Video", "音频": "Audio"}
    return TOKEN.sub(lambda m: f"<{kind_map[m.group(1)]} {int(m.group(2))}>", text or "")


def compose(text: str) -> str:
    """提示词只有两处约定：素材记号翻成 H3 认的写法、禁止把字烧进画面。"""
    body = to_h3(text).strip()
    if not body:
        return ""
    return body + "\n画面里不要出现字幕、水印、台标或任何文字。"


def _set_input(api: dict[str, Any], node: str, field: str, value: Any) -> None:
    if node in api:
        api[node]["inputs"][field] = value


def build(api: dict[str, Any], *, text: str, ratio: str, refs: list[dict[str, Any]],
          seed: int, prefix: str, duration: float | None,
          func: str = "multiRefFast", grade: str | None = None,
          mode: str | None = None) -> dict[str, Any]:
    """把用户输入注入一条档位工作流（api 会被就地改）。

    注意：不碰任何"加速链"节点（124 / 126 / 136 / 276 / 283 等）的参数结构。
    那些是档位文件本身的差异，运行时不能改，否则一档一文件就没意义了。
    参考模式只换生效链上的 unet_name / lora_name 两个模型字段，不动连线。
    """
    api = copy.deepcopy(api)
    spec = (registry.bridge(_bridge_wf_name(api)) or {}).get("video_ext") or {}
    h3 = spec.get("node", "230")
    prompt_node = spec.get("prompt", "250")
    ratio_node = spec.get("ratio_node", "115")
    seed_node = spec.get("seed", "129")
    out_node = spec.get("output", "92")

    # 1. 提示词（本工作流的提示词节点是 CR Prompt Text，字段名 prompt 而非 value）
    _set_input(api, prompt_node, spec.get("prompt_field", "prompt"), text)
    # 2. 宽高比：只改 ResolutionSelector 的 ratio，不动它的 megapixels 连线
    #    （megapixels 由档位文件自己的 ImpactSwitch 决定 480P / 768P，属于档位配方）
    _set_input(api, ratio_node, spec.get("ratio_field", "aspect_ratio"),
               RATIO_COMBO.get(ratio, RATIO_COMBO["16:9"]))
    # 3. 种子
    _set_input(api, seed_node, "noise_seed", seed)
    # 4. 时长（秒）：母版用 ImpactSwitch(205) 从 11 个 DF_Int_to_Float 里挑一个：
    #    input1=164(5秒) … input11=200(15秒)，所以 select = 秒数 - 4。
    #    帧数由 ComfyMathExpression(132) 按 (秒数 × 24) 再对齐 17 的倍数算出。
    if duration is not None:
        sec = max(5, min(15, int(round(float(duration)))))
        _set_input(api, spec.get("length_select_node", "205"), "select", sec - 4)
    # 5. 出片文件名前缀（成品归档按它识别本次生成的文件）
    _set_input(api, out_node, spec.get("prefix_field", "filename_prefix"), prefix)

    # 6. 参考素材：按用户实际传的数量重建 autogrow 槽，没传的槽完全不连线
    images = [r for r in refs if r.get("kind", "image") == "image"]
    videos = [r for r in refs if r.get("kind") == "video"]
    audios = [r for r in refs if r.get("kind") == "audio"]
    nodes = spec.get("images") or []
    vnodes = spec.get("videos") or []
    vcnodes = spec.get("video_components") or []
    anodes = spec.get("audios") or []

    # 先把母版里所有参考节点整只删掉，再按用户实际传的数量重建。
    # 不能只清空 inputs 留空节点——ComfyUI 会校验必填项，空 LoadImage/LoadAudio 会让整条任务失败。
    for nid in nodes + vnodes + vcnodes + anodes:
        api.pop(nid, None)
    if h3 in api:
        for key in list(api[h3]["inputs"]):
            if key.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios.")):
                api[h3]["inputs"].pop(key, None)
        for index, ref in enumerate(images[:6]):
            nid = nodes[index] if index < len(nodes) else f"ext_img_{index}"
            if nid not in api:
                api[nid] = {"class_type": "LoadImage", "inputs": {}}
            api[nid]["inputs"]["image"] = ref["comfyName"]
            api[h3]["inputs"][f"ref_images.ref_image_{index}"] = [nid, 0]
        for index, ref in enumerate(videos[:1]):
            vn = vnodes[index] if index < len(vnodes) else f"ext_vid_{index}"
            cn = vcnodes[index] if index < len(vcnodes) else f"ext_vidc_{index}"
            if vn not in api:
                api[vn] = {"class_type": "LoadVideo", "inputs": {}}
            if cn not in api:
                api[cn] = {"class_type": "GetVideoComponents", "inputs": {}}
            api[vn]["inputs"]["file"] = ref["comfyName"]
            api[cn]["inputs"]["video"] = [vn, 0]
            api[h3]["inputs"][f"ref_videos.ref_video_{index}"] = [cn, 0]
            api[h3]["inputs"][f"ref_video_audios.ref_video_audio_{index}"] = [cn, 1]
        for index, ref in enumerate(audios[:3]):
            nid = anodes[index] if index < len(anodes) else f"ext_aud_{index}"
            if nid not in api:
                api[nid] = {"class_type": "LoadAudio", "inputs": {}}
            api[nid]["inputs"]["audio"] = ref["comfyName"]
            api[h3]["inputs"][f"ref_audios.ref_audio_{index}"] = [nid, 0]

    # 7. 参考模式：按「档位 × 模式」换模型。
    #    档位文件里有三条并行模型链，只有一条接到 sampler；modelNodes 指明该档生效的
    #    那一条，另一条备用链保持原样。20 采没有 LoRA 加速链，lora 节点配成 null 就不碰。
    if grade:
        f = find_func(func) or {}
        chain = (f.get("modelNodes") or {}).get(grade) or {}
        mid = norm_mode(func, mode)
        model = ((f.get("models") or {}).get(grade) or {}).get(mid) or {}
        if chain.get("unet") and model.get("unet"):
            _set_input(api, str(chain["unet"]), "unet_name", str(model["unet"]))
        if chain.get("lora") and model.get("lora"):
            _set_input(api, str(chain["lora"]), "lora_name", str(model["lora"]))
    return api


def _bridge_wf_name(api: dict[str, Any]) -> str:
    """build 不需要知道工作流名（bridge 结构在 5 档里完全一样），用缓存里任一档即可。

    只有取注入点用；调用方已经把档位对应的工作流副本传进来了。
    """
    for name in ("视频扩展-多参极速生视频-8采480P", "视频扩展-多参极速生视频-4采480P"):
        if config.workflow(name):
            return name
    return ""
