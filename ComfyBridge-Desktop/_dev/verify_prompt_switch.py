"""验证：顶栏「提示词模板」开关 ⇄ 提示词速取窗，桌面 / 移动端。"""
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
SHOTS = APP / "_dev" / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)


class CDP:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=90, suppress_origin=True)
        self.seq = 0

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

    def js(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError("JS: " + str(r["exceptionDetails"])[:400])
        return r.get("result", {}).get("value")

    def wait(self, expr, timeout=20):
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

    def shot(self, name):
        import base64
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        p = SHOTS / f"{name}.png"
        p.write_bytes(base64.b64decode(data["data"]))
        return str(p)


def start(mobile):
    port = 9281 + (os.getpid() % 20)
    profile = APP / ".cache" / f"edge_switch_{port}_{int(time.time())}"
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


STATE = """(()=>{
  const q=s=>document.querySelector(s);
  const sw=q('#ptTopDockSwitch'), dock=q('.pt-dock'), label=q('#ptTopDockSwitch .pts-label');
  const kn=q('#ptTopDockSwitch .pts-knob svg');
  const rect=e=>{if(!e)return null;const b=e.getBoundingClientRect();return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)}};
  return {
    route: typeof UI!=='undefined'?UI.route:'?',
    uiDock: !!(typeof S!=='undefined'&&S.ui&&S.ui.promptDock),
    swExists: !!sw, swOn: sw?sw.classList.contains('on'):null,
    aria: sw?sw.getAttribute('aria-checked'):null,
    labelText: label?label.textContent.trim():null,
    labelVisible: label?getComputedStyle(label).display!=='none':null,
    knobSvgVisible: kn?getComputedStyle(kn).display!=='none':null,
    dockExists: !!dock, dockHidden: dock?dock.hidden:null,
    dockMin: !!q('[data-dock-min]'), dockClose: !!q('[data-dock-close]'),
    adminSwitch: !!q('#ptDockSwitch'),
    adminFloatText: document.body.innerText.includes('浮动小窗'),
    topbar: rect(q('.topbar')), swRect: rect(sw),
    vw: innerWidth, docSW: document.documentElement.scrollWidth, docCW: document.documentElement.clientWidth,
    topSW: q('.topbar')?q('.topbar').scrollWidth:null, topCW: q('.topbar')?q('.topbar').clientWidth:null,
  };
})()"""


def set_dock(cdp, on):
    cdp.js(f"window.PromptLib.setDockEnabled({str(on).lower()})")
    assert cdp.wait(f"!!(S.ui&&S.ui.promptDock)==={str(on).lower()}", 15), f"setDockEnabled({on}) 超时"


def run(mobile):
    tag = "mobile" if mobile else "desktop"
    proc, cdp = start(mobile)
    out = {"viewport": tag}
    try:
        cdp.js("try{localStorage.clear()}catch(e){}; location.hash='#studio'; location.reload();")
        assert cdp.wait("typeof UI!=='undefined' && UI.route==='studio' && !!document.querySelector('#ptTopDockSwitch')", 30), "页面/开关没起来"
        cdp.pause(600)
        # 统一从关闭态起步
        set_dock(cdp, False)
        cdp.pause(400)
        out["closed"] = cdp.js(STATE)
        out["shot_closed"] = cdp.shot(f"verify_{tag}_closed")

        # 点开关 → 弹出速取窗
        cdp.js("document.querySelector('#ptTopDockSwitch').click()")
        assert cdp.wait("!!(S.ui&&S.ui.promptDock)===true", 15), "点开关没打开"
        cdp.pause(500)
        out["opened"] = cdp.js(STATE)
        out["shot_open"] = cdp.shot(f"verify_{tag}_open")

        # 点最小化 → 隐藏速取窗 + 开关关闭
        cdp.js("document.querySelector('.pt-dock [data-dock-min]').click()")
        assert cdp.wait("!!(S.ui&&S.ui.promptDock)===false", 15), "点最小化没隐藏"
        cdp.pause(400)
        out["minimized"] = cdp.js(STATE)

        # 再点开关弹出，最后恢复关闭
        cdp.js("document.querySelector('#ptTopDockSwitch').click()")
        assert cdp.wait("!!(S.ui&&S.ui.promptDock)===true", 15), "二次点开关没弹出"
        cdp.pause(300)
        out["reopened"] = cdp.js(STATE)
        set_dock(cdp, False)

        # 管理台提示词模板库：旧开关必须不在
        cdp.js("UI.route='admin'; UI.adminPage='prompts'; render();")
        assert cdp.wait("!!document.querySelector('.pt-page')", 15), "没进提示词模板库"
        cdp.pause(300)
        out["admin"] = cdp.js(STATE)
        out["shot_admin"] = cdp.shot(f"verify_{tag}_admin")
        print(json.dumps(out, ensure_ascii=False, indent=1))
    finally:
        proc.terminate()


if __name__ == "__main__":
    run(len(sys.argv) > 1 and sys.argv[1] == "mobile")
