"""定位：CV_ADMIN 有数据但页面仍停在 loading。"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9290
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_probe_adm3"


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
            return "THROW " + str(r["exceptionDetails"].get("exception", {}).get("description", ""))[:200]
        return r.get("result", {}).get("value")


def main():
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page":
                    ws_url = t["webSocketDebuggerUrl"]
                    break
            if ws_url:
                break
        except Exception:
            continue
    cdp = C(ws_url)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Page.navigate", {"url": BASE + "/#studio"})
    time.sleep(4)
    print("ui?", cdp.js("typeof UI"), "cv?", cdp.js("typeof cvLoadAdmin"))
    cdp.js("window.__log=[];window.__mark=(s)=>window.__log.push(s)")
    cdp.js("(()=>{const _r=render;window.render=function(){window.__mark('render?route='+UI.route+' page='+UI.adminPage+' loading='+(CV_ADMIN&&CV_ADMIN.loading));return _r.apply(this,arguments)}})()")
    cdp.js("(()=>{UI.route='admin';UI.adminPage='canvas';render();return 1})()")
    time.sleep(3)
    print("log =", cdp.js("(window.__log||[]).join(' || ')"))
    print("CV loading =", cdp.js("(()=>{try{return String(CV_ADMIN&&CV_ADMIN.loading)}catch(e){return 'ERR'}})()"))
    print("has funcs =", cdp.js("(()=>{try{return (CV_ADMIN.funcs||[]).length}catch(e){return 'ERR'}})()"))
    print("controls =", cdp.js("document.querySelectorAll('[data-cvf-save]').length"))
    print("pageCanvasAdmin head =", cdp.js("String(pageCanvasAdmin().slice(0,60))"))
    proc.terminate()


if __name__ == "__main__":
    main()
