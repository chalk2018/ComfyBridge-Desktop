"""临时探针：只验「拖标题栏移动节点」到底走没走对。"""
from __future__ import annotations

import base64
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
PROFILE = APP / ".cache" / "edge_probe"


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
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", BASE + "/#canvas",
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
    cid = [0]

    def js(e):
        r = call(ws, n, "Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return r.get("result", {}).get("value")

    def mouse(t, x, y, cc=1):
        call(ws, n, "Input.dispatchMouseEvent",
             {"type": t, "x": x, "y": y, "button": "left",
              "buttons": 0 if t == "mouseReleased" else 1, "clickCount": cc})

    call(ws, n, "Runtime.enable")
    call(ws, n, "Emulation.setDeviceMetricsOverride",
         {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    for _ in range(60):
        if js("typeof CanvasModule!=='undefined' && document.querySelector('#cvsStage')"):
            break
        time.sleep(0.3)
    time.sleep(2.0)

    pos = "(()=>[...document.querySelectorAll('.cnode')].map(e=>[e.dataset.id,e.dataset.kind,"
    pos += "Math.round(parseFloat(e.style.left)),Math.round(parseFloat(e.style.top)),"
    pos += "Math.round(e.getBoundingClientRect().left),Math.round(e.getBoundingClientRect().top)]))()"
    print("前：", js(pos))
    # 拖标题栏 +300,+120：要挑一个「真的露在最上面」的标题栏，被别的卡压住的拖不到
    b = js("(()=>{for(const c of document.querySelectorAll('.cnode')){"
           "const e=c.querySelector('.cnode-hd .nm');const r=e.getBoundingClientRect();"
           "const x=r.left+r.width/2,y=r.top+r.height/2;const hit=document.elementFromPoint(x,y);"
           "if(hit&&hit.closest('.cnode')===c&&hit.closest('.cnode-hd'))return [x,y,c.dataset.id];}"
           "return null})()")
    print("抓点", b)
    js("window.__ev={pointermove:0,pointerup:0,pointercancel:0,pointerdown:0};"
       "const s=document.querySelector('#cvsStage');"
       "['pointermove','pointerup','pointercancel','pointerdown'].forEach(k=>"
       "s.addEventListener(k,()=>{window.__ev[k]++}));")

    def run(b, dx, dy):
        mouse("mouseMoved", b[0], b[1])
        mouse("mousePressed", b[0], b[1])
        for i in range(1, 9):
            mouse("mouseMoved", b[0] + dx * i / 8, b[1] + dy * i / 8)
            time.sleep(0.03)
        mouse("mouseReleased", b[0] + dx, b[1] + dy)
        time.sleep(0.6)

    run(b, 300, 120)
    print("事件", js("window.__ev"))
    print("后：", js(pos))
    b2 = js("(()=>{for(const c of document.querySelectorAll('.cnode')){"
            "const e=c.querySelector('.cnode-hd .nm');const r=e.getBoundingClientRect();"
            "const x=r.left+r.width/2,y=r.top+r.height/2;const hit=document.elementFromPoint(x,y);"
            "if(hit&&hit.closest('.cnode')===c&&hit.closest('.cnode-hd'))return [x,y,c.dataset.id];}"
            "return null})()")
    print("抓点2", b2)
    js("window.__ev={pointermove:0,pointerup:0,pointercancel:0,pointerdown:0}")
    run(b2, -260, -60)
    print("事件2", js("window.__ev"))
    print("后2：", js(pos))
    print("视图", js("(()=>{const w=document.querySelector('#cvsWorld');return w.style.transform})()"))
    proc.terminate()


if __name__ == "__main__":
    main()
