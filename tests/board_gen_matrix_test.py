"""1.03 宫格分镜故事板：真实出图矩阵（第二轮核验用）。

覆盖 `board_e2e_test.py --gen` 没跑到的两条路径：

1. 九宫格 · 彩色 · 0 张参考图 —— 3×3 版式在应用层真实出图（此前只在调试脚本里出过）。
2. 四宫格 · 彩色 · 1 张参考图 —— 参考图注入 + 格子写「图 1」锁身份的端到端验证。

两张都用默认档，逐项核对尺寸、归档目录、图片不是纯色空白，人工看图另附在报告里。

用法：
    .venv\\Scripts\\python.exe tests\\board_gen_matrix_test.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8190"
C = httpx.Client(base_url=BASE, timeout=180)

PASS = 0
FAIL = 0

# 用一张真实角色设定图当参考图：同一人物在四宫格里要认得出是同一个人
REF_SRC = Path("D:/ComfyUI-0.37.1/ComfyUI/output/ComfyBridgeOutput/角色设定图/"
               "ComfyBridge_job_2953_2955_00001.png")
# 第三轮换一张不同的参考图（当场景/建筑用），顺带验证多图注入
REF_SRC2 = Path("D:/ComfyUI-0.37.1/ComfyUI/output/ComfyBridgeOutput/角色设定图/"
                "ComfyBridge_job_0774_776_00001.png")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name} — {detail}")


def api(path: str, body: dict | None = None, method: str = "GET") -> dict:
    if method == "GET":
        return C.get(path).json()
    return C.post(path, json=body).json()


def wait_idle(timeout: float = 180) -> None:
    """普通生成要求 ComfyUI 队列空闲，提交前先等干净。"""
    end = time.time() + timeout
    while time.time() < end:
        st = api("/api/state")
        busy = [r for j in st["jobs"] for r in j.get("runs") or []
                if r.get("state") in ("queued", "running")]
        if not busy:
            return
        time.sleep(1.0)


def upload(path: Path) -> str:
    with path.open("rb") as fh:
        res = C.post("/api/uploads", files={"file": (path.name, fh.read(), "image/png")}).json()
    return (res.get("data") or {}).get("id", "")


def run_board(layout: str, ink: str, cells: list[str], refs: list[str],
              grade: str = "默认", line_style: str | None = None) -> dict:
    """提交一张故事板并等到结束，返回最后一条 run。"""
    wait_idle()
    body = {"capId": "board.story", "grade": grade, "prompt": "分镜矩阵测试", "refs": refs,
            "board": {"layout": layout, "ink": ink, "lineStyle": line_style,
                      "grade": grade, "refs": refs, "cells": cells}}
    res = api("/api/tasks", body, "POST")
    job_id = (res.get("data") or {}).get("job_id", "")
    check(f"提交 {layout}·{ink}", bool(job_id), str(res.get("reason")))
    if not job_id:
        return {}
    for _ in range(160):
        st = api("/api/state")
        job = next((x for x in st["jobs"] if x["id"] == job_id), None)
        run = ((job or {}).get("runs") or [{}])[-1]
        if run.get("state") in ("done", "failed", "canceled"):
            return run
        time.sleep(3)
    return {}


def image_is_generated(path: str) -> tuple[bool, str]:
    """粗判成品不是纯色/全白：看像素标准差。"""
    try:
        from PIL import Image, ImageStat
    except Exception as exc:  # noqa: BLE001
        return True, f"PIL 不可用，跳过像素检查（{exc}）"
    try:
        img = Image.open(path).convert("L")
        st = ImageStat.Stat(img)
        return st.stddev[0] > 8, f"stddev={st.stddev[0]:.1f}"
    except Exception as exc:  # noqa: BLE001
        return False, f"打不开成品：{exc}"


def main() -> int:
    # --- 1. 九宫格 · 彩色 · 0 参考图
    print("=== 九宫格 · 彩色 · 0 张参考图 ===")
    cells9 = [
        "清晨的老式照相馆，短发女孩推门进来，中景",
        "她在柜台前挑选相框，手部特写",
        "",
        "午后阳光斜照，她举起相机对镜自拍，中景",
        "街角屋檐下，一只黑猫蹲在窗台，近景",
        "",
        "黄昏的公交站台，她抱着相册等车，全景",
        "夜里路灯下，她回头看向镜头，近景",
        "",
    ]
    run9 = run_board("G9", "color", cells9, [])
    files9 = run9.get("files") or []
    check("九宫格出图成功", run9.get("state") == "done",
          json.dumps(run9.get("err") or {}, ensure_ascii=False)[:200])
    check("九宫格成品 1536×864",
          bool(files9) and files9[0].get("width") == 1536 and files9[0].get("height") == 864,
          f"{((files9 or [{}])[0]).get('width')}×{((files9 or [{}])[0]).get('height')}")
    check("九宫格归档到「宫格分镜故事板」",
          "宫格分镜故事板" in str(((files9 or [{}])[0]).get("path")))
    if files9:
        ok, detail = image_is_generated(files9[0]["path"])
        check("九宫格成品不是纯色空图", ok, detail)
        print(f"       成品：{files9[0]['path']}")

    # --- 2. 四宫格 · 彩色 · 1 张参考图（格子写「图 1」锁身份）
    print("\n=== 四宫格 · 彩色 · 1 张参考图 ===")
    ref_id = upload(REF_SRC) if REF_SRC.is_file() else ""
    check("参考图上传成功", bool(ref_id), ref_id or f"找不到 {REF_SRC}")
    cells4 = [
        "图 1 中的角色站在雨后的老街上，抬头看招牌，中景",
        "图 1 中的角色推开咖啡馆的门，侧身回头，中景",
        "",
        "",
    ]
    run4 = run_board("G4", "color", cells4, [ref_id] if ref_id else [])
    files4 = run4.get("files") or []
    check("带参考图出图成功", run4.get("state") == "done",
          json.dumps(run4.get("err") or {}, ensure_ascii=False)[:300])
    check("带参考图成品 1536×864",
          bool(files4) and files4[0].get("width") == 1536 and files4[0].get("height") == 864,
          f"{((files4 or [{}])[0]).get('width')}×{((files4 or [{}])[0]).get('height')}")
    if files4:
        ok, detail = image_is_generated(files4[0]["path"])
        check("带参考图成品不是纯色空图", ok, detail)
        print(f"       成品：{files4[0]['path']}")

    # --- 3. 六宫格 · 线稿 W3 · 2 张参考图（换画法 + 换参考图，和前两轮完全不同的输入）
    print("\n=== 六宫格 · 线稿 W3 · 2 张参考图 ===")
    ref_a = upload(REF_SRC) if REF_SRC.is_file() else ""
    ref_b = upload(REF_SRC2) if REF_SRC2.is_file() else ""
    check("两张参考图都上传成功", bool(ref_a) and bool(ref_b), f"{ref_a} / {ref_b}")
    cells6 = [
        "图 1 中的角色背着包走进车站大厅，全景",
        "图 1 中的角色在自动售票机前低头操作，中景",
        "图 2 中的建筑外观，雨夜霓虹招牌，全景",
        "图 1 中的角色站在月台上等车，侧身，中景",
        "图 2 中的列车进站，车头灯照亮雨丝，全景",
        "",
    ]
    run6 = run_board("G6", "line", cells6, [r for r in (ref_a, ref_b) if r], line_style="W3")
    files6 = run6.get("files") or []
    check("六宫格线稿出图成功", run6.get("state") == "done",
          json.dumps(run6.get("err") or {}, ensure_ascii=False)[:300])
    check("六宫格线稿成品 2304×864",
          bool(files6) and files6[0].get("width") == 2304 and files6[0].get("height") == 864,
          f"{((files6 or [{}])[0]).get('width')}×{((files6 or [{}])[0]).get('height')}")
    if files6:
        ok, detail = image_is_generated(files6[0]["path"])
        check("六宫格线稿成品不是纯色空图", ok, detail)
        print(f"       成品：{files6[0]['path']}")

    print(f"\n===== {PASS}/{PASS + FAIL} 项通过 =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
