"""把《需求与功能文档-1.02》按章节截图，人工过一眼版式有没有崩。

用 CDP 驱动本机 Edge（headless），不装 Playwright；文件一律落 D 盘。
用法：.venv/Scripts/python.exe tools/shot_doc.py
"""

from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import time
import urllib.request

import websocket  # websocket-client

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT.parent / "ComfyBridge-需求与功能文档-1.02.html"
OUT = ROOT / "_reference"
PROFILE = ROOT / ".cache" / "edge_doc"
PORT = 9333
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class CDP:
    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=40)
        self.id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                return msg.get("result") or {}

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if r.get("exceptionDetails"):
            raise RuntimeError(r["exceptionDetails"])
        return (r.get("result") or {}).get("value")

    def shot(self, name: str) -> None:
        r = self.call("Page.captureScreenshot", {"format": "png"})
        (OUT / name).write_bytes(base64.b64decode(r["data"]))
        print("saved", name)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    url = DOC.as_uri()
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--disable-background-networking", "--remote-allow-origins=*",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
        "--window-size=1280,1000", url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 40
        cdp = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as resp:
                    targets = json.loads(resp.read().decode())
                page = next((t for t in targets if t.get("type") == "page" and t.get("url", "").startswith("file:")), None)
                if page:
                    cdp = CDP(page["webSocketDebuggerUrl"])
                    cdp.call("Runtime.enable")
                    cdp.call("Page.enable")
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        if cdp is None:
            raise RuntimeError("连不上 Edge 调试端口")

        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1280, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(2.5)
        # 文档对 html 设了平滑滚动，截图会拍到"滚到一半"的位置，先关掉
        cdp.js("document.documentElement.style.scrollBehavior='auto';"
               "document.body.style.scrollBehavior='auto';true")

        def to(selector_js: str) -> str:
            return ("(()=>{const el=" + selector_js + ";"
                    "if(!el)return 'missing';const y=el.getBoundingClientRect().top+window.scrollY-16;"
                    "window.scrollTo(0,y);return Math.round(window.scrollY);})()")

        shots = [
            ("doc_00_cover", "document.querySelector('#cover')"),
            ("doc_01_changelog", "[...document.querySelectorAll('h2')].find(h=>h.textContent.includes('1.02 相对 1.01'))"),
            ("doc_02_admin_svg", "[...document.querySelectorAll('h2')].find(h=>h.textContent.includes('二、管理台'))"),
            ("doc_03_five_steps", "[...document.querySelectorAll('h3')].find(h=>h.textContent.includes('2.3 这个页面怎么用'))"),
            ("doc_04_sheet_tpl", "[...document.querySelectorAll('h3')].find(h=>h.textContent.includes('2.6 页面五'))"),
            ("doc_05_ch08", "document.querySelector('#ch-08')"),
            ("doc_06_ch08_table", "document.querySelector('#sec-45')"),
            ("doc_07_dual_track", "[...document.querySelectorAll('h3')].find(h=>h.textContent.includes('1.4b'))"),
        ]
        for name, sel in shots:
            pos = cdp.js(to(sel))
            time.sleep(1.2)
            print(name, "->", pos)
            cdp.shot(f"{name}.png")
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
