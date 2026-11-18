"""提示词调试台：直接调 ComfyUI 出图，图片落到 _dev/prompt_lab/out。

用法（在 ComfyBridge-Desktop 根目录）：
  .venv/Scripts/python.exe _dev/prompt_lab/gen.py \
      --wf 设定图-文生图 --prompt-file _dev/prompt_lab/p.txt --tag t2i_normal_01 \
      --aspect "16:9 (Widescreen)" --megapixels 1.265625 --multiple 16

参考图（图生图/扩图）：
  --refs _dev/prompt_lab/refs/a.png _dev/prompt_lab/refs/b.png

读 --prompt-file 的纯文本作为整条提示词，走项目 registry.build_prompt，
和线上提交完全同一条链路；出图后按 subfolder/filename 从 /view 取回并存盘。
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.comfy import ComfyClient  # noqa: E402
from app.config import config  # noqa: E402
from app.workflows import registry  # noqa: E402

OUT = ROOT / "_dev" / "prompt_lab" / "out"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wf", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--aspect", default="16:9 (Widescreen)")
    ap.add_argument("--megapixels", type=float, default=1.265625)
    ap.add_argument("--multiple", type=int, default=16)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--refs", nargs="*", default=[])
    ap.add_argument("--timeout", type=int, default=240)
    args = ap.parse_args()

    config.load()
    text = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    comfy = ComfyClient()

    # 参考图上传，得到 comfyName；第 1 张改写 root LoadImage，其余走 ref_slots
    refs: list[dict] = []
    for i, p in enumerate(args.refs):
        path = Path(p)
        if not path.is_absolute():
            path = ROOT / path
        name = comfy.upload_image(path, f"lab_{args.tag}_{i}{path.suffix}")
        refs.append({"id": f"lab{i}", "comfyName": name, "path": str(path), "name": path.name})

    # 用组合档位那套：参数显式写到尺寸节点；tag 用 tag 名隔离拷贝
    params = {"13": {}, "502": {}}
    if args.aspect:
        params["13"]["aspect_ratio"] = args.aspect
        params["502"]["aspect_ratio"] = args.aspect
    if args.megapixels:
        params["13"]["megapixels"] = args.megapixels
        params["502"]["megapixels"] = args.megapixels
    if args.multiple:
        params["13"]["multiple"] = args.multiple
        params["502"]["multiple"] = args.multiple

    seed = args.seed if args.seed is not None else secrets.randbelow(2**62)
    prompt = registry.build_prompt(
        args.wf, "默认", text, refs, seed, f"LAB_{args.tag}",
        params=params, tag=f"lab_{args.tag}",
    )
    pid = comfy.submit(prompt)
    print(json.dumps({"tag": args.tag, "prompt_id": pid, "seed": seed, "refs": len(refs)}, ensure_ascii=False))

    t0 = time.time()
    while time.time() - t0 < args.timeout:
        time.sleep(0.8)
        entry = comfy.history(pid)
        if not entry:
            continue
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            msgs = status.get("messages") or []
            for name, payload in msgs:
                if name == "execution_error":
                    print("ERROR", payload.get("node_type"), str(payload.get("exception_message"))[:600], file=sys.stderr)
            return 2
        if status.get("completed") or status.get("status_str") == "success":
            bridge = registry.bridge(args.wf)
            saved: list[str] = []
            for node_id in bridge.get("output_nodes", []):
                for item in (entry.get("outputs", {}).get(node_id, {}) or {}).get("images", []) or []:
                    fn = item.get("filename")
                    if not fn:
                        continue
                    raw = comfy.view(fn, item.get("subfolder", "") or "", item.get("type", "output") or "output")
                    if raw is None:
                        continue
                    out = OUT / f"{args.tag}__{fn}"
                    out.write_bytes(raw)
                    meta = (item.get("metadata") or {})
                    saved.append(f"{out.name} ({meta.get('width')}x{meta.get('height')})")
            print(json.dumps({"tag": args.tag, "images": saved, "seconds": round(time.time() - t0, 1)}, ensure_ascii=False))
            return 0
    print("TIMEOUT", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
