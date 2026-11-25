"""量移动端顶栏/抽屉/输入区的实际几何，改样式前后各跑一次。"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9274
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_probe"


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
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
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


PROBE = r"""(() => {
  const box = (sel) => {
    const el = document.querySelector(sel); if (!el) return null;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return {sel, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
            bottom: Math.round(r.bottom), pos: cs.position, mh: cs.maxHeight, of: cs.overflowY,
            vis: cs.visibility, top: cs.top, bt: cs.bottom, alignSelf: cs.alignSelf};
  };
  return {
    vw: window.innerWidth, vh: window.innerHeight,
    topbar: box('.topbar'),
    brand: box('.topbar .brand'),
    health: box('.topbar .health'),
    navbtn: box('#navToggle'),
    sheetbtn: box('#sheetToggle'),
    adminNav: box('.admin-nav'),
    rail: box('.rail'),
    composer: box('.composer'),
    composerInner: box('.composer-inner'),
    inputbox: box('.inputbox'),
    sheetbox: box('.sheetbox'),
  };
})()"""


def main() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--remote-allow-origins=*",
        f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE}",
        "--window-size=390,844", BASE + "/#studio",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
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
    cdp.call("Network.enable")
    cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
    cdp.call("Page.reload", {"ignoreCache": True})
    time.sleep(1.0)
    try:
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities")
        time.sleep(1.2)
        print("== 创作台 · 图片生成 ==")
        cdp.js("(() => {UI.route='studio';UI.capId='image.generate';UI._draftCap='image.generate';"
               "UI.mode='t2i';UI.navOpen=false;UI.sheet=false;render();return true})()")
        time.sleep(1.0)
        print(json.dumps(cdp.js(PROBE), ensure_ascii=False, indent=1))

        print("\n== 创作台 · 角色设定图 ==")
        cdp.js("(() => {UI.route='studio';UI.capId='sheet.character';UI._draftCap='sheet.character';"
               "UI.sheetOpen=true;UI.sheetForm={layout:'C',ink:'line',lineStyle:'W3',ref:'lock',"
               "grade:'默认',character:'二十五岁中国女子，清冷气质，长直黑发用竹簪高盘',style:'写实摄影',styleText:''};"
               "render();return true})()")
        time.sleep(1.2)
        print(json.dumps(cdp.js(PROBE), ensure_ascii=False, indent=1))

        print("\n== 管理台 · 抽屉打开 ==")
        cdp.js("(() => {UI.route='admin';UI.adminPage='caps';UI.navOpen=false;render();return true})()")
        time.sleep(1.4)
        cdp.js("(() => {const b=document.querySelector('#navToggle'); if(b) b.click(); return true})()")
        time.sleep(1.0)
        print(json.dumps(cdp.js(PROBE), ensure_ascii=False, indent=1))
        print("admin class:", cdp.js("document.querySelector('.admin') ? document.querySelector('.admin').className : null"))
        print("nav scrollH/clientH:", cdp.js(
            "(() => {const n=document.querySelector('.admin-nav');"
            "return n ? [n.scrollHeight, n.clientHeight, n.getBoundingClientRect().height] : null})()"))
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "image.generate",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
