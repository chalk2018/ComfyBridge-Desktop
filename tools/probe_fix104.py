"""1.04 改修回归：返回画布、任务栏整行选中与勾选对齐。

用法：
    .venv\\Scripts\\python.exe tools/probe_fix104.py
产物：_reference/fx_*.png
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_canvas_gen import BASE, CDP_PORT, EDGE, OUT, C, center  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / ".cache" / "edge_fix104"


def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.4",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page":
                    ws = t["webSocketDebuggerUrl"]
                    break
            if ws:
                break
        except Exception:  # noqa: BLE001
            continue
    cdp = C(ws)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    problems: list[str] = []

    def shot(name: str) -> None:
        data = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / f"fx_{name}.png").write_bytes(base64.b64decode(data["data"]))
        print("saved", f"fx_{name}.png")

    try:
        cdp.call("Page.navigate", {"url": BASE + "/#canvas"})
        time.sleep(4.5)
        if not cdp.wait("!!document.querySelector('.cvs-bar')", 30):
            problems.append("画布没起来")

        # ---- ③ 画布 → 管理台 → 返回，应该回到画布 ----
        cdp.js("(()=>{const b=document.querySelector('[data-nav=admin]');if(b)b.click();return !!b})()")
        time.sleep(2.0)
        route_admin = cdp.js("(()=>{const b=document.querySelector('[data-nav=canvas],[data-nav=studio]');"
                             "return b?[b.textContent.trim(),b.dataset.nav]:null})()")
        print("管理台上的返回按钮 =", route_admin)
        if not route_admin or "画布" not in route_admin[0]:
            problems.append(f"从画布进管理台后，返回按钮不是「返回画布」（{route_admin}）")
        shot("a_admin_back")
        if route_admin:
            cdp.js("(()=>{const b=document.querySelector('[data-nav=%s]');if(b)b.click();return 1})()" % route_admin[1])
            time.sleep(2.0)
            route_now = cdp.js("UI.route")
            print("点返回后 route =", route_now)
            if route_now != "canvas":
                problems.append(f"返回后没回到画布（route={route_now}）")
            shot("b_back_canvas")

        # ---- ⑥ 任务栏：勾选框与文字对齐 + 整行点击选中 ----
        cdp.js("(()=>{const b=document.querySelector('[data-nav=studio]');if(b)b.click();return 1})()")
        time.sleep(2.5)
        if not cdp.wait("!!document.querySelector('[data-taskrow]')", 20):
            problems.append("任务栏里没有任务行（先跑一次生成再来测）")
        else:
            geo = cdp.js("(()=>{const row=document.querySelector('[data-taskrow]');"
                         "const cb=row.querySelector('.tsel');const t=row.querySelector('.ti-top');"
                         "const nm=row.querySelector('.t');"
                         "const a=cb.getBoundingClientRect(),b=t.getBoundingClientRect(),c=nm.getBoundingClientRect();"
                         "return [Math.round(a.top+a.height/2),Math.round(c.top+c.height/2),"
                         "Math.round(a.height),Math.round(b.height)]})()")
            print("勾选框中心/文字中心/勾选框高/行高 =", geo)
            if abs(geo[0] - geo[1]) > 3:
                problems.append(f"勾选框和任务文字块没对齐（相差 {abs(geo[0]-geo[1])}px）")
            n0 = cdp.js("UI.selTasks.size")
            row = center(cdp, "[data-taskrow]")
            # 点行的右侧空白处（不碰删除/定位/勾选框）
            box = cdp.js("(()=>{const r=document.querySelector('[data-taskrow] .t').getBoundingClientRect();"
                         "return [r.right-6,r.top+r.height/2]})()")
            cdp.click_at(float(box[0]), float(box[1]))
            time.sleep(0.8)
            n1 = cdp.js("UI.selTasks.size")
            print("选中数 =", n0, "->", n1)
            if n1 == n0:
                problems.append("点任务行的空白处没有选中")
            shot("c_taskrow")
            # 点删除不应该顺带选中另一条
            cdp.js("UI.selTasks.clear();refreshDrawer();")
            time.sleep(0.4)
            before = cdp.js("(()=>{const r=document.querySelector('[data-taskrow]');"
                            "const b=r.querySelector('[data-jobdel]');return [!!b,UI.selTasks.size]})()")
            print("删除按钮/选中数 =", before)
            if not before[0]:
                problems.append("任务行里没有删除按钮")
    finally:
        try:
            cdp.ws.close()
        except Exception:  # noqa: BLE001
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()

    print("\n问题：", "无" if not problems else "")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
