"""冷启动实测：页面刷新后第一次点开开关，各步骤耗时 + 网络请求。"""
from __future__ import annotations

import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
import websocket

sys.stdout.reconfigure(encoding="utf-8")
APP = Path(__file__).resolve().parents[1]
BASE = os.environ.get("SHOT_BASE", "http://127.0.0.1:8190")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=120, suppress_origin=True)
        self.seq = 0
    def call(self, m, p=None):
        self.seq += 1; mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg: raise RuntimeError(f"{m}: {msg['error']}")
                return msg.get("result", {})
    def js(self, e):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r: raise RuntimeError("JS:" + str(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")
    def wait(self, e, timeout=40):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(e): return True
            except Exception: pass
            time.sleep(0.2)
        return False
    def pause(self, ms=200): self.js(f"new Promise(r=>setTimeout(r(1),{ms}))")


def start():
    port = 9331 + (os.getpid() % 20)
    prof = APP / ".cache" / f"edge_cold_{port}_{int(time.time())}"; prof.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--disable-background-networking", "--disk-cache-size=1", "--remote-allow-origins=*",
        f"--remote-debugging-port={port}", f"--user-data-dir={prof}", "--window-size=1440,1000", BASE + "/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 40
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                for t in json.loads(r.read().decode()):
                    if t.get("type") == "page" and BASE.rsplit(":", 1)[-1] in t.get("url", ""):
                        c = CDP(t["webSocketDebuggerUrl"]); c.call("Runtime.enable"); c.call("Page.enable")
                        return proc, c
        except Exception: pass
        time.sleep(0.5)
    proc.terminate(); raise RuntimeError("连不上 Edge")


proc, cdp = start()
out = {}
try:
    # 从关闭状态刷新，保证 PT.loaded=false
    cdp.js("fetch('/api/ui',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({patch:{promptDock:false}})})")
    cdp.pause(300)
    cdp.js("location.reload()")
    assert cdp.wait("typeof UI!=='undefined' && !!document.querySelector('#ptTopDockSwitch')", 40)
    cdp.pause(400)
    out["before"] = {"loaded": cdp.js("PromptLib._state.loaded"), "uiDock": cdp.js("S.ui.promptDock")}

    # 注入细粒度计时：hook fetch + 覆盖 setDockEnabled 内部调用链无法直接拆，
    # 改为监听 fetch 完成 + Long Task + 首帧
    cdp.js("""(()=>{
      window.__m={req:[]}; const of=window.fetch;
      window.fetch=function(u,o){const t=performance.now(),url=String(u&&u.url||u);
        return of.apply(this,arguments).then(r=>{window.__m.req.push({u:url.replace(location.origin,''),ms:Math.round(performance.now()-t)});return r;});};
      window.__lt=[]; try{new PerformanceObserver(l=>l.getEntries().forEach(e=>window.__lt.push(Math.round(e.duration)))).observe({entryTypes:['longtask']});}catch(e){}
    })()""")

    # 真正点一次开关（模拟用户）
    t0 = time.time()
    cdp.js("document.querySelector('#ptTopDockSwitch').click()")
    cdp.wait("S.ui.promptDock===true", 40)
    t_click_to_state = round((time.time()-t0)*1000)
    # 等数据加载与列表渲染完成
    cdp.wait("PromptLib._state.loaded===true", 40)
    cdp.pause(800)
    t_total = round((time.time()-t0)*1000)
    out["first_open"] = {
        "click_to_state_ms": t_click_to_state,
        "total_ms": t_total,
        "reqs": cdp.js("window.__m.req"),
        "longtasks_ms": cdp.js("window.__lt.slice(0,20)"),
        "dockCards": cdp.js("document.querySelectorAll('.pt-dock .pt-dcard').length"),
        "loaded": cdp.js("PromptLib._state.loaded"),
    }

    # 第二次：关掉再开（数据已加载），测是否还重新拉数据/重渲染
    cdp.js("PromptLib.setDockEnabled(false)"); cdp.wait("S.ui.promptDock===false", 15); cdp.pause(200)
    cdp.js("window.__m.req=[]; window.__lt=[]")
    t0 = time.time()
    cdp.js("document.querySelector('#ptTopDockSwitch').click()")
    cdp.wait("S.ui.promptDock===true", 15)
    cdp.pause(500)
    out["second_open"] = {
        "total_ms": round((time.time()-t0)*1000),
        "reqs": cdp.js("window.__m.req"),
        "longtasks_ms": cdp.js("window.__lt.slice(0,20)"),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
finally:
    proc.terminate()
    # 恢复开启
    urllib.request.urlopen(urllib.request.Request(BASE+"/api/ui",
        data=json.dumps({"patch":{"promptDock":True}}).encode(), headers={"Content-Type":"application/json"}, method="POST"))
