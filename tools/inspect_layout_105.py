"""临时调试：读取 1.05 页面关键容器的实际位置/样式。"""
from __future__ import annotations
import json, subprocess, time, urllib.request
from pathlib import Path
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
PORT = 9353
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_layout_105"


def main():
    PROFILE.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
                          "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1",
                          "--remote-allow-origins=*", f"--remote-debugging-port={PORT}",
                          f"--user-data-dir={PROFILE}", "--window-size=1440,1060", BASE + "/#studio"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        for _ in range(60):
            time.sleep(0.4)
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2).read())
                for t in tabs:
                    if t.get("type") == "page" and "8190" in t.get("url", ""):
                        ws = t["webSocketDebuggerUrl"]
                        break
                if ws:
                    break
            except Exception:
                pass
        assert ws, "no CDP"
        c = websocket.create_connection(ws, timeout=60, suppress_origin=True)
        n = 0

        def call(method, params=None):
            nonlocal n
            n += 1
            mid = n
            c.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(c.recv())
                if msg.get("id") == mid:
                    return msg.get("result", {})

        def js(expr):
            r = call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
            if "exceptionDetails" in r:
                raise RuntimeError(json.dumps(r, ensure_ascii=False)[:1000])
            return r.get("result", {}).get("value")

        call("Runtime.enable"); call("Page.enable")
        call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1060, "deviceScaleFactor": 1, "mobile": False})
        for _ in range(80):
            time.sleep(0.25)
            try:
                if js("typeof UI!=='undefined' && !!S && !!S.jobs"):
                    break
            except Exception:
                pass
        js("(()=>{UI.route='studio';UI.capId='image.generate';UI.railMin=false;UI.drawerMin=false;render();return true})()")
        time.sleep(1)
        out = js("""(()=>{
          const names=['.app','.topbar','.configerr','.studio','.rail','.chat','.drawer','.admin-nav','.admin-body'];
          const o={vw:innerWidth,vh:innerHeight,scrollY:scrollY,bodyBg:getComputedStyle(document.body).backgroundColor};
          names.forEach(s=>{const e=document.querySelector(s);if(e){const r=e.getBoundingClientRect(),cs=getComputedStyle(e);
            o[s]={x:r.x,y:r.y,w:r.width,h:r.height,pos:cs.position,top:cs.top,bg:cs.backgroundColor,align:cs.alignSelf,minH:cs.minHeight,maxH:cs.maxHeight,height:cs.height};}});
          const at=(x,y)=>{const e=document.elementFromPoint(x,y);return e?e.className||e.tagName:'';};
          o.at60=at(5,60);o.at80=at(5,80);o.at120=at(5,120);o.at700=at(5,700);
          return o;
        })()""")
        print(json.dumps(out, ensure_ascii=False, indent=2))
    finally:
        p.terminate()


if __name__ == "__main__":
    main()
