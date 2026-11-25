"""视觉验收：需求文档 1.07 的变更记录与 §13 章节能正常渲染。"""
from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

DOC = r"D:\workspace\WorkBuddyProject\ComfyBridge-需求与功能文档-1.06.html"
CDP_PORT = 9285
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = Path(r"D:\workspace\WorkBuddyProject\ComfyBridge-Desktop\.cache\edge_shot_doc107")
OUT = Path(r"D:\workspace\WorkBuddyProject\ComfyBridge-Desktop\_dev\shots")


class C:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=90, suppress_origin=True)
        self.n = 0

    def call(self, m, p=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})

    def js(self, e):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:500])
        return r.get("result", {}).get("value")

    def shot(self, name):
        d = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        p = OUT / name
        p.write_bytes(base64.b64decode(d["data"]))
        print("saved", p)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1400,1000",
        Path(DOC).as_uri(),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    for _ in range(90):
        time.sleep(0.5)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page":
                    ws = t["webSocketDebuggerUrl"]
                    break
            if ws:
                break
        except Exception:
            pass
    assert ws, "CDP 连不上"
    c = C(ws)
    c.call("Runtime.enable")
    c.call("Page.enable")
    end = time.time() + 60
    while time.time() < end:
        if c.js("!!document.querySelector('#sec-videoext')"):
            break
        time.sleep(1)
    title = c.js("document.title")
    print("title:", title)
    assert "1.07" in title
    c.js("document.querySelector('h1.title').scrollIntoView({behavior:'instant'});true")
    time.sleep(2)
    c.shot("doc107_cover.png")
    c.js("document.querySelector('#sec-videoext').scrollIntoView({behavior:'instant',block:'start'});true")
    time.sleep(3)
    c.shot("doc107_section13.png")
    # 表格是 h2 的后续兄弟节点，不是子节点
    info = c.js("({tables:document.querySelectorAll('h2#sec-videoext ~ table').length,"
                " has13:document.body.innerText.includes('13.3 参考模式与模型对应'),"
                " text:document.querySelector('#sec-videoext').innerText.slice(0,80)})")
    print("§13:", json.dumps(info, ensure_ascii=False))
    assert info["tables"] >= 3 and info["has13"]
    proc.terminate()
    print("DOC VISUAL PASS")


if __name__ == "__main__":
    main()
