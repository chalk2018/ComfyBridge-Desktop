"""画布真实出图：双击空白建「图片生成」节点 → 写提示词 → 点生成 → 等成品进节点。

用法：
    .venv\\Scripts\\python.exe tools/shot_canvas_gen.py
产物：_reference/cg_*.png
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
CDP_PORT = 9292
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_canvas_gen"
OUT = APP / "_reference"


class C:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=120, suppress_origin=True)
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
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:500])
        return r.get("result", {}).get("value")

    def wait(self, e: str, t: float = 30.0) -> bool:
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.4)
        return False

    def click_at(self, x: float, y: float) -> None:
        for t in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent",
                      {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
            time.sleep(0.05)

    def dbl(self, x: float, y: float) -> None:
        for i in (1, 2):
            self.call("Input.dispatchMouseEvent",
                      {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": i})
            self.call("Input.dispatchMouseEvent",
                      {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": i})
            time.sleep(0.05)

    def shot(self, name: str) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / f"cg_{name}.png").write_bytes(base64.b64decode(data["data"]))
        print("saved", f"cg_{name}.png")


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read())


def center(cdp: C, sel: str):
    box = cdp.js(f"(()=>{{const e=document.querySelector('{sel}');if(!e)return null;"
                 f"const r=e.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]}})()")
    return (float(box[0]), float(box[1])) if box else None


def main() -> int:
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.4",
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
        cdp.call("Page.navigate", {"url": BASE + "/#canvas"})
        time.sleep(3.5)
        if not cdp.wait("typeof CanvasModule!=='undefined'", 40):
            problems.append("画布没起来")
        if not cdp.wait("!!document.querySelector('#cvsStage')", 20):
            problems.append("画布舞台没出来")
        time.sleep(1.0)

        st = cdp.js("(()=>{const b=document.querySelector('#cvsStage').getBoundingClientRect();"
                    "return [b.left,b.top,b.width,b.height]})()")
        bx, by, bw, bh = [float(v) for v in st]
        # 找一块真正的空白：落在节点上双击只会选中节点，不会弹菜单
        blank = cdp.js("(()=>{const s=document.querySelector('#cvsStage').getBoundingClientRect();"
                       "for(let gy=3;gy<=9;gy++)for(let gx=2;gx<=9;gx++){"
                       "const x=s.left+s.width*gx/10,y=s.top+s.height*gy/10;"
                       "const e=document.elementFromPoint(x,y);"
                       "if(e&&!e.closest('.cnode')&&!e.closest('.cvs-zoom')&&!e.closest('.cvs-bar'))"
                       "return [x,y];}return null})()")
        if not blank:
            problems.append("画布上没有空白处可以双击")
            blank = [bx + bw * 0.5, by + bh * 0.5]
        cdp.dbl(float(blank[0]), float(blank[1]))
        time.sleep(0.7)
        if not cdp.js("!!document.querySelector('.cvs-menu')"):
            problems.append("双击没弹出生成节点菜单")
        cdp.shot("a_menu")
        before = cdp.js("document.querySelectorAll('.cnode.gen').length")
        cdp.js("(()=>{const b=document.querySelector('.cvs-menu [data-mk=imagegen]');if(b)b.click();return !!b})()")
        time.sleep(1.2)
        after = cdp.js("document.querySelectorAll('.cnode.gen').length")
        if after <= before:
            problems.append(f"菜单点了没建出节点（{before} → {after}）")
        sel = cdp.js("(()=>{const e=document.querySelector('.cnode.gen.sel')"
                     "||[...document.querySelectorAll('.cnode.gen')].pop();"
                     "return e?e.dataset.id:''})()") or ""
        print("新节点 id =", sel)

        # 只对刚建的这个节点写提示词、点它自己的生成按钮。
        # 用真实点击 + 输入，别用 dispatchEvent：后者造不出受信任的事件，
        # 页面上的 input 委托收不到，n.prompt 还是空的，点生成会被拦下。
        ta = center(cdp, '.cnode[data-id="%s"] textarea[data-prompt]' % sel)
        if not ta:
            problems.append("新节点上没有提示词输入框")
        else:
            cdp.click_at(ta[0], ta[1])
            time.sleep(0.2)
            cdp.call("Input.insertText", {"text": "宋代文士独立江边，远山如黛，水墨质感"})
            time.sleep(0.4)
            typed = cdp.js('(document.querySelector(\'.cnode[data-id="%s"] textarea[data-prompt]\')||{}).value||""' % sel)
            print("输入框里的字 =", typed)
            if not typed:
                problems.append("提示词没输进去")
        btn = center(cdp, '.cnode[data-id="%s"] [data-gen]' % sel)
        if not btn:
            problems.append("找不到生成按钮")
        else:
            jobs_before = {j["id"] for j in (get("/api/state").get("jobs") or [])}
            cdp.click_at(btn[0], btn[1])
            print("已点生成，等成品…")
            time.sleep(1.5)
            print("页面提示 =", cdp.js("(()=>{const e=document.querySelector('.cvs-tip');"
                                  "const m=document.querySelector('.toast,.toasts,.toastbox');"
                                  "return [e?e.textContent:'', m?m.textContent.slice(0,60):'']})()"))
            run = None
            end = time.time() + 300
            while time.time() < end:
                for j in (get("/api/state").get("jobs") or []):
                    if j["id"] in jobs_before or not j.get("runs"):
                        continue
                    r = j["runs"][-1]
                    if r.get("state") in ("done", "failed"):
                        run = r
                        break
                if run:
                    break
                time.sleep(2)
            if not run:
                problems.append("点了生成，5 分钟内没有新任务完成")
            else:
                print("新任务 =", run.get("state"), run.get("size"), run.get("wf"),
                      (run.get("files") or [{}])[0].get("path"))
                if run.get("state") != "done":
                    problems.append("画布出图失败：" + str((run.get("err") or {}).get("msg")))
            time.sleep(2.5)
            cdp.shot("b_done")
            info = cdp.js("(()=>{const im=document.querySelector('.cnode.gen .rwrap img');"
                          "return im?[im.src.slice(-40), im.naturalWidth, im.naturalHeight]:null})()")
            print("节点里的图 =", info)
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
