"""视觉验收截图：桌面 + 手机两种视口，把改动涉及的页面各截一张。

用法：
    .venv\\Scripts\\python.exe tools/shot_ui.py
产物：_reference/ui_<名字>.png
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9271
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_ui"
OUT = APP / "_reference"
CHAR = ("二十五岁中国女子，清冷气质，长直黑发用竹簪高盘，细眉凤眼，穿素白交领长衫，"
        "腰系青色织带，脚踩黑布靴，腰侧挂一枚青玉玉佩")


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
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        return r.get("result", {}).get("value")

    def wait(self, e: str, t: float = 20.0) -> bool:
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
        path = OUT / f"ui_{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        print("saved", path.name)


def set_ui(cdp: C, js: str, pause: float = 1.2) -> None:
    cdp.js(js)
    time.sleep(pause)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "adminPage": "caps",
                                                 "capId": "sheet.character"}}, timeout=20)
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
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1060, "deviceScaleFactor": 1, "mobile": False})
    try:
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities", 25)
        time.sleep(1.0)

        # ---------------- 桌面 ----------------
        set_ui(cdp, "(()=>{UI.route='admin';UI.adminPage='caps';render();return true})()")
        cdp.shot("d_admin_caps")
        set_ui(cdp, "(()=>{UI.capId='image.generate';UI.adminPage='grades';UI.editingWfIndex=0;"
                    "UI.gOpen=1;render();return true})()")
        cdp.shot("d_admin_grades_image")
        set_ui(cdp, "(()=>{UI.capId='sheet.character';UI.adminPage='grades';UI.comboSel='C.line.lock';"
                    "UI.gOpen=0;render();return true})()", 1.8)
        cdp.shot("d_admin_grades_sheet")
        set_ui(cdp, "(()=>{UI.adminPage='sheet';render();return true})()", 1.0)
        cdp.shot("d_admin_sheet")
        set_ui(cdp, f"""(()=>{{UI.route='studio';UI.capId='sheet.character';UI._draftCap='sheet.character';
          UI.mode='i2i';UI.refs=[{{id:'shot1',kind:'image'}}];UI.sheetOpen=false;
          UI.sheetForm={{layout:'C',ink:'line',lineStyle:'W3',ref:'lock',grade:'默认',
          character:{json.dumps(CHAR, ensure_ascii=False)},style:'写实摄影',styleText:''}};
          render();return true}})()""", 1.4)
        cdp.shot("d_studio_sheet")

        # ---------------- 手机 ----------------
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        set_ui(cdp, "(()=>{UI.navOpen=false;UI.sheet=false;UI.route='studio';render();return true})()", 1.6)
        cdp.shot("m_studio")
        set_ui(cdp, "(()=>{document.querySelector('#navToggle').click();return true})()", 0.9)
        cdp.shot("m_studio_nav")
        set_ui(cdp, "(()=>{UI.navOpen=false;document.querySelector('.app').classList.remove('nav-open');"
                    "document.querySelector('#sheetToggle').click();return true})()", 0.9)
        cdp.shot("m_studio_sheet")
        set_ui(cdp, "(()=>{UI.sheet=false;document.querySelector('.app').classList.remove('sheet-open');"
                    "UI.route='admin';UI.adminPage='caps';render();return true})()", 1.4)
        cdp.shot("m_admin_caps")
        set_ui(cdp, "(()=>{document.querySelector('#navToggle').click();return true})()", 0.9)
        cdp.shot("m_admin_nav")
        set_ui(cdp, "(()=>{UI.navOpen=false;document.querySelector('.admin').classList.remove('nav-open');"
                    "UI.capId='image.generate';UI.adminPage='grades';UI.editingWfIndex=0;UI.gOpen=1;"
                    "render();return true})()", 1.6)
        cdp.shot("m_admin_grades_image")
        set_ui(cdp, "(()=>{UI.capId='sheet.character';UI.adminPage='grades';UI.comboSel='C.line.lock';"
                    "UI.gOpen=0;render();return true})()", 1.8)
        cdp.shot("m_admin_grades_sheet")
        set_ui(cdp, "(()=>{UI.adminPage='sheet';render();window.scrollTo(0,0);return true})()", 1.0)
        cdp.shot("m_admin_sheet")
        set_ui(cdp, "(()=>{const t=document.querySelector('.presets');if(t)t.scrollIntoView({block:'start'});"
                    "return true})()", 0.6)
        cdp.shot("m_admin_sheet_presets")
        set_ui(cdp, "(()=>{UI.route='gallery';render();return true})()", 0.9)
        cdp.shot("m_gallery")
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "sheet.character",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
