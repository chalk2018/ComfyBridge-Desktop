"""临时探针：看管理台画布页为什么一直停在读取中。"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9286
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_probe_adm"


class C:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
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
            return "ERR " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400]
        return r.get("result", {}).get("value")


def main():
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", BASE + "/#studio",
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
    cdp = C(ws_url)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    time.sleep(3)
    print("typeof S =", cdp.js("typeof S"))
    print("typeof cvLoadAdmin =", cdp.js("typeof cvLoadAdmin"))
    print("typeof pageCanvasAdmin =", cdp.js("typeof pageCanvasAdmin"))
    print("cvAdmin =", cdp.js("(()=>{try{return String(!!S.cvAdmin)}catch(e){return 'ERR '+e.message}})()"))
    print("cvLoadAdmin src =", cdp.js("String(cvLoadAdmin).slice(0,120)"))
    cdp.js("(()=>{UI.route='admin';UI.adminPage='canvas';render();return 1})()")
    time.sleep(3)
    print("after:", cdp.js("(()=>{try{return JSON.stringify(S.cvAdmin).slice(0,200)}catch(e){return 'x'}})()"))
    print("funcs:", cdp.js("(()=>{try{return (S.cvAdmin&&(S.cvAdmin.funcs||[]).length)+''}catch(e){return 'x'}})()"))
    print("手动 fetch funcs:", cdp.js(
        "fetch('/api/canvas/funcs').then(r=>r.text()).then(t=>t.slice(0,160))"))
    print("手动 fetch video/modes:", cdp.js(
        "fetch('/api/video/modes').then(r=>r.text()).then(t=>t.slice(0,120))"))
    print("手动 fetch video grades:", cdp.js(
        "fetch('/api/video/grades/ref2v').then(r=>r.text()).then(t=>t.slice(0,120))"))
    print("手动 fetch audio grades:", cdp.js(
        "fetch('/api/audio/grades/tts').then(r=>r.text()).then(t=>t.slice(0,160))"))
    print("DOM head:", cdp.js("document.querySelector('.admin-main')?document.querySelector('.admin-main').innerText.slice(0,160):'none'"))
    proc.terminate()


if __name__ == "__main__":
    main()
