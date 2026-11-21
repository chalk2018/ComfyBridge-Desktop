"""管理台「视频生成画布」1.04 验收：子功能绑定 + 视频四模式档位 + 音频四功能档位。

用法：
    .venv\\Scripts\\python.exe tools\\shot_admin_canvas.py
产物：_reference/m_adm_*.png
"""

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
CDP_PORT = 9284
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_adm"
OUT = APP / "_reference"


class C:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
        self.n = 0

    def call(self, m: str, p: dict | None = None) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result", {})

    def js(self, e: str):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return r.get("result", {}).get("value")

    def wait(self, e: str, t: float = 25.0) -> bool:
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        return False

    def shot(self, name: str, full: bool = False) -> None:
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": 1440, "height": 2600, "deviceScaleFactor": 1, "mobile": False}) if full else None
        time.sleep(0.5)
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": full})
        path = OUT / f"m_adm_{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        print("saved", path.name)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.4",
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
        except Exception:  # noqa: BLE001
            continue
    assert ws_url, "连不上 CDP"
    cdp = C(ws_url)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    problems: list[str] = []
    try:
        # 明确导航一次再等，避免连到 Edge 的中转页/上一个上下文
        cdp.call("Page.navigate", {"url": BASE + "/#studio"})
        time.sleep(3.0)
        if not cdp.wait("typeof UI!=='undefined' && !!S", 40):
            problems.append("页面没起来（UI 未定义）")
        cdp.js("window.__errs=[];window.addEventListener('error',e=>window.__errs.push(e.message))")
        time.sleep(1.0)

        # 走真实路径：先点「管理台」，再点左侧「视频生成画布」——导航会把 adminPage 存到服务端，
        # 直接改 UI.adminPage 会被每秒轮询盖回来（真人操作不会）
        cdp.js("(()=>{const b=document.querySelector('[data-nav=admin]');if(b)b.click();return !!b})()")
        time.sleep(1.2)
        cdp.js("(()=>{const b=document.querySelector('[data-page=canvas]');if(b)b.click();return !!b})()")
        time.sleep(1.0)
        ok = cdp.wait("!!document.querySelector('[data-cvf-save]')", 20)
        if not ok:
            problems.append("管理台画布页没渲染出子功能行")
            print("DOM =", cdp.js("(document.querySelector('.admin-main')||{}).innerText?.slice(0,300)"))
            print("errs =", cdp.js("(window.__errs||[]).join(' | ')"))
            print("CV_ADMIN keys =", cdp.js("(()=>{try{return Object.keys(CV_ADMIN||{}).join(',')}catch(e){return 'ERR '+e.message}})()"))
            print("DOM =", cdp.js("(document.querySelector('.admin-main')||{}).innerText?.slice(0,300)"))
            print("errs =", cdp.js("(window.__errs||[]).join(' | ')"))
            print("CV_ADMIN keys =", cdp.js("(()=>{try{return Object.keys(CV_ADMIN||{}).join(',')}catch(e){return 'ERR '+e.message}})()"))
        time.sleep(1.2)
        cdp.shot("a_top")

        # 子功能 + 视频档位齐全
        n = cdp.js("(()=>({func:document.querySelectorAll('[data-cvf-save]').length,"
                   "vtab:document.querySelectorAll('[data-vr-save]').length,"
                   "atab:document.querySelectorAll('[data-ar-save]').length,"
                   "vrows:document.querySelectorAll('[data-vr]').length,"
                   "arows:document.querySelectorAll('[data-ar]').length}))()")
        print("控件统计", n)
        if n["func"] != 2:
            problems.append(f"子功能行应为 2 个，实际 {n['func']}")
        if n["vtab"] != 4:
            problems.append(f"视频档位表应为 4 个模式，实际 {n['vtab']}")
        if n["atab"] != 4:
            problems.append(f"音频档位表应为 4 个功能，实际 {n['atab']}")

        # 滚动到视频档位
        cdp.js("(()=>{const e=document.querySelector('[data-vr-save]');if(e)e.scrollIntoView({block:'center'});return 1})()")
        time.sleep(0.6)
        cdp.shot("b_video")

        # 改一档步数 → 点保存 → 服务端应生效。
        # 先把原值记下来，验完按原值写回去，别把测试改动留在配置里。
        orig = cdp.js("(()=>{const r=(CV_ADMIN.vGrades.ref2v.rows||[]).find(g=>g.name==='快速');return r?String(r.steps):''})()")
        print("快速档原步数 =", orig)
        cdp.js('(()=>{const s=document.querySelector(\'[data-vr="steps|ref2v|1"]\');if(s){s.value="20";s.dispatchEvent(new Event("change"));}return !!s})()')
        time.sleep(0.3)
        cdp.js("(()=>{document.querySelector('[data-vr-save=ref2v]').click();return 1})()")
        time.sleep(1.6)
        got = cdp.js("(()=>{const r=(CV_ADMIN.vGrades.ref2v.rows||[]).find(g=>g.name==='快速');return r?r.steps:null})()")
        print("ref2v 快速档步数 =", got)
        if got != 20:
            problems.append(f"视频档位保存没生效（快速 = {got}）")
        # 写回原值
        cdp.js('(()=>{const s=document.querySelector(\'[data-vr="steps|ref2v|1"]\');if(s){s.value="%s";s.dispatchEvent(new Event("change"));}'
               'document.querySelector("[data-vr-save=ref2v]").click();return 1})()' % (orig or "4"))
        time.sleep(1.4)

        # 音频档位
        cdp.js("(()=>{const e=document.querySelector('[data-ar-save]');if(e)e.scrollIntoView({block:'start'});return 1})()")
        time.sleep(0.8)
        cdp.shot("c_audio")

        # 窄屏
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
        cdp.js("(()=>{const b=document.querySelector('[data-page=canvas]');if(b)b.click();return !!b})()")
        time.sleep(1.6)
        over = cdp.js("(()=>{const d=document.documentElement;return d.scrollWidth-d.clientWidth})()")
        print("390 横向溢出", over)
        if over and over > 2:
            problems.append(f"窄屏横向溢出 {over}px")
        cdp.shot("d_w390")

        errs = cdp.js("(window.__errs||[]).join(' | ')")
        if errs:
            problems.append("页面报错：" + errs)
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    print("\n问题：", problems or "无")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
