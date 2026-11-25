"""视觉验收：视频生成扩展的「参考模式」单选切换。"""
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
CDP_PORT = 9283
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_vemode"
OUT = APP / "_dev" / "shots"


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

    def wait(self, e: str, t: float = 30.0) -> bool:
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def shot(self, name: str) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path = OUT / f"{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        print("saved", path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "adminPage": "caps",
                                                 "capId": "video.ext"}}, timeout=20)
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
        except Exception:
            continue
    assert ws_url, "连不上 CDP"
    cdp = C(ws_url)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1060, "deviceScaleFactor": 1, "mobile": False})
    try:
        assert cdp.wait("typeof UI!=='undefined' && !!S && !!S.videoExt && S.videoExt.funcs.length", 35)
        cdp.js("(()=>{UI.route='studio';UI.capId='video.ext';"
               "UI.veFunc={id:'multiRefFast',grade:'8采480P',ratio:'16:9',mode:'fl2v'};"
               "UI.prompt='';UI.refs=[];render();return true})()")
        time.sleep(1.5)
        info = cdp.js("(()=>{const b=[...document.querySelectorAll('[data-vemode]')];"
                      "return {count:b.length, text:b.map(x=>x.textContent).join('|'),"
                      " on:b.filter(x=>x.classList.contains('on')).map(x=>x.textContent).join('|'),"
                      " meta:document.querySelector('.vdur-meta')?.textContent||''}})()")
        print("fl2v state:", json.dumps(info, ensure_ascii=False))
        assert info["count"] == 2 and info["on"] == "首尾帧生成", info
        cdp.shot("vemode_fl2v")

        cdp.js("(()=>{document.querySelector('[data-vemode=\"ref2v\"]').click();return true})()")
        time.sleep(1.2)
        info2 = cdp.js("(()=>{const b=[...document.querySelectorAll('[data-vemode]')];"
                       "return {on:b.filter(x=>x.classList.contains('on')).map(x=>x.textContent).join('|'),"
                       " meta:document.querySelector('.vdur-meta')?.textContent||'',"
                       " mode:(UI.veFunc||{}).mode}})()")
        print("ref2v state:", json.dumps(info2, ensure_ascii=False))
        assert info2["on"] == "参考生成" and info2["mode"] == "ref2v", info2
        cdp.shot("vemode_ref2v")
        print("VISUAL PASS")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
