"""开发用：在手机视口下截创作台 / 管理台，检查两个新能力的移动端排版。

用法：
    .venv\\Scripts\\python.exe _dev\\ui_shot.py mobile
    .venv\\Scripts\\python.exe _dev\\ui_shot.py desktop
"""

from __future__ import annotations

import json
import os
import shutil
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

    def call(self, method: str, params: dict | None = None) -> dict:
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expression: str):
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError("JS: " + str(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    def wait(self, expr: str, timeout: float = 20.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(expr):
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def pause(self, ms: int = 300) -> None:
        self.js(f"new Promise(r=>setTimeout(()=>r(1),{ms}))")

    def shot(self, name: str) -> Path:
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        import base64
        path = SHOTS / f"{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        return path


def start(mobile: bool) -> tuple[subprocess.Popen, CDP]:
    port = 9261 + (os.getpid() % 20)
    # 不删旧 profile：清理目录会触发批量删除，直接每次用独立目录更省事。
    profile = APP / ".cache" / f"edge_ui_{port}_{int(time.time())}"
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


def pick(cdp: CDP, selector: str) -> bool:
    return bool(cdp.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)return false;e.click();return true}})()"))


def open_cap(cdp: CDP, cap: str) -> None:
    # 手机上能力列表在抽屉里，先点开菜单再选
    if cdp.js("!!document.querySelector('.drawer.open, .rail.nav-open')"):
        pass
    if not cdp.js(f"!!document.querySelector('.rail [data-cap={json.dumps(cap)}]')"):
        pick(cdp, "#navBtn") or pick(cdp, "[data-nav]")
        cdp.pause(400)
    if not cdp.js(f"UI.capId==={json.dumps(cap)}"):
        pick(cdp, f'.rail [data-cap="{cap}"]')
        cdp.pause(700)
    cdp.wait(f"UI.capId==={json.dumps(cap)} && !!document.querySelector('#extText')", 15)


def metrics() -> None:
    proc, cdp = start(True)
    try:
        cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')", 25)
        cdp.pause(600)
        probe = """(()=>{
          const r = el => { if(!el) return null; const b = el.getBoundingClientRect();
            return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height),
                    cw:el.clientWidth, sw:el.scrollWidth, ch:el.clientHeight, sh:el.scrollHeight,
                    ox:getComputedStyle(el).overflowX, oy:getComputedStyle(el).overflowY}; };
          const q = s => document.querySelector(s);
          const send = q('#sendBtn'), foot = q('.inputbox .foot'), box = q('.inputbox');
          return {
            vw: innerWidth, vh: innerHeight,
            doc: {sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth},
            composer: r(q('.composer')), inner: r(q('.composer-inner')), box: r(box),
            body: r(q('.sheetbody')), foot: r(foot), send: r(send),
            sendVisible: !!(send && send.getBoundingClientRect().right <= innerWidth + 1 && send.getBoundingClientRect().bottom <= innerHeight + 1),
            styleOfBox: box ? getComputedStyle(box).maxHeight : '',
            slots: [...document.querySelectorAll('.ext-slot')].map(e=>r(e)),
            segRows: [...document.querySelectorAll('.sheetbar')].map(e=>{
              const seg = e.querySelector('.seg'); return seg ? {lb:(e.querySelector('.slb')||{}).textContent, w:Math.round(seg.getBoundingClientRect().width), sw:seg.scrollWidth, h:Math.round(seg.getBoundingClientRect().height)} : null;
            }).filter(Boolean),
          };
        })()"""
        out = {}
        open_cap(cdp, "sheet.scene")
        pick(cdp, '[data-mode="expand"]')
        cdp.pause(700)
        out["scene_expand"] = cdp.js(probe)
        pick(cdp, '[data-mode="i2i"]')
        cdp.pause(600)
        out["scene_i2i"] = cdp.js(probe)
        open_cap(cdp, "sheet.prop")
        pick(cdp, '[data-mode="i2i"]')
        cdp.pause(600)
        out["prop_i2i"] = cdp.js(probe)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    finally:
        proc.terminate()


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "mobile"
    if mode == "metrics":
        metrics()
        return
    mobile = mode == "mobile"
    proc, cdp = start(mobile)
    try:
        # 浏览器可能从上次的 admin 路由恢复，先强制回创作台再截图
        cdp.js("try{localStorage.clear()}catch(e){}; location.hash='#studio'; location.reload();")
        assert cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.topbar') && UI.route==='studio'", 25), "页面没起来"
        cdp.pause(800)
        out = []
        cdp.shot(f"{mode}_00_home")

        if True:
            open_cap(cdp, "sheet.scene")
            for m in ("t2i", "i2i", "expand"):
                pick(cdp, f'[data-mode="{m}"]')
                cdp.pause(500)
                out.append((m, cdp.shot(f"{mode}_10_scene_{m}")))
            # 点开提示词面板，检查滚动区
            pick(cdp, "#extPvBtn")
            cdp.pause(900)
            out.append(("scene_pv", cdp.shot(f"{mode}_11_scene_expand_pv")))

            open_cap(cdp, "sheet.prop")
            for m in ("t2i", "i2i"):
                pick(cdp, f'[data-mode="{m}"]')
                cdp.pause(500)
                out.append((f"prop_{m}", cdp.shot(f"{mode}_20_prop_{m}")))

        # 管理台档位表
        pick(cdp, '.topbar [data-nav="admin"]')
        cdp.wait("UI.route==='admin' && !!document.querySelector('.admin-nav')", 20)
        for cap in ("sheet.scene", "sheet.prop"):
            pick(cdp, f'[data-grades-cap="{cap}"]')
            cdp.wait(f"UI.capId==={json.dumps(cap)} && !!document.querySelector('[data-combo-mode]')", 20)
            cdp.pause(900)
            out.append((cap, cdp.shot(f"{mode}_30_admin_{cap.split('.')[1]}")))

        errs = cdp.js("JSON.stringify((window.__errs||[]).slice(0,8))")
        print(json.dumps({"shots": [str(p) for _, p in out], "errors": errs}, ensure_ascii=False, indent=1))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
