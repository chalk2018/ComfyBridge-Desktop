"""开发期冒烟测试：把转换后的 API 工作流提交给本机 ComfyUI，确认能真正出图。

Usage:
    python tools/test_prompt.py [t2i|edit]
"""

from __future__ import annotations

import json
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / "workflows"
COMFY = "http://127.0.0.1:8188"
KIND = sys.argv[1] if len(sys.argv) > 1 else "t2i"
STEM = "图片生成-文生图-qwen2.1turbo" if KIND == "t2i" else "图片生成-参考生图-qwen2.1turbo"
PROMPT = "一只橘猫坐在窗台上，午后阳光，写实摄影"


def req(path: str, payload: dict | None = None, timeout: int = 30) -> tuple[int, object]:
    """向 ComfyUI 发 GET/POST，返回 (status, json_or_text)。"""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        COMFY + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw.strip().startswith(("{", "[")) else raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> None:
    """提交一次真实生成并等待结果。"""
    api = json.loads((WF / f"{STEM}.json").read_text(encoding="utf-8"))
    bridge = json.loads((WF / f"{STEM}.bridge.json").read_text(encoding="utf-8"))
    for target in bridge["prompt_targets"]:
        api[target["node"]]["inputs"][target["field"]] = PROMPT
    seed = random.randint(1, 2**62)
    for target in bridge["seed_targets"]:
        api[target["node"]]["inputs"][target["field"]] = seed
    for node_id in bridge["output_nodes"]:
        if "filename_prefix" in api[node_id]["inputs"]:
            api[node_id]["inputs"]["filename_prefix"] = "ComfyBridge_smoketest"
    print(f"submitting {STEM}, nodes={len(api)}, seed={seed}")
    status, result = req("/prompt", {"prompt": api, "client_id": "comfybridge-smoketest"})
    print("POST /prompt ->", status, str(result)[:1200])
    if status != 200 or not isinstance(result, dict):
        return
    prompt_id = result.get("prompt_id")
    if not prompt_id:
        return
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(3)
        status, hist = req(f"/history/{prompt_id}")
        if status == 200 and isinstance(hist, dict) and prompt_id in hist:
            entry = hist[prompt_id]
            print("history status:", json.dumps(entry.get("status", {}), ensure_ascii=False)[:600])
            outputs = entry.get("outputs", {})
            print("outputs:", json.dumps(outputs, ensure_ascii=False)[:900])
            return
        _, queue = req("/prompt")
        if isinstance(queue, dict):
            print("waiting... queue_running=", len(queue.get("queue_running", [])),
                  "queue_pending=", len(queue.get("queue_pending", [])))
    print("TIMEOUT")


if __name__ == "__main__":
    main()
