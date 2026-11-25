"""把「视频生成扩展」的 5 个 UI 工作流转换成 ComfyBridge 用的 API 格式 + bridge。

只处理独立的新模块工作流，读写的文件名都带 `视频扩展-` 前缀，不碰任何现有工作流。

为什么不能直接用 tools/convert_workflows.py 的产物：
1) 那 5 个母版里 6 个 LoadImage、LoadVideo、GetVideoComponents、3 个 LoadAudio
   都是 mode=4（静音），Converter.convert() 会把它们剔除；但我们要运行时按用户
   实际上传数量动态重建这些槽，所以 bridge 里显式登记这些节点，后端自行插入。
2) LoadImage 的 `upload` 是前端按钮，不能进入 API prompt；Converter 没过滤它。
3) 宽高比要运行时写进 ResolutionSelector 的 aspect_ratio，bridge 必须显式登记。

用法：python tools/convert_videoext.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import convert_workflows as cw  # noqa: E402

SRC_DIR = Path(
    r"D:\ComfyUI-Desktop\Comfy-Desktop\ComfyUI-Installs\ComfyUI (1)\ComfyUI\user\default\workflows"
)
OUT_DIR = ROOT / "workflows"

GRADES = [
    ("4采480P", "加速版-MiniMax+H3｜多参图像音频极速生视频 （挡位：4采480P）.json"),
    ("8采480P", "加速版-MiniMax+H3｜多参图像音频极速生视频 （挡位：8采480P）.json"),
    ("8采768P", "加速版-MiniMax+H3｜多参图像音频极速生视频 （挡位：8采768P） .json"),
    ("20采480P", "加速版-MiniMax+H3｜多参图像音频极速生视频 （挡位：20采480P）.json"),
    ("20采768P", "加速版-MiniMax+H3｜多参图像音频极速生视频 （挡位：20采768P） .json"),
]

OUT_STEM = "视频扩展-多参极速生视频"


def clean_api(api: dict, ui_data: dict | None = None) -> list[str]:
    """清掉前端字段 / 占位文件名，返回备注。"""
    notes: list[str] = []
    for nid, spec in list(api.items()):
        inputs = spec.get("inputs") or {}
        # upload 是 ComfyUI 前端的"选择文件"按钮，API 不接受
        if "upload" in inputs:
            del inputs["upload"]
            notes.append(f"{nid} 去除 upload 前端字段")
    # ImpactSwitch 的 input1..inputN 在 object_info 里是 dynamic optional，
    # Converter 只搬了 input1；select=2 以上会拿到 None（768P 失败的根因）。
    # 从 UI 原始连线补全所有 inputN。205（时长 5~15 秒）和 225（480P/768P）都靠它。
    if ui_data:
        links = {int(l[0]): l for l in ui_data.get("links", [])}
        for node in ui_data.get("nodes", []):
            if node.get("type") != "ImpactSwitch":
                continue
            nid = str(node["id"])
            sw = api.get(nid)
            if not sw or sw.get("class_type") != "ImpactSwitch":
                continue
            for item in node.get("inputs", []):
                name = item.get("name", "")
                if not name.startswith("input") or name in sw["inputs"]:
                    continue
                lid = item.get("link")
                if lid in links:
                    src = links[lid]
                    sw["inputs"][name] = [str(src[1]), int(src[2])]
                    notes.append(f"{nid} 补回 {name} <- {src[1]}")
    return notes


def bridge_for(api: dict) -> dict:
    """生成这个模块专用的 bridge：明确告诉后端注入点在哪。"""
    return {
        "media": "video",
        "prompt_targets": [{"node": "250", "field": "prompt"}],
        "seed_targets": [{"node": "129", "field": "noise_seed"}],
        "output_nodes": ["92"],
        "root_load_images": [],
        "class_types": {nid: spec["class_type"] for nid, spec in api.items()},
        # 本模块专用：运行时注入契约，后端 videoext.py 读这里
        "video_ext": {
            "node": "230",                 # MiniMaxH3ReferenceToVideo
            "prompt": "250",               # CR Prompt Text
            "prompt_field": "prompt",      # 本工作流的提示词字段名（不是 value）
            "ratio_node": "115",           # ResolutionSelector
            "ratio_field": "aspect_ratio",
            "length_node": "132",          # ComfyMathExpression -> 帧数
            "length_select_node": "205",   # ImpactSwitch：select = 时长秒数 - 4（5秒→1 … 15秒→11）
            "length_value_node": "164",    # DF_Int_to_Float：时长秒数 5~15（构建时按顺序选任意一个）
            "seed": "129",                 # RandomNoise
            "output": "92",                # SaveVideo
            "prefix_field": "filename_prefix",
            # 静音但运行时会重建的参考槽（API 里保留占位节点，名字在 build 时替换）
            "images": ["231", "232", "233", "234", "235", "236"],
            "videos": ["251"],
            "video_components": ["252"],
            "audios": ["247", "248", "249"],
        },
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    oi = cw.ObjectInfo()
    report: list[str] = []

    for grade, filename in GRADES:
        src_path = SRC_DIR / filename
        data = json.loads(src_path.read_text(encoding="utf-8"))
        stem = f"{OUT_STEM}-{grade}"
        conv = cw.Converter(data, oi)
        api, _ = conv.convert()
        notes = clean_api(api, data)
        problems = conv.validate()
        (OUT_DIR / f"{stem}.json").write_text(json.dumps(api, ensure_ascii=False, indent=2), encoding="utf-8")
        (OUT_DIR / f"{stem}.bridge.json").write_text(json.dumps(bridge_for(api), ensure_ascii=False, indent=2), encoding="utf-8")
        report.append(f"===== {filename} -> {stem} =====")
        report.append(f"API 节点 {len(api)}; 校验问题: {problems or '无'}; 清理: {notes or '无'}")

    print("\n".join(report))


if __name__ == "__main__":
    main()
