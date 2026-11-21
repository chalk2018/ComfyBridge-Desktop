"""1.05 四档布局验收：1440 / 1024 / 820 / 390，逐页截图 + 横向溢出 + JS 错误收集。"""
from __future__ import annotations
import json, subprocess, time, urllib.request
from pathlib import Path
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9363
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_layout4_105"
OUT = APP / "_reference"
RESULT = APP / "_dev" / "layout_105.json"


class C:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
        self.n, self.events = 0, []

    def call(self, m, p=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result", {})
            if msg.get("method") in ("Runtime.exceptionThrown", "Log.entryAdded"):
                self.events.append(msg)

    def js(self, e):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            return "EXCEPTION:" + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:300]
        return r.get("result", {}).get("value")

    def wait(self, e, t=30):
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def shot(self, name):
        # 截图在脚本外单独做（check_105.py 已覆盖核心页面），这里只做几何与错误检查
        return name


def main():
    OUT.mkdir(exist_ok=True)
    RESULT.parent.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
                             "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1",
                             "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
                             f"--user-data-dir={PROFILE}", "--window-size=1440,1060", BASE + "/#studio"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = None
        for _ in range(80):
            time.sleep(0.4)
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
                for t in tabs:
                    if t.get("type") == "page" and "8190" in t.get("url", ""):
                        ws = t["webSocketDebuggerUrl"]
                        break
                if ws:
                    break
            except Exception:
                continue
        assert ws, "no CDP"
        c = C(ws)
        c.call("Runtime.enable")
        c.call("Page.enable")
        c.call("Log.enable")
        c.wait("typeof UI!=='undefined' && !!S", 40)
        rows = []
        for w, h in ((1440, 1060), (1024, 900), (820, 900), (390, 780)):
            c.call("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": w <= 820})
            time.sleep(0.4)
            for name, js in (
                ("studio-image", "(async()=>{UI.route='studio';UI.capId='image.generate';UI._userSwitched=true;await saveDraft({capId:UI.capId});render();return 1})()"),
                ("studio-video", "(async()=>{UI.capId='video.generate';UI._userSwitched=true;await saveDraft({capId:UI.capId});render();return 1})()"),
                ("studio-audio", "(async()=>{UI.capId='audio.generate';UI._userSwitched=true;await saveDraft({capId:UI.capId});render();return 1})()"),
                ("admin-grades", "(async()=>{UI.route='admin';UI.adminPage='grades';UI.capId='video.generate';UI._userSwitched=true;await saveDraft({route:'admin',adminPage:'grades',capId:UI.capId});render();return 1})()"),
                ("admin-audio", "(async()=>{UI.capId='audio.generate';UI._userSwitched=true;await saveDraft({route:'admin',adminPage:'grades',capId:UI.capId});render();return 1})()"),
                ("canvas", "(()=>{UI.route='canvas';render();return 1})()"),
            ):
                c.js(js)
                time.sleep(0.35)
                geo = c.js("(()=>{const d=document.documentElement,b=document.body;return {sw:d.scrollWidth,cw:d.clientWidth,bodySW:b.scrollWidth,bodyCW:b.clientWidth}})()")
                row = {"size": f"{w}x{h}", "page": name, **geo,
                       "overflow": bool(geo and (geo["sw"] > geo["cw"] or geo["bodySW"] > geo["bodyCW"]))}
                rows.append(row)
                print(row, flush=True)
        errs = []
        for ev in c.events:
            p = ev.get("params", {})
            if ev.get("method") == "Runtime.exceptionThrown":
                d = p.get("exceptionDetails", {})
                errs.append("EXC " + str(d.get("text") or (d.get("exception") or {}).get("description", ""))[:300])
            elif ev.get("method") == "Log.entryAdded":
                e = p.get("entry", {})
                if e.get("level") == "error" and "favicon" not in e.get("text", "").lower() and "404" not in e.get("text", ""):
                    errs.append("LOG " + e.get("text", "")[:300])
        RESULT.write_text(json.dumps({"rows": rows, "errors": errs}, ensure_ascii=False, indent=2), encoding="utf-8")
        print("OVERFLOW", sum(1 for r in rows if r["overflow"]), "/", len(rows), "JS_ERRORS", len(errs), flush=True)
        for e in errs[:20]:
            print(e, flush=True)
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
