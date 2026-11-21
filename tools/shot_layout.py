"""1.04 布局验收：折叠、任务栏、图片生成画法/风格、窗口缩放不崩。

用法：
    .venv\\Scripts\\python.exe tools\\shot_layout.py
产物：_reference/m_<名字>.png
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9282
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_layout"
OUT = APP / "_reference"


class C:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
        self.n = 0

    def call(self, m: str, p: dict | None = None) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result", {})

    def js(self, e: str):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return r.get("result", {}).get("value")

    def wait(self, e: str, t: float = 25.0) -> bool:
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        return False

    def shot(self, name: str) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path = OUT / f"m_{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        print("saved", path.name)


def size(cdp: C, w: int, h: int, mobile: bool = False) -> None:
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})
    time.sleep(0.8)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.5",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1060", BASE + "/#studio",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page" and "8190" in t.get("url", ""):
                    ws_url = t["webSocketDebuggerUrl"]
                    break
            if ws_url:
                break
        except Exception:  # noqa: BLE001
            continue
    assert ws_url, "连不上 CDP"
    cdp = C(ws_url)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    size(cdp, 1440, 1060)
    problems: list[str] = []
    try:
        cdp.wait("typeof UI!=='undefined' && !!S", 30)
        time.sleep(1.0)

        # 图片生成：画法 + 风格
        cdp.js("(()=>{UI.route='studio';UI.capId='image.generate';UI.prompt='一位宋代文人站在江边';UI.railMin=false;UI.drawerMin=false;render();return true})()")
        time.sleep(1.0)
        cdp.shot("i_style")
        cdp.js("(()=>{UI.mode='i2i';UI.refs=[{id:'x',kind:'image'}];UI.prompt='改成水墨';render();return true})()")
        time.sleep(0.8)
        cdp.shot("i_style_i2i")

        # 折叠：左栏 + 右栏
        cdp.js("(()=>{UI.mode='t2i';UI.railMin=true;UI.drawerMin=true;render();return true})()")
        time.sleep(1.0)
        cdp.shot("l_collapsed")
        cdp.js("(()=>{UI.railMin=false;UI.drawerMin=false;render();return true})()")
        time.sleep(0.8)

        # 任务栏：整行选中 + 对齐
        cdp.js("(()=>{UI.capId='video.generate';UI.video={mode:'t2v'};render();return true})()")
        time.sleep(1.0)
        cdp.shot("l_taskbar")
        cdp.js("(()=>{const el=document.querySelector('[data-taskrow]');if(el)el.click();return !!el})()")
        time.sleep(0.8)
        cdp.shot("l_taskbar_sel")

        # 画布 → 管理台 → 返回（应回到画布）
        cdp.js("(()=>{UI.route='canvas';render();return true})()")
        time.sleep(2.0)
        cdp.shot("l_canvas")
        cdp.js("(()=>{document.querySelector('[data-nav=admin]').click();return true})()")
        time.sleep(1.5)
        cdp.shot("l_admin_from_canvas")
        cdp.js("(()=>{const bs=[...document.querySelectorAll('[data-nav]')];bs[bs.length-1].click();return true})()")
        time.sleep(1.5)
        back = cdp.js("UI.route")
        print("返回后 route =", back)
        cdp.shot("l_back_from_admin")
        if back != 'canvas':
            problems.append(f"从画布进管理台再返回，落在了 {back}")

        # 窗口缩放：宽到窄
        for w, h, tag, mob in ((1280, 900, "w1280", False), (1024, 768, "w1024", False),
                               (820, 900, "w820", True), (390, 844, "w390", True)):
            size(cdp, w, h, mob)
            cdp.js("(()=>{UI.route='studio';UI.capId='video.generate';render();return true})()")
            time.sleep(1.0)
            cdp.shot(f"r_{tag}")
            # 横向溢出检查
            over = cdp.js("(()=>{const d=document.documentElement;return d.scrollWidth - d.clientWidth})()")
            print(tag, "横向溢出", over)
            if over and over > 2:
                problems.append(f"{tag} 横向溢出 {over}px")
        msg = cdp.js("(window.__errs||[]).join(' | ')")
        if msg:
            problems.append(f"页面报错：{msg}")
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    print("\n问题：", problems or "无")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
