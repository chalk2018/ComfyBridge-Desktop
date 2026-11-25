"""管理台画布页排查探针（一次性）。"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9288
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_probe_adm2"


class C:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
        self.n = 0
        self.logs: list[str] = []

    def call(self, m, p=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("method") in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown"):
                self.logs.append(json.dumps(msg.get("params", {}), ensure_ascii=False)[:300])
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
    print("CV_ADMIN before =", cdp.js("(()=>{try{return String(!!CV_ADMIN)}catch(e){return 'ERR '+e.message}})()"))
    cdp.js("(()=>{UI.route='admin';UI.adminPage='canvas';render();return 1})()")
    time.sleep(0.4)
    print("CV_ADMIN just after render =", cdp.js("(()=>{try{return JSON.stringify(CV_ADMIN)}catch(e){return 'ERR '+e.message}})()"))
    time.sleep(2.5)
    print("CV_ADMIN after 3s =", cdp.js("(()=>{try{return JSON.stringify(CV_ADMIN).slice(0,160)}catch(e){return 'ERR '+e.message}})()"))
    print("manual api chain =", cdp.js(
        "(async()=>{try{const a=await api('/api/canvas');const b=await api('/api/canvas/funcs');"
        "const v=await api('/api/video/modes');const u=await api('/api/audio/modes');"
        "return [a.ok,b.ok,v.ok,u.ok].join(',')}catch(e){return 'EXC '+e.message}})()"))
    print("console/exception logs:")
    for line in cdp.logs[-8:]:
        print("  ", line[:260])
    proc.terminate()


if __name__ == "__main__":
    main()
