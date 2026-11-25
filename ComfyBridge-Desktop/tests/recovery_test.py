"""重启恢复逻辑的单元级验证（不启动第二个服务、不改动真实状态文件）。

需求 04 章：服务重启时有任务在跑，要拿 prompt_id 去 ComfyUI 问一遍：
跑完的补取成品、还在跑的继续盯、都没有的转失败。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import config  # noqa: E402
from app.services import service  # noqa: E402
from app.state import now_ms, state  # noqa: E402


def main() -> int:
    """构造一条"重启时还在跑"的任务，验证能按 ComfyUI 历史补回成品。"""
    config.load()
    records = ROOT / "data" / "tasks" / "records.jsonl"
    if not records.exists():
        print("SKIP: 还没有任务记录，先跑 smoke_test.py")
        return 0
    row = None
    for line in records.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("prompt_id") and item.get("result") == "done":
            row = item
    if not row:
        print("SKIP: 找不到已完成的 prompt_id")
        return 0

    # 只改内存；禁止写回 state.json / records.jsonl，避免影响正在运行的服务
    state.persist = lambda: None
    service._write_record = lambda *_args, **_kwargs: None
    state.jobs = [{
        "id": "recover_probe", "cap": "image.generate", "capName": "图片生成",
        "capSnapshot": {"id": "image.generate", "name": "图片生成"},
        "wf": "文生图", "prompt": "恢复测试", "refs": [], "t0": now_ms(), "seed": None,
        "runs": [{
            "id": "recover_run", "seq": 999, "mode": "normal", "wf": "文生图",
            "grade": "默认", "seed": None, "media": "image", "state": "recovering",
            "pct": 40, "step": 3, "total": 8, "t0": now_ms(), "t1": None,
            "file": "", "files": [], "prompt_id": row["prompt_id"], "err": None,
            "archive_failed": None, "size": "", "node": "",
        }],
    }]
    service._recover()
    run = state.jobs[0]["runs"][0]
    ok = run["state"] == "done" and bool(run["files"])
    print(f"[{'PASS' if ok else 'FAIL'}] 重启恢复：recovering -> {run['state']}，取回 {len(run['files'])} 个成品")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
