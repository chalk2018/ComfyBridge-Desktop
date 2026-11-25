"""把每个页面真实的按钮/文案 dump 出来，用来核对需求文档是否与实现一致。

用法：.venv\\Scripts\\python.exe tools/dump_ui_text.py > .cache/ui_dump.txt
"""

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
CDP_PORT = 9273
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_dump"


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


DUMP_JS = r"""(() => {
  const out = [];
  const seen = new Set();
  const push = (kind, el) => {
    const t = (el.innerText || el.value || el.textContent || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 160) return;
    const cls = (el.className || '').toString().split(' ').slice(0, 2).join('.');
    const key = kind + '|' + cls + '|' + t;
    if (seen.has(key)) return;
    seen.add(key);
    out.push(`${kind}\t${el.tagName.toLowerCase()}.${cls}\t${t}`);
  };
  document.querySelectorAll('button, a[href], [role=button], select, input, textarea, h1, h2, h3, h4, .cap-row, .item, .sitem, .tile, .job .head, .parambar, .sheetbar, .seg, .grow-hd, .chip, .tag').forEach(el => {
    if (el.offsetParent === null && el.tagName !== 'SELECT') return;
    push(el.tagName.toLowerCase(), el);
  });
  return out;
})()"""


def main() -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--remote-allow-origins=*",
        f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE}",
        "--window-size=1440,1060", BASE + "/#studio",
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
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1060, "deviceScaleFactor": 1, "mobile": False})
    pages = [
        ("创作台 · 图片生成", "UI.route='studio';UI.capId='image.generate';UI._draftCap='image.generate';UI.mode='t2i';UI.refs=[];UI.sheetOpen=false;"),
        ("创作台 · 图片生成 · 图生图", "UI.route='studio';UI.capId='image.generate';UI.mode='i2i';"),
        ("创作台 · 角色设定图", "UI.route='studio';UI.capId='sheet.character';UI._draftCap='sheet.character';UI.sheetOpen=true;"),
        ("管理台 · 能力与工作流", "UI.route='admin';UI.adminPage='caps';"),
        ("管理台 · 档位表 · 图片生成", "UI.route='admin';UI.capId='image.generate';UI.adminPage='grades';UI.gOpen=1;"),
        ("管理台 · 档位表 · 角色设定图", "UI.route='admin';UI.capId='sheet.character';UI.adminPage='grades';UI.gOpen=0;"),
        ("管理台 · 设定图模板", "UI.route='admin';UI.adminPage='sheet';"),
        ("管理台 · 服务设置", "UI.route='admin';UI.adminPage='service';"),
        ("管理台 · 自检", "UI.route='admin';UI.adminPage='diag';"),
        ("历史", "UI.route='gallery';"),
    ]
    try:
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities")
        time.sleep(1.2)
        for title, code in pages:
            cdp.js(f"(() => {{{code}render();window.scrollTo(0,0);return true}})()")
            time.sleep(1.8 if "grades" in title else 1.0)
            rows = cdp.js(DUMP_JS) or []
            print(f"\n{'=' * 70}\n## {title}\n{'=' * 70}")
            for r in rows:
                print(r)
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "image.generate",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
