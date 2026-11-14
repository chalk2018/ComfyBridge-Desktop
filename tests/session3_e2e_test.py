"""第 3 轮需求的前端 E2E：手机适配 / 档位表保存不被只读默认档卡住 / 生成后保持模式与档位。

只测前端（app.js / index.html 改动，静态文件刷新即生效），不依赖后端新接口，
所以可以在不重启服务的情况下跑。

用法：
    .venv\\Scripts\\python.exe tests\\session3_e2e_test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9224
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_s3"
HTTP = httpx.Client(base_url=BASE, timeout=30.0)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: Any, detail: str = "") -> None:
    passed = bool(ok)
    RESULTS.append((name, passed, detail[:240]))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail[:200]}" if detail else ""), flush=True)


def api(path: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    resp = HTTP.request(method, path, json=body) if body is not None else HTTP.request(method, path)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": resp.text[:200]}


class CDP:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=30, suppress_origin=True)
        self.seq = 0
        self.events: list[dict[str, Any]] = []

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def js(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS 异常: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.js(expression):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.35)
        return False

    def pause(self, ms: int = 400) -> None:
        self.js(f"new Promise(r=>setTimeout(()=>r(true),{ms}))")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def click(cdp: CDP, selector: str) -> Any:
    return cdp.js(f"(() => {{ const e=document.querySelector({json.dumps(selector)}); if(!e) return false; e.click(); return true; }})()")


def start_browser() -> tuple[subprocess.Popen, CDP]:
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--disable-background-networking", "--remote-allow-origins=*",
        f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE}",
        "--window-size=1440,900", BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as resp:
                targets = json.loads(resp.read().decode())
            page = next((t for t in targets if t.get("type") == "page" and "8190" in t.get("url", "")), None)
            if page:
                cdp = CDP(page["webSocketDebuggerUrl"])
                cdp.call("Runtime.enable")
                cdp.call("Page.enable")
                return proc, cdp
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("连不上 Edge 调试端口")


def done_run_id() -> str:
    st = api("/api/state")
    for j in reversed(st.get("jobs", [])):
        for r in j.get("runs", []):
            if r.get("state") == "done" and r.get("files"):
                return r["id"]
    return ""


def run_checks(cdp: CDP) -> None:
    # 回到创作台
    api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps"}})
    cdp.call("Page.reload", {"ignoreCache": True})
    cdp.wait("!!document.querySelector('.app .topbar')", 25)
    cdp.wait("!!document.querySelector('.composer')", 15)

    # ---------- A. 手机端适配（#5） ----------
    cdp.call("Emulation.setDeviceMetricsOverride", {"width": 375, "height": 667, "deviceScaleFactor": 2, "mobile": True})
    cdp.pause(500)
    layout = cdp.js("""(() => {
      const st=getComputedStyle(document.querySelector('.studio'));
      const rail=document.querySelector('.rail'); const rs=getComputedStyle(rail);
      const chat=document.querySelector('.chat'); const comp=document.querySelector('.composer');
      return {studio:st.display, dir:st.flexDirection, railDir:rs.flexDirection,
              railOverflow:rs.overflowX, railScrollable: rail.scrollWidth>rail.clientWidth+2,
              chatW:Math.round(chat.getBoundingClientRect().width),
              compW:Math.round(comp.getBoundingClientRect().width),
              vw:window.innerWidth, bodyOverflowX: document.documentElement.scrollWidth<=window.innerWidth+2};
    })()""")
    check("A1 手机(375)纵向堆叠布局", layout and layout["studio"] == "flex" and layout["dir"] == "column", str(layout))
    check("A2 左栏变顶部横向可滑胶囊条", layout and layout["railDir"] == "row" and layout["railOverflow"] in ("auto", "scroll"), str(layout.get("railDir") if layout else ""))
    check("A3 聊天区与输入区占满屏宽", layout and layout["chatW"] >= 340 and layout["compW"] >= 340, str(layout))
    check("A4 页面无横向溢出", layout and layout["bodyOverflowX"] is True, str(layout.get("bodyOverflowX") if layout else ""))
    cdp.call("Emulation.clearDeviceMetricsOverride")

    # ---------- B. 档位表保存不被只读默认档卡住（#2） ----------
    click(cdp, '[data-nav="admin"]')
    cdp.pause(400)
    click(cdp, '[data-page="grades"]')
    cdp.pause(500)
    # 全部收起
    cdp.js("UI.gOpen=-1; render();")
    cdp.pause(300)
    before = cdp.js("document.querySelectorAll('.grow').length")
    click(cdp, '[data-savewf]')
    cdp.pause(800)
    toast_txt = cdp.js("(()=>{const t=document.querySelector('.toast,.tblock'); return t?t.textContent:'';})()") or ""
    check("B1 收起全部档位后点保存：不再报『默认档是只读的』", "只读" not in toast_txt, toast_txt)
    check("B2 提示保存成功", ("已保存" in toast_txt) or ("保存" in toast_txt and "只读" not in toast_txt), toast_txt)
    check("B3 档位数量未变", cdp.js("document.querySelectorAll('.grow').length") == before, str(before))
    # 回创作台
    click(cdp, '[data-nav="studio"]')
    cdp.pause(400)

    # ---------- C. 生成后保持模式与档位（#3） ----------
    click(cdp, '[data-mode="i2i"]')
    cdp.pause(400)
    check("C1 切到图生图", cdp.js("document.querySelector('[data-mode=\"i2i\"]').classList.contains('on')"))
    # 选一个非默认档（参考生图有 快速/精细）
    cdp.js("(()=>{const gs=[...document.querySelectorAll('[data-grade]')]; const t=gs.find(b=>b.dataset.grade!=='默认'); if(t) t.click(); return t?t.dataset.grade:'';})()")
    cdp.pause(300)
    chosen_grade = cdp.js("(()=>{const b=document.querySelector('[data-grade].on'); return b?b.dataset.grade:'';})()")
    # 上传一张真实参考图
    rid = done_run_id()
    src = f"/api/media?run={rid}&i=0"
    cdp.js(f"""(async () => {{ const blob=await (await fetch({json.dumps(src)})).blob();
        await uploadFiles([new File([blob],'s3_ref.png',{{type:'image/png'}})]); return true; }})()""")
    cdp.pause(900)
    check("C2 图生图已上传 1 张参考图", cdp.js("document.querySelectorAll('.refs .ref-thumb').length>=1"))
    # 等队列空闲，避免被“忙时拦截”影响后面“发送后是否清空”的判断
    dl = time.time() + 180
    while time.time() < dl:
        st = api("/api/state")
        if not any(r["state"] in ("queued", "running") for j in st["jobs"] for r in j["runs"]):
            break
        time.sleep(1)
    # 输入并发送
    cdp.js("(()=>{const t=document.querySelector('#promptInput'); t.textContent='参考这张图改一版'; t.dispatchEvent(new Event('input',{bubbles:true}));})()")
    cdp.pause(300)
    jobs_before = len(api("/api/state")["jobs"])
    click(cdp, "#sendBtn")
    cdp.pause(1200)
    check("C3 发送后仍保持『图生图』", cdp.js("document.querySelector('[data-mode=\"i2i\"]').classList.contains('on')"),
          "i2i on=" + str(cdp.js("document.querySelector('[data-mode=\"i2i\"]').classList.contains('on')")))
    check("C4 发送后档位不回到『默认』", cdp.js("(()=>{const b=document.querySelector('[data-grade].on'); return !!b && b.dataset.grade!=='默认';})()"),
          "selected=" + str(cdp.js("(()=>{const b=document.querySelector('[data-grade].on'); return b?b.dataset.grade:'';})()")) + f" (期望={chosen_grade})")
    check("C5 输入框已清空", cdp.js("document.querySelector('#promptInput').textContent.trim()===''"))

    check("Z1 全程无未捕获 JS 异常", not any(e.get("method") == "Runtime.exceptionThrown" for e in cdp.events),
          f"{len(cdp.events)} 事件")


def main() -> int:
    if HTTP.get("/api/state").status_code != 200:
        print("服务没在 127.0.0.1:8190 运行，请先启动服务")
        return 1
    proc, cdp = start_browser()
    try:
        run_checks(cdp)
    finally:
        try:
            api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps"}})
        except Exception:  # noqa: BLE001
            pass
        cdp.close()
        proc.terminate()
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n===== 第3轮前端 E2E：{passed}/{len(RESULTS)} 项通过 =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
