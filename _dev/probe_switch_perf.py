"""实测：移动端顶栏遮挡 + 提示词速取开关切换耗时/网络请求。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

sys.stdout.reconfigure(encoding="utf-8")
APP = Path(__file__).resolve().parents[1]
BASE = os.environ.get("SHOT_BASE", "http://127.0.0.1:8190")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=90, suppress_origin=True)
        self.seq = 0
        self.events = []

    def call(self, method, params=None):
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def js(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError("JS: " + str(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")

    def wait(self, expr, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def pause(self, ms=300):
        self.js(f"new Promise(r=>setTimeout(()=>r(1),{ms}))")


def start(mobile):
    port = 9311 + (os.getpid() % 20)
    profile = APP / ".cache" / f"edge_probe_{port}_{int(time.time())}"
    profile.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--disable-background-networking", "--disk-cache-size=1",
        "--remote-allow-origins=*", f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}", "--window-size=1440,1000", BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 40
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                for t in json.loads(resp.read().decode()):
                    if t.get("type") == "page" and BASE.rsplit(":", 1)[-1] in t.get("url", ""):
                        cdp = CDP(t["webSocketDebuggerUrl"])
                        cdp.call("Runtime.enable")
                        cdp.call("Page.enable")
                        cdp.call("Network.enable")
                        cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
                        if mobile:
                            cdp.call("Emulation.setDeviceMetricsOverride", {
                                "width": 390, "height": 844, "deviceScaleFactor": 2,
                                "mobile": True, "screenWidth": 390, "screenHeight": 844,
                            })
                            cdp.call("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
                        return proc, cdp
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("连不上 Edge")


TOP_PROBE = """(()=>{
  const q=s=>document.querySelector(s);
  const rect=e=>{if(!e)return null;const b=e.getBoundingClientRect();return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height),r:Math.round(b.right)}};
  const els={
    nav:q('#navToggle'), brand:q('.brand'), health:q('.health'),
    task:q('#sheetToggle'), sw:q('#ptTopDockSwitch'), history:q('[data-nav="gallery"]'), admin:q('.topbar [data-nav="admin"]')
  };
  const out={vw:innerWidth};
  for(const k in els) out[k]=rect(els[k]);
  const h=out.health;
  out.overlap = h?Object.entries(out).filter(([k,v])=>v&&v.x!==undefined&&k!=='health'&&k!=='vw'&&v.x<h.r&&v.r>h.x&&v.y<h.y+h.h&&v.y+v.h>h.y).map(([k])=>k):[];
  return out;
})()"""


def perf_probe(cdp):
    # 注入：hook fetch 记请求、hook setDockEnabled/syncDock/render 计时；只在测试期生效
    cdp.js("""(()=>{
      window.__perf={reqs:[],steps:[]};
      const of=window.fetch;
      window.fetch=function(u,o){const t0=performance.now();const url=String(u&&u.url||u);
        return of.apply(this,arguments).then(r=>{window.__perf.reqs.push({url:url.replace(location.origin,''),ms:Math.round(performance.now()-t0)});return r;});};
      const oldSet=window.PromptLib.setDockEnabled;
      window.PromptLib.setDockEnabled=async function(v){
        const t0=performance.now(); const r=await oldSet.apply(this,arguments);
        window.__perf.steps.push({name:'setDockEnabled',ms:Math.round(performance.now()-t0)}); return r;};
      ['syncDock','ensureLoaded'].forEach(n=>{const f=window.PromptLib[n];if(!f)return;
        window.PromptLib[n]=function(){const t0=performance.now();const r=f.apply(this,arguments);
          window.__perf.steps.push({name:n,ms:Math.round(performance.now()-t0)});return r;};});
      const os=window.syncDock;
    })()""")


def run(mobile):
    proc, cdp = start(mobile)
    tag = "mobile" if mobile else "desktop"
    out = {"viewport": tag}
    try:
        cdp.js("try{localStorage.clear()}catch(e){}; location.hash='#studio'; location.reload();")
        assert cdp.wait("typeof UI!=='undefined' && UI.route==='studio' && !!document.querySelector('#ptTopDockSwitch')", 30)
        cdp.pause(600)
        out["topbar"] = cdp.js(TOP_PROBE)

        # 先确保数据加载过（首次开启），再测「数据已加载后」的切换
        cdp.js("window.PromptLib.setDockEnabled(true)")
        cdp.wait("PromptLib._state.loaded===true", 25)
        cdp.pause(500)
        cdp.js("window.PromptLib.setDockEnabled(false)")
        cdp.wait("S.ui.promptDock===false", 15)
        cdp.pause(300)

        perf_probe(cdp)
        cdp.js("window.__perf.reqs=[]; window.__perf.steps=[]")
        t0 = time.time()
        cdp.js("document.querySelector('#ptTopDockSwitch').click()")
        cdp.wait("S.ui.promptDock===true", 15)
        wall = round((time.time() - t0) * 1000)
        cdp.pause(300)
        out["toggle_on"] = {"wall_ms": wall, **cdp.js("window.__perf")}
        out["loaded"] = cdp.js("PromptLib._state.loaded")

        cdp.js("window.PromptLib.setDockEnabled(false)")
        cdp.wait("S.ui.promptDock===false", 15)
        cdp.pause(200)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    finally:
        proc.terminate()


if __name__ == "__main__":
    run(len(sys.argv) > 1 and sys.argv[1] == "mobile")
