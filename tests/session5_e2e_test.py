"""第 5 轮需求的 E2E：批量删除不报错 / 卡片提示词 5 行 / 成品图操作区 / 看大图复制图片 / 粘贴上传。

用法：
    .venv\\Scripts\\python.exe tests\\session5_e2e_test.py
说明：
    - 需要服务已在 http://127.0.0.1:8190 运行（后端已重启）。
    - 会真实生成 2 张图（快速档）用来验证「历史批量删除」整条链路，删的是这两张自己造的。
    - 不会真的往系统剪贴板写东西：页面里把 navigator.clipboard.write 换成了记录桩。
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
CDP_PORT = 9255
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
HTTP = httpx.Client(base_url=BASE, timeout=120.0)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: Any, detail: str = "") -> None:
    passed = bool(ok)
    RESULTS.append((name, passed, detail[:240]))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail[:220]}" if detail else ""), flush=True)


def api(path: str, method: str = "GET", body: dict | None = None) -> Any:
    resp = HTTP.request(method, path, json=body) if body is not None else HTTP.request(method, path)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": resp.text[:200], "_status": resp.status_code}


class CDP:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
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
    prof = APP / ".cache" / f"edge_s5_{int(time.time())}"
    prof.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--disable-background-networking", "--remote-allow-origins=*",
        f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={prof}",
        "--window-size=1440,900", BASE + "/#studio",
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


def wait_idle(timeout: float = 240.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api("/api/state")
        busy = [r for j in st.get("jobs", []) for r in j.get("runs", [])
                if r.get("state") in ("queued", "running", "recovering")]
        if not busy:
            return True
        time.sleep(2)
    return False


def make_two(done_grade: str = "快速（16:9）") -> list[str]:
    """真跑两张小图，作为「批量删除」的素材。返回新建的 run id。"""
    wait_idle()
    before = {r["id"] for j in api("/api/state").get("jobs", []) for r in j.get("runs", [])}
    ids = []
    for i in range(2):
        res = api("/api/tasks", "POST", {"capId": "image.generate", "prompt": f"S5 删除用素材 {i + 1}",
                                         "grade": done_grade, "refs": [], "wf": "文生图"})
        if not res.get("ok"):
            print("  提交失败:", res, flush=True)
            continue
        wait_idle()
    st = api("/api/state")
    for j in st.get("jobs", []):
        for r in j.get("runs", []):
            if r["id"] not in before and r.get("state") == "done":
                ids.append(r["id"])
    return ids


def run_checks(cdp: CDP) -> None:
    api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps"}})
    cdp.js("location.hash = '#studio'")
    cdp.call("Page.reload", {"ignoreCache": True})
    cdp.wait("!!document.querySelector('.composer')", 25)

    # ================= A. 历史批量删除不再报错（需求 1） =================
    run_ids = make_two()
    check("A1 造出 2 张可删的成品", len(run_ids) >= 1, f"runs={run_ids}")
    items = [{"run": rid, "index": 0} for rid in run_ids]
    # 混进几个无效条目，模拟「选中集合里有已经没了的」
    items += [{"run": "run_nope_1", "index": 0}, {"run": "run_nope_2", "index": 3}]
    resp = HTTP.post("/api/history/delete", json={"items": items, "deleteFiles": True})
    body: Any = None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = None
    check("A2 批量删除返回的是 JSON（不再是纯文本 500）",
          resp.status_code == 200 and isinstance(body, dict) and body.get("ok") is True,
          f"HTTP {resp.status_code} {resp.text[:120]}")
    check("A3 文件真的删掉了、无效条目被跳过",
          isinstance(body, dict) and (body.get("data") or {}).get("files", 0) >= len(run_ids),
          str((body or {}).get("data")))
    gone = api("/api/state")
    still = [r["id"] for j in gone.get("jobs", []) for r in j.get("runs", [])
             if r["id"] in run_ids and not any(f.get("deleted") for f in (r.get("files") or [{}]))]
    check("A4 历史里不再出现这几张", not still, f"残留={still}")

    # ================= B. 卡片里的提示词最多 5 行（需求 2） =================
    # 先自己造一条超长提示词：以前靠会话里恰好留着一条长任务，删任务用例一跑就没了，
    # 「最长的那张卡」退化成两行短卡，这里会假失败。自带数据才稳。
    api("/api/ui", "POST", {"patch": {"route": "studio"}})
    long_prompt = "S5 长提示词卡片：窗台上的橘猫，午后阳光，胶片颗粒。" * 40
    api("/api/tasks", "POST", {"capId": "image.generate", "prompt": long_prompt,
                               "grade": "快速（16:9）", "refs": [], "wf": "文生图"})
    cdp.js("location.hash = '#studio'")
    cdp.call("Page.reload", {"ignoreCache": True})
    cdp.wait("!!document.querySelector('.job .body .msg-u')", 25)
    cdp.wait(f"[...document.querySelectorAll('.job .body .msg-u')].some(e=>e.textContent.length>{len(long_prompt) // 2})", 20)
    box = cdp.js("""(() => {
      const list = [...document.querySelectorAll('.job .body .msg-u')];
      if (!list.length) return null;
      const b = list.reduce((a, c) => (c.scrollHeight > a.scrollHeight ? c : a));
      const lh = parseFloat(getComputedStyle(b).lineHeight) || 21;
      return { h: Math.round(b.getBoundingClientRect().height), sh: b.scrollHeight, ch: b.clientHeight,
               lh: Math.round(lh), lines: Math.round(b.clientHeight / lh) };
    })()""")
    check("B1 长提示词卡片被限制在 5 行以内", box and box["lines"] <= 5, str(box))
    check("B2 超出的内容有滚动条（scrollHeight > clientHeight）", box and box["sh"] > box["ch"] + 2, str(box))

    # ================= C. 成品图操作区（需求 3） =================
    tile = cdp.js("""(() => {
      const t = document.querySelector('.tile');
      if (!t) return null;
      return { label: !!document.querySelector('.tile-label'),
               sizeText: (document.querySelector('.tile-size') || {}).textContent || '',
               sizePos: (() => { const e = document.querySelector('.tile-size'); if (!e) return '';
                 const cs = getComputedStyle(e); return `${cs.right}/${cs.bottom}`; })(),
               btns: [...t.querySelectorAll('.acts button')].map(b =>
                 b.dataset.zoom ? 'zoom' : b.dataset.dl ? 'download' : b.dataset.copyimg ? 'copyimg' : 'other') };
    })()""")
    check("C1 「成品 N」标签已去掉", tile and tile["label"] is False, str(tile))
    check("C2 尺寸显示在右下角", tile and "/" in (tile["sizePos"] or "") and tile["sizeText"].strip() != "", str(tile))
    check("C3 只留下 看大图 / 下载 / 复制图片 三个按钮",
          tile and tile["btns"] == ["zoom", "download", "copyimg"], str(tile["btns"] if tile else ""))

    # 复制图片：把剪贴板写入换成桩，验证真的写了 image/png
    cdp.js("""(() => {
      window.__clip = [];
      if (!navigator.clipboard) Object.defineProperty(navigator, 'clipboard', { value: {}, configurable: true });
      navigator.clipboard.write = async items => {
        for (const it of items) window.__clip.push((it.types || []).join(','));
      };
      return true;
    })()""")
    click(cdp, ".tile .acts [data-copyimg]")
    cdp.pause(1500)
    clip = cdp.js("window.__clip")
    check("C4 点「复制图片」把 PNG 写进剪贴板", clip and any("image/png" in x for x in clip), str(clip))

    # ================= D. 看大图窗口（需求 4） =================
    click(cdp, ".tile .acts [data-zoom]")
    cdp.pause(600)
    modal = cdp.js("""(() => {
      const m = document.querySelector('#mask .modal'); if (!m) return null;
      const texts = [...m.querySelectorAll('.m-foot button')].map(b => b.textContent.trim());
      return { texts, hasClose: texts.includes('关闭'),
               hasCopy: texts.some(t => t.includes('复制图片')),
               hasDir: texts.some(t => t.includes('打开所在目录')),
               footerBtns: texts.length };
    })()""")
    check("D1 弹窗里没有「关闭」按钮", modal and modal["hasClose"] is False, str(modal))
    check("D2 「复制路径」已换成「复制图片」，并保留打开所在目录",
          modal and modal["hasCopy"] and modal["hasDir"] and not any("复制路径" in (t or "") for t in modal["texts"]), str(modal))

    before_clip = len(cdp.js("window.__clip") or [])
    cdp.js("document.body.dispatchEvent(new KeyboardEvent('keydown', {key:'c', ctrlKey:true, bubbles:true, cancelable:true}))")
    cdp.pause(1500)
    after_clip = cdp.js("window.__clip") or []
    check("D3 弹窗里 Ctrl+C 也能复制图片", len(after_clip) > before_clip and any("image/png" in x for x in after_clip),
          str(after_clip))
    check("D4 没有遗留的旧「复制路径」控件", cdp.js("!document.querySelector('#copyPath')"))
    cdp.js("document.querySelector('#mask [data-close]').click()")
    cdp.pause(400)
    check("D5 弹窗能正常关掉（点右上角 ✕）", cdp.js("!document.querySelector('#mask')"))

    # ================= E. 上传 / 粘贴常见图片格式（需求 5） =================
    click(cdp, '[data-mode="i2i"]')
    cdp.pause(500)
    # 上一轮跑完可能还留着参考图（有上限，留着就传不进新的了）
    cdp.js("(() => { UI.refs = []; saveDraft(); refreshComposer(); })()")
    cdp.pause(500)
    up = cdp.js("""(async () => {
      const st = await (await fetch('/api/state')).json();
      let url = '';
      outer: for (const j of st.jobs) for (const r of j.runs) {
        if (r.state === 'done' && (r.files || []).length) { url = `/api/media?run=${encodeURIComponent(r.id)}&i=0`; break outer; }
      }
      if (!url) return { ok: false, why: '没有可用成品图' };
      const blob = await (await fetch(url)).blob();
      const png = blob.type === 'image/png' ? blob : new Blob([blob], { type: 'image/png' });
      const before = UI.refs.length;
      // 故意用没有扩展名的文件名，模拟剪贴板复制过来的图
      await uploadFiles([new File([png], 'image', { type: 'image/png' })]);
      return { ok: true, before, after: UI.refs.length };
    })()""")
    cdp.pause(600)
    check("E1 png（含无扩展名文件名）能上传", (up or {}).get("ok") and (up or {}).get("after", 0) > (up or {}).get("before", 0), str(up))

    paste = cdp.js("""(async () => {
      const st = await (await fetch('/api/state')).json();
      let url = '';
      outer: for (const j of st.jobs) for (const r of j.runs) {
        if (r.state === 'done' && (r.files || []).length) { url = `/api/media?run=${encodeURIComponent(r.id)}&i=0`; break outer; }
      }
      if (!url) return { ok: false, why: '没有可用成品图' };
      const raw = await (await fetch(url)).blob();
      const blob = raw.type === 'image/png' ? raw : new Blob([raw], { type: 'image/png' });
      const ed = document.querySelector('#promptInput');
      ed.focus();
      const dt = new DataTransfer();
      dt.items.add(new File([blob], 'clipboard-image', { type: 'image/png' }));   // 没有扩展名
      ed.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
      const before = UI.refs.length;
      await new Promise(r => setTimeout(r, 1500));
      return { ok: true, before, after: UI.refs.length };
    })()""")
    cdp.pause(400)
    check("E2 Ctrl+V 粘贴图片能进参考图", (paste or {}).get("ok") and (paste or {}).get("after", 0) > (paste or {}).get("before", 0), str(paste))
    check("E3 粘贴/上传后缩略图真的显示出来",
          cdp.js("[...document.querySelectorAll('.refs .ref-thumb img')].every(i => i.complete) && document.querySelectorAll('.refs .ref-thumb').length > 0"))

    # ================= Z. 无未捕获异常 =================
    errs = [e for e in cdp.events if e.get("method") == "Runtime.exceptionThrown"]
    check("Z1 全程无未捕获 JS 异常", not errs, f"{len(errs)} 条")


def main() -> int:
    proc = None
    cdp = None
    try:
        proc, cdp = start_browser()
        run_checks(cdp)
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(("EXC 运行中断", False, str(exc)))
        print(f"[FAIL] 运行中断：{exc}", flush=True)
    finally:
        if cdp:
            cdp.close()
        if proc:
            proc.terminate()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n==== {passed}/{total} 通过 ====", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
