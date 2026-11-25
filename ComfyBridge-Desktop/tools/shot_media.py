"""1.04 视觉验收：视频生成 / 音频生成 / 布局折叠 的桌面与手机截图。

用法：
    .venv\\Scripts\\python.exe tools\\shot_media.py
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

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9281
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_media"
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


def set_ui(cdp: C, js: str, pause: float = 1.2) -> None:
    cdp.js(js)
    time.sleep(pause)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "adminPage": "caps",
                                                 "capId": "video.generate"}}, timeout=20)
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
    errs: list[str] = []
    try:
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.video", 30)
        time.sleep(1.2)
        print("video modes:", cdp.js("S.video.modes.map(m=>m.id+':'+m.grades.length).join(',')"))
        print("audio modes:", cdp.js("S.audio.modes.map(m=>m.id).join(',')"))

        set_ui(cdp, "(()=>{UI.route='studio';UI.capId='video.generate';UI.video={mode:'t2v'};"
                    "UI.prompt='宋代文人在雨后的竹林小径上缓步前行，长镜头跟随';render();return true})()", 1.4)
        cdp.shot("v_t2v")

        set_ui(cdp, "(()=>{UI.video={mode:'ref2v'};UI.refs=[];render();return true})()", 1.0)
        cdp.shot("v_ref2v")

        set_ui(cdp, "(()=>{UI.video={mode:'board',grid:6,cells:['第1格 考场全景','第2格 苏轼答题','','','','','','','']};"
                    "render();return true})()", 1.0)
        cdp.shot("v_board")

        set_ui(cdp, "(()=>{UI.capId='audio.music';UI.audio={mode:'music',lyrics:'一蓑烟雨任平生'};"
                    "UI.prompt='古风民谣，古琴与笛子，男声吟唱';render();return true})()", 1.4)
        cdp.shot("a_music")

        set_ui(cdp, "(()=>{UI.capId='audio.tts';UI.audio={mode:'tts'};"
                    "UI.prompt='人生如逆旅，我亦是行人。';render();return true})()", 1.2)
        cdp.shot("a_tts")

        # 有成品时的结果卡（视频 / 音频播放器）
        set_ui(cdp, "(()=>{UI.capId='video.generate';UI.route='studio';render();return true})()", 1.2)
        cdp.shot("v_result")

        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        set_ui(cdp, "(()=>{UI.capId='video.generate';UI.video={mode:'ref2v'};UI.navOpen=false;render();return true})()", 1.6)
        cdp.shot("v_mobile")
        set_ui(cdp, "(()=>{UI.video={mode:'board',grid:4};render();return true})()", 1.0)
        cdp.shot("v_mobile_board")
        set_ui(cdp, "(()=>{UI.capId='audio.music';UI.audio={mode:'music'};render();return true})()", 1.2)
        cdp.shot("a_mobile")

        msg = cdp.js("(window.__errs||[]).join(' | ')")
        if msg:
            errs.append(str(msg))
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    if errs:
        print("页面报错：", errs)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
