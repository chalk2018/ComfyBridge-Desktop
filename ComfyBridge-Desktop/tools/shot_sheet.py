"""角色设定图模块视觉验收：截创作台（含提示词面板）与管理台模板页。

用法：
    .venv\\Scripts\\python.exe tools/shot_sheet.py
产物：_reference/page_sheet_studio.png、_reference/page_sheet_admin.png
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9251
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_sheet"
OUT = APP / "_reference"
CHAR = ("二十五岁中国女子，清冷气质，长直黑发用竹簪高盘，细眉凤眼，穿素白交领长衫，"
        "腰系青色织带，脚踩黑布靴，腰侧挂一枚青玉玉佩")


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

    def wait(self, e: str, t: float = 20.0) -> bool:
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)
        return False

    def shot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path.write_bytes(base64.b64decode(data["data"]))
        print("saved", path)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "sheet.character"}}, timeout=20)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.5",
        f"--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
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
    try:
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities", 25)
        time.sleep(1.2)
        # 造一个「填好 + 面板展开」的创作台状态
        cdp.js(f"""(()=>{{
          UI.capId='sheet.character'; UI._draftCap='sheet.character'; UI.mode='i2i';
          UI.refs=[{{id:'shot1',kind:'image'}}];
          UI.sheetOpen=true;
          UI.sheetForm={{layout:'B',ink:'color',lineStyle:'W1',ref:'lock',
                         character:{json.dumps(CHAR, ensure_ascii=False)},style:'水墨国风',styleText:''}};
          UI.params.grade='四宫格'; render(); return true;}})()""")
        time.sleep(0.5)
        cdp.js("refreshSheetPreview()")
        time.sleep(1.2)
        cdp.shot(OUT / "page_sheet_studio.png")
        # 管理台模板页
        cdp.js("(()=>{UI.route='admin';UI.adminPage='sheet';render();return true;})()")
        time.sleep(1.2)
        cdp.shot(OUT / "page_sheet_admin.png")
        # 管理台模板页下半部分（通用块 / 风格 / 实测组合）
        print("滚动容器:", cdp.js("""(()=>{
          const c=[document.scrollingElement, document.querySelector('.admin-main'),
                   document.querySelector('.admin-body'), document.querySelector('#root')];
          for (const el of c) {
            if (el && el.scrollHeight > el.clientHeight + 20) {
              el.scrollTop = Math.round((el.scrollHeight - el.clientHeight) * 0.55);
              return (el.className || el.tagName) + ' → ' + el.scrollTop + '/' + el.scrollHeight;
            }
          }
          return 'none';})()"""))
        time.sleep(0.8)
        cdp.shot(OUT / "page_sheet_admin_blocks.png")
        # 再往下：风格预设 + 已实测组合
        cdp.js("(()=>{const el=document.scrollingElement;el.scrollTop=Math.round(el.scrollHeight*0.9);return el.scrollTop;})()")
        time.sleep(0.8)
        cdp.shot(OUT / "page_sheet_admin_styles.png")
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "sheet.character",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
