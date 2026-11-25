"""故事板提示词调试执行器：走与线上一致的 registry.build_prompt 链路出图。

用法：
  .venv/Scripts/python.exe _dev/prompt_lab/board/run.py _dev/prompt_lab/board/cases_w1.json
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.comfy import ComfyClient  # noqa: E402
from app.config import config  # noqa: E402
from app.workflows import registry  # noqa: E402
from compose import LAYOUTS, compose  # noqa: E402

OUT = ROOT / "_dev" / "prompt_lab" / "board" / "out"
OUT.mkdir(parents=True, exist_ok=True)
WF_16 = "设定图-参考生图"
WF_6 = "LAB_BOARD6"


def register_lab_wf() -> None:
    base = config.raw["workflows"][WF_16]
    lab = dict(base)
    lab["file"] = "workflows/故事板-参考生图-宽幅6格.json"
    lab["bridge"] = "workflows/故事板-参考生图-宽幅6格.bridge.json"
    lab["grades"] = [{"name": "默认", "locked": True, "note": "8:3 宽幅"}]
    config.raw["workflows"][WF_6] = lab


def run_case(case: dict) -> int:
    tag = case["tag"]
    layout = case["layout"]
    L = LAYOUTS[layout]
    text = compose(layout, case["cells"], case.get("ink", "color"),
                   case.get("line_style", "W3"), len(case.get("refs", [])))
    pf = OUT / f"_{tag}.txt"
    pf.write_text(text, encoding="utf-8")
    comfy = ComfyClient()
    refs = []
    ref_files = list(case.get("refs", []))
    if not ref_files:
        # 编辑工作流必须有一个 LoadImage 输入；0 张参考图时用纯白占位图，
        # 提示词仍按 0 张参考图拼装，尺寸由 EmptyLatentImage 决定。
        ref_files = ["_dev/prompt_lab/board/refs/blank_16x9.png"]
    for i, rel in enumerate(ref_files):
        path = Path(rel)
        if not path.is_absolute():
            path = ROOT / path
        name = comfy.upload_image(path, f"board_{tag}_{i}{path.suffix}")
        refs.append({"id": f"r{i}", "comfyName": name, "path": str(path), "name": path.name})
    wf = WF_6 if layout == "G6" else WF_16
    params = {"502": {}, "13": {}}
    if wf == WF_6:
        params["502"].update({"aspect_ratio": "16:9 (Widescreen)", "megapixels": 1.99, "multiple": 16})
        params["459_456"] = {"width": L["size"][0], "height": L["size"][1]}
    else:
        params["502"].update({"aspect_ratio": "16:9 (Widescreen)", "megapixels": 1.265625, "multiple": 16})
    seed = case.get("seed") or secrets.randbelow(2**62)
    prompt = registry.build_prompt(wf, "默认", text, refs, seed, f"BOARD_{tag}",
                                   params=params, tag=f"board_{tag}")
    pid = comfy.submit(prompt)
    print(json.dumps({"tag": tag, "prompt_id": pid, "seed": seed, "refs": len(refs)}, ensure_ascii=False), flush=True)
    t0 = time.time()
    while time.time() - t0 < case.get("timeout", 300):
        time.sleep(0.8)
        entry = comfy.history(pid)
        if not entry:
            continue
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            for name, payload in status.get("messages", []):
                if name == "execution_error":
                    print("ERROR", payload.get("node_type"), str(payload.get("exception_message"))[:600], file=sys.stderr)
            return 2
        if status.get("completed") or status.get("status_str") == "success":
            bridge = registry.bridge(wf)
            saved = []
            for node_id in bridge.get("output_nodes", []):
                for item in (entry.get("outputs", {}).get(node_id, {}) or {}).get("images", []) or []:
                    fn = item.get("filename")
                    if not fn:
                        continue
                    raw = comfy.view(fn, item.get("subfolder", "") or "", item.get("type", "output") or "output")
                    if not raw:
                        continue
                    out = OUT / f"{tag}__{fn}"
                    out.write_bytes(raw)
                    meta = item.get("metadata") or {}
                    saved.append(f"{out.name} ({meta.get('width')}x{meta.get('height')})")
            print(json.dumps({"tag": tag, "images": saved, "seconds": round(time.time() - t0, 1)}, ensure_ascii=False), flush=True)
            return 0
    print("TIMEOUT", tag, file=sys.stderr)
    return 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    config.load()
    register_lab_wf()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    failed = 0
    for case in cases:
        if args.only and case["tag"] not in args.only:
            continue
        try:
            rc = run_case(case)
        except Exception as exc:  # noqa: BLE001
            print("EXC", case["tag"], repr(exc), getattr(exc, "raw", "")[:2000], file=sys.stderr)
            rc = 9
        failed += rc != 0
    print(f"DONE cases={len(cases)} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
