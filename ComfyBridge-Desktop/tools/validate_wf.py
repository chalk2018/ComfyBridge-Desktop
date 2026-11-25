"""对着本机 ComfyUI 的 object_info 校验 workflows/*.json（API 格式）。

只做静态校验，不提交任务：
- class_type 是否存在；
- required 输入是否齐（缺了就报，免得等到 /prompt 才 400）；
- 连线目标节点 / 输出下标是否合法；
- 枚举值是否在可选范围内（unet_name / lora_name / vae_name / sampler 之类）。

用法：
    .\\.venv\\Scripts\\python.exe tools\\validate_wf.py 视频生成-参考生视频-h3 音频-音乐生成
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / "workflows"
BASE = "http://127.0.0.1:8188"

_cache: dict[str, dict] = {}


def info(cls: str) -> dict:
    if cls not in _cache:
        with urllib.request.urlopen(f"{BASE}/object_info/{cls}", timeout=30) as r:
            _cache[cls] = json.load(r).get(cls) or {}
    return _cache[cls]


def check(name: str) -> list[str]:
    api = json.loads((WF / f"{name}.json").read_text(encoding="utf-8"))
    errs: list[str] = []
    for nid, node in api.items():
        cls = node.get("class_type", "")
        meta = info(cls)
        if not meta:
            errs.append(f"[{nid}] 本机没有节点类型 {cls}")
            continue
        inputs = node.get("inputs") or {}
        spec = meta.get("input") or {}
        order = meta.get("input_order") or {}
        for section in ("required", "optional"):
            for field in order.get(section) or []:
                if field in inputs:
                    continue
                if section == "required":
                    cfg = (spec.get("required") or {}).get(field)
                    # 有默认值的算可省
                    if isinstance(cfg, list) and len(cfg) > 1 and isinstance(cfg[1], dict) and "default" in cfg[1]:
                        continue
                    errs.append(f"[{nid}] {cls} 缺必填输入 {field}")
        for field, value in inputs.items():
            cfg = (spec.get("required") or {}).get(field) or (spec.get("optional") or {}).get(field)
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] in api:
                target, idx = value[0], value[1]
                tmeta = info(api[target].get("class_type", ""))
                outs = tmeta.get("output") or []
                if not isinstance(idx, int) or idx >= len(outs):
                    errs.append(f"[{nid}] {cls}.{field} 连到 [{target}] 的输出 {idx}，但它只有 {len(outs)} 个输出")
                continue
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0] not in api:
                errs.append(f"[{nid}] {cls}.{field} 连到不存在的节点 {value[0]}")
                continue
            if cfg is None or not isinstance(cfg, list) or not cfg:
                continue
            kind = cfg[0]
            if isinstance(kind, list) and isinstance(value, str) and value not in kind:
                errs.append(f"[{nid}] {cls}.{field} = {value!r} 不在候选里（{len(kind)} 个）")
    return errs


def main(argv: list[str]) -> int:
    names = argv or [p.stem for p in sorted(WF.glob("*.json")) if not p.name.endswith(".bridge.json")]
    bad = 0
    for name in names:
        if not (WF / f"{name}.json").exists():
            print(f"跳过 {name}：文件不存在")
            continue
        errs = check(name)
        if errs:
            bad += 1
            print(f"\n✗ {name}")
            for e in errs:
                print("   ", e)
        else:
            print(f"✓ {name}")
    print(f"\n{len(names) - bad}/{len(names)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
