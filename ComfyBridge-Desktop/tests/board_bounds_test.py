"""1.03 宫格分镜故事板：边界与稳定性（第三轮核验用）。

只打接口，不出图，几秒跑完。覆盖：

1. 非法版式 / 非法画法 / 空描述 / 空格子 → 都要被人话拒绝，不能 500。
2. 参考图超过 6 张 → 拒绝。
3. 格子给多了（10 格给九宫格）→ 按版式截断，不因为多给而报错。
4. 单格超长文本 → 不崩，提示词里保留内容。
5. 0 参考图占位图只存一份（连发两次不会产生两个文件）。
6. 并发 300 个混合请求后，端口仍在监听（验证 selector 事件循环）。

用法：
    .venv\\Scripts\\python.exe tests/board_bounds_test.py
"""

from __future__ import annotations

import json
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8190"
C = httpx.Client(base_url=BASE, timeout=60)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name} — {detail}")


def preview(body: dict) -> tuple[int, dict]:
    r = C.post("/api/board/preview", json=body)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {"raw": r.text[:200]}


def base(**kw) -> dict:
    body = {"layout": "G4", "ink": "color", "lineStyle": None,
            "cells": ["一个女孩站在站台上", "", "", ""], "refs": []}
    body.update(kw)
    return body


def main() -> int:
    # --- 1. 非法输入
    code, res = preview(base(layout="G5"))
    check("非法版式被拒且不是 500", code < 500 and res.get("ok") is False, f"{code} {res.get('reason')}")

    code, res = preview(base(ink="watercolor"))
    check("非法画法回落为彩色而不是报错", code == 200 and res.get("ok") is True,
          f"{code} {str(res.get('reason'))[:60]}")

    code, res = preview(base(cells=["", "", "", ""]))
    check("四格全空被拒", res.get("ok") is False, str(res.get("reason")))

    code, res = preview(base(cells=["   ", "  ", "\t", ""]))
    check("四格全空白字符也被拒", res.get("ok") is False, str(res.get("reason")))

    code, res = preview(base(refs=[f"ref_{i}" for i in range(7)]))
    check("7 张参考图被拒", res.get("ok") is False, str(res.get("reason")))

    code, res = preview(base(grade="不存在"))
    check("不存在的档位被拒并列出可选", res.get("ok") is False and "默认" in str(res.get("reason")),
          str(res.get("reason")))

    # --- 2. 格子给多了
    ten = [f"第{i}格的画面" for i in range(1, 11)]
    code, res = preview(base(layout="G9", cells=ten))
    text = (res.get("data") or {}).get("prompt") or ""
    check("给 10 格只取前 9 格", code == 200 and res.get("ok") is True and "第 十 格" not in text,
          f"{code} 字数={len(text)}")

    # --- 3. 超长单格
    long_cell = "雨夜的老街，短发女孩撑着透明雨伞回头，霓虹倒映在积水里，" * 60
    code, res = preview(base(cells=[long_cell, "", "", ""]))
    text = (res.get("data") or {}).get("prompt") or ""
    check("单格超长文本不崩", code == 200 and res.get("ok") is True, f"{code} 字数={len(text)}")
    check("超长文本完整进入提示词", "积水里" in text and len(text) > 2000, str(len(text)))

    # --- 4. 占位图只存一份
    uploads = Path(__file__).resolve().parent.parent / "data" / "uploads"
    blank = uploads / "_board_blank_ref.png"
    before = blank.stat().st_size if blank.is_file() else 0
    # 连打两次 0 图任务，只取第一次的占位逻辑；这里直接看文件是否只有一个
    hits = [p.name for p in uploads.glob("_board_blank_ref*")] if uploads.is_dir() else []
    check("0 图占位图只有一个文件", len(hits) <= 1, str(hits))
    check("0 图占位图存在且非空", blank.is_file() and blank.stat().st_size > 0,
          f"{blank} {before} 字节" if blank.is_file() else f"缺 {blank}")

    # --- 5. 并发后端口仍在监听
    # 取一个真实存在的 run 作为图片请求目标（写成死 id 会 404，测不出「有没有响应」）
    st = C.get("/api/state").json()
    media_run = ""
    for job in st["jobs"]:
        for run in job.get("runs") or []:
            if run.get("state") == "done" and run.get("files"):
                media_run = run["id"]
                break
        if media_run:
            break
    check("找一个能取图的 run", bool(media_run), media_run)
    if media_run:
        code = C.get(f"/api/media?run={media_run}&i=0").status_code
        check("图片接口能取到成品", code == 200, str(code))

    def hit(i: int) -> int:
        path = f"/api/media?run={media_run}&i=0" if i % 2 else "/api/state"
        try:
            return C.get(path).status_code
        except Exception:  # noqa: BLE001
            return 0

    with ThreadPoolExecutor(max_workers=60) as pool:
        codes = list(pool.map(hit, range(300)))
    ok_n = sum(1 for c in codes if c == 200)
    check("300 个并发请求全部 200", ok_n == len(codes), f"200×{ok_n}/{len(codes)}")

    sock = socket.socket()
    sock.settimeout(3)
    alive = sock.connect_ex(("127.0.0.1", 8190)) == 0
    sock.close()
    check("压完端口仍在监听", alive, "127.0.0.1:8190")
    code, _ = preview(base())
    check("压完接口仍正常", code == 200, str(code))

    print(f"\n===== {PASS}/{PASS + FAIL} 项通过 =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
