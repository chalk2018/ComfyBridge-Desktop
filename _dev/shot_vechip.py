"""视觉验收：视频生成扩展结果块头部显示宽高比 + 参考模式徽章。"""
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
CDP_PORT = 9287
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_vechip"
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
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.35",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1280,1500", BASE + "/#studio",
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
        except Exception:
            pass
        if ws_url:
            break
    if not ws_url:
        print("NO CDP"); proc.terminate(); return 1

    c = C(ws_url)
    c.wait("document.querySelector('#studio') || document.querySelector('.app')", 20)
    time.sleep(2)
    # 确认 state 里 video.ext 已渲染，结果卡头部含 vechip
    ok = c.wait("document.querySelectorAll('.job.done .head .vechip').length>0", 20)
    chips = c.js("JSON.stringify([...document.querySelectorAll('.vechip')].map(x=>x.textContent.trim()))")
    print("wait vechip:", ok)
    print("vechips:", chips)
    arr = json.loads(chips or "[]")
    assert any("宽高比" in x for x in arr), "没有宽高比徽章"
    assert any("参考模式" in x for x in arr), "没有参考模式徽章"
    assert any(x in ("首尾帧生成", "参考生成") or x.endswith("首尾帧生成") or x.endswith("参考生成")
               for x in arr), "参考模式没有中文名"
    # 回归：非 video.ext 的能力（图片生成）结果块不应出现这些徽章
    other = c.js("(()=>{const j={id:'fake',cap:'image.generate',prompt:'x',seed:1,t0:Date.now(),"
                 "runs:[{id:'r',grade:'默认',state:'done',t0:Date.now(),t1:Date.now(),files:[{kind:'image'}]}]};"
                 "return GenUI.jobCard(j,{}).indexOf('vechip')>=0})()")
    assert other is False, "图片生成结果块被误加了视频扩展徽章"
    print("other-cap no vechip: PASS")
    # 滚到第一条含完整徽章的结果卡再截图
    c.js("(()=>{const j=[...document.querySelectorAll('.job.done')].find(x=>x.querySelectorAll('.vechip').length>=2);"
         "if(j)j.scrollIntoView({block:'center'});return true})()")
    time.sleep(1)
    c.shot("vechip_result.png")
    proc.terminate()
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
