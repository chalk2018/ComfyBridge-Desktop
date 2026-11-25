"""把「画布图片生成节点」拆成文生图 / 参考生图两条链（幂等，可反复跑）。

起因：画布图片节点原本只挂「图片生成-参考生图-qwen2.1turbo」，这个工作流里有个
LoadImage 的默认值指向一张本机不存在的示例图，一条线都不接时 ComfyUI 直接
prompt_outputs_failed_validation 拒收。所以：

    画布-图片生成      → 图片生成-文生图（0 参考）
    画布-图片生成-参考 → 图片生成-参考生图（接了图）

canvasFuncs.image 里多一个 wfRef，画布提交时有参考图就自动换过去。

用法：
    .\\.venv\\Scripts\\python.exe tools/fix_canvas_image.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config.yaml"

T2I = ("图片生成-文生图-qwen2.1turbo", "画布图片生成节点：不接任何素材，纯提示词出图")
I2I = ("图片生成-参考生图-qwen2.1turbo", "画布图片生成节点：接了图片时用，按参考图改画")


def entry(name: str, note: str) -> dict:
    return {
        "file": f"workflows/{name}.json",
        "bridge": f"workflows/{name}.bridge.json",
        "status": "ok",
        "media": "image",
        "note": note,
        "fields": [],
        "grades": [{"name": "默认", "locked": True, "note": "按工作流原样出图"}],
    }


def main() -> int:
    raw = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    wfs = raw.setdefault("workflows", {})
    wfs["画布-图片生成"] = {**entry(*T2I), **{"grades": [{"name": "默认", "locked": True,
                                                    "note": "按工作流原样出图"}]}}
    wfs["画布-图片生成-参考"] = entry(*I2I)

    funcs = raw.setdefault("canvasFuncs", {})
    image = funcs.setdefault("image", {})
    image["name"] = image.get("name") or "图片生成"
    image["wf"] = "画布-图片生成"
    image["wfRef"] = "画布-图片生成-参考"

    for cap in raw.get("capabilities") or []:
        if cap.get("id") == "canvas.video":
            list_wfs = list(cap.get("workflows") or [])
            for name in ("画布-图片生成", "画布-图片生成-参考", "画布-视频生成"):
                if name not in list_wfs:
                    list_wfs.append(name)
            cap["workflows"] = list_wfs

    CFG.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120),
                   encoding="utf-8")
    print("已更新：画布-图片生成 → 文生图；新增 画布-图片生成-参考 → 参考生图")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
