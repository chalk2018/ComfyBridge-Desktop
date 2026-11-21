"""探针：窄屏下画布 DOM 到底还剩什么。"""
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


def call(ws, n, m, p=None):
    n[0] += 1
    ws.send(json.dumps({"id": n[0], "method": m, "params": p or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == n[0]:
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))
            return msg.get("result", {})


def main() -> None:
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.5",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={APP / '.cache' / 'edge_probe2'}", "--window-size=1440,1000", BASE + "/#canvas",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(80):
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
    ws = websocket.create_connection(ws_url, timeout=60, suppress_origin=True)
    n = [0]

    def js(e):
        r = call(ws, n, "Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return r.get("result", {}).get("value")

    call(ws, n, "Runtime.enable")
    for _ in range(60):
        if js("typeof CanvasModule!=='undefined' && document.querySelector('#cvsStage')"):
            break
        time.sleep(0.3)
    time.sleep(2.0)

    dump = ("(()=>{const r=document.querySelector('#cvsRoot');const s=document.querySelector('#cvsStage');"
            "const c=document.querySelector('.chat');const st=document.querySelector('.studio');"
            "const box=e=>e?[Math.round(e.getBoundingClientRect().width),Math.round(e.getBoundingClientRect().height)]:null;"
            "return {root:!!r,rootHTML:r?r.innerHTML.length:-1,stage:box(s),chat:box(c),studio:box(st),"
            "bar:box(document.querySelector('.cvs-bar')),nodes:document.querySelectorAll('.cnode').length,"
            "route:(typeof UI!=='undefined'?UI.route:'?'),mounted:(window.CanvasModule?CanvasModule.isMounted():null),"
            "cvsStyle:r?getComputedStyle(r).display+'/'+getComputedStyle(r).flex:null,"
            "studioDisp:st?getComputedStyle(st).display+'/'+getComputedStyle(st).flexDirection:null,"
            "chatFlex:c?getComputedStyle(c).flex+'|'+getComputedStyle(c).display+'|'+getComputedStyle(c).position:null,"
            "mq:matchMedia('(max-width:820px)').matches,"
            "kids:st?[...st.children].map(e=>[e.className,e.offsetHeight,getComputedStyle(e).position,"
            "getComputedStyle(e).flex]):null,"
            "chatH:c?[c.offsetHeight,c.clientHeight,getComputedStyle(c).height,getComputedStyle(c).maxHeight,"
            "getComputedStyle(c).overflow]:null}})()")
    print("1440", json.dumps(js(dump), ensure_ascii=False))
    for w, h in ((820, 900), (390, 844)):
        call(ws, n, "Emulation.setDeviceMetricsOverride",
             {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": True})
        time.sleep(1.5)
        print(w, json.dumps(js(dump), ensure_ascii=False))
    proc.terminate()


if __name__ == "__main__":
    main()
