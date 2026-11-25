"""视觉验收：需求文档 1.08 的变更记录与 §13.7 / §13.8 新增小节能正常渲染。"""
from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

DOC = r"D:\workspace\WorkBuddyProject\ComfyBridge-需求与功能文档-1.08.html"
CDP_PORT = 9288
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = Path(r"D:\workspace\WorkBuddyProject\ComfyBridge-Desktop\.cache\edge_shot_doc108")
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
    assert "1.08" in title, "标题版本不对"

    # 1) 1.08 变更记录：定位它的 h2，滚过去截图
    has = c.js("(()=>{const hs=[...document.querySelectorAll('h2')];"
               "const h=hs.find(x=>x.textContent.trim()==='1.08 改了什么');"
               "if(!h)return false;h.scrollIntoView({behavior:'instant',block:'start'});return true})()")
    assert has, "找不到 1.08 改了什么章节"
    time.sleep(2)
    c.shot("doc108_changelog.png")

    # 2) §13.7 / §13.8 小节渲染 + 表格数 + 关键文字
    info = c.js("({has137:document.body.innerText.includes('13.7 「基于上一条」带回什么'),"
                " has138:document.body.innerText.includes('13.8 结果块上的参数徽章'),"
                " hasBadge:document.body.innerText.includes('宽高比 16:9'),"
                " tables:document.querySelectorAll('h2#sec-videoext ~ table').length})")
    print("§13 extra:", json.dumps(info, ensure_ascii=False))
    assert info["has137"] and info["has138"] and info["hasBadge"], "13.7/13.8 或徽章样例缺失"
    assert info["tables"] >= 4, "§13 表格数不足"

    # 滚到 13.7 小节截图
    c.js("(()=>{const h=[...document.querySelectorAll('h3')].find(x=>x.textContent.includes('13.7 '));"
         "if(h)h.scrollIntoView({behavior:'instant',block:'start'});return true})()")
    time.sleep(2)
    c.shot("doc108_section137.png")
    proc.terminate()
    print("DOC 1.08 VISUAL PASS")


if __name__ == "__main__":
    main()
