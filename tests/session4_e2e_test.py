"""第 4 轮需求的 E2E：参考图清理 + 基于上一条带回图片 / 手机端重做 / 输入框高度规则。

用法：
    .venv\\Scripts\\python.exe tests\\session4_e2e_test.py
说明：
    - 需要服务已在 http://127.0.0.1:8190 运行（后端已重启，含 /api/tasks/{id}/refs）。
    - 会真实提交一次「参考生图」任务（用真实图片当参考），等它跑完后验证「基于上一条」。
    - 会在 .cache/shots/ 留下手机端截图，方便肉眼复核。
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
CDP_PORT = 9228
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_s4"
SHOTS = APP / ".cache" / "shots"
HTTP = httpx.Client(base_url=BASE, timeout=60.0)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: Any, detail: str = "") -> None:
    passed = bool(ok)
    RESULTS.append((name, passed, detail[:240]))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail[:220]}" if detail else ""), flush=True)


def api(path: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    resp = HTTP.request(method, path, json=body) if body is not None else HTTP.request(method, path)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": resp.text[:200]}


class CDP:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=40, suppress_origin=True)
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

    def shot(self, name: str) -> None:
        SHOTS.mkdir(parents=True, exist_ok=True)
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (SHOTS / f"{name}.png").write_bytes(__import__("base64").b64decode(data["data"]))

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def click(cdp: CDP, selector: str) -> Any:
    return cdp.js(f"(() => {{ const e=document.querySelector({json.dumps(selector)}); if(!e) return false; e.click(); return true; }})()")


def mobile(cdp: CDP, w: int = 390, h: int = 844) -> None:
    cdp.call("Emulation.setDeviceMetricsOverride", {
        "width": w, "height": h, "deviceScaleFactor": 2, "mobile": True,
        "screenWidth": w, "screenHeight": h, "positionX": 0, "positionY": 0, "dontSetVisibleSize": False,
    })
    cdp.pause(600)


def desktop(cdp: CDP) -> None:
    cdp.call("Emulation.clearDeviceMetricsOverride")
    cdp.pause(400)


def start_browser() -> tuple[subprocess.Popen, CDP]:
    # 每次用独立 profile：既不会被上次的 #admin/grades 带走，也不用去删旧目录
    prof = APP / ".cache" / f"edge_s4_{int(time.time())}"
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


def wait_idle(timeout: float = 300.0) -> bool:
    """等队列空下来，否则发送会被「忙时拦截」挡住，验证不了清空逻辑。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api("/api/state")
        busy = [(j["id"], r["state"]) for j in st.get("jobs", []) for r in j.get("runs", [])
                if r.get("state") in ("queued", "running", "recovering")]
        if not busy:
            return True
        time.sleep(2)
    return False


def overflow_report(cdp: CDP) -> dict[str, Any]:
    """只看真正撑破页面的元素：祖先里有滚动容器的算正常（例如横向胶囊条）。"""
    return cdp.js("""(() => {
      const vw = window.innerWidth;
      let worst = null;
      document.querySelectorAll('body *').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width <= 0) return;
        if (r.right <= vw + 1) return;
        let a = el.parentElement, clipped = false;
        while (a && a !== document.body) {
          const ox = getComputedStyle(a).overflowX;
          if (ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip') { clipped = true; break; }
          a = a.parentElement;
        }
        if (!clipped && (!worst || r.right > worst.right)) {
          worst = { tag: el.tagName, cls: (el.className || '').toString().slice(0, 40), right: Math.round(r.right) };
        }
      });
      return { vw, scrollW: document.documentElement.scrollWidth, overflow: !!worst, worst };
    })()""")


def run_checks(cdp: CDP) -> None:
    api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps"}})
    cdp.js("location.hash = '#studio'")
    cdp.call("Page.reload", {"ignoreCache": True})
    cdp.wait("!!document.querySelector('.composer')", 25)

    # ================= A. 手机端骨架 =================
    mobile(cdp)
    lay = cdp.js("""(() => {
      const r = s => document.querySelector(s).getBoundingClientRect();
      const st = getComputedStyle(document.querySelector('.studio'));
      const rail = document.querySelector('.rail'); const rs = getComputedStyle(rail);
      const cs = document.querySelector('.chat-scroll');
      const app = document.querySelector('.app');
      return {
        vh: window.innerHeight, vw: window.innerWidth,
        appH: Math.round(app.getBoundingClientRect().height),
        studioDir: st.flexDirection,
        railDir: rs.flexDirection, railOx: rs.overflowX,
        railPos: rs.position, railRight: Math.round(rail.getBoundingClientRect().right),
        composerBottom: Math.round(r('.composer').bottom),
        composerW: Math.round(r('.composer').width),
        chatScrollable: cs.scrollHeight > cs.clientHeight + 2,
        chatInView: Math.round(r('.chat-scroll').bottom) <= Math.round(r('.composer').top) + 2,
      };
    })()""")
    check("A1 手机整页一屏高、不靠 body 滚动", abs(lay["appH"] - lay["vh"]) <= 2, str(lay))
    # 手机端左栏改成左侧抽屉（参考豆包），默认整块移出屏幕
    check("A2 创作台竖列布局、左栏收成左侧抽屉", lay["studioDir"] == "column"
          and lay["railPos"] == "fixed" and lay["railRight"] <= 2, str(lay))
    check("A3 输入区贴底且满宽", abs(lay["composerBottom"] - lay["vh"]) <= 2 and lay["composerW"] >= lay["vw"] - 24, str(lay))
    check("A4 聊天区内部滚动、不被输入区盖住", lay["chatScrollable"] and lay["chatInView"], str(lay))
    ov = overflow_report(cdp)
    check("A5 无横向溢出", ov["overflow"] is False, str(ov))
    cdp.shot("m1-studio")

    # ================= B. 右栏改成底部抽屉 =================
    vis = cdp.js("""(() => {
      const d = document.querySelector('.drawer'); const cs = getComputedStyle(d);
      return { vis: cs.visibility, ty: cs.transform, btn: !!document.querySelector('#sheetToggle'),
               btnVisible: (document.querySelector('#sheetToggle')||{}).offsetParent !== null };
    })()""")
    check("B1 手机端右栏默认收起、顶栏有「任务」按钮", vis["vis"] in ("hidden", "visible") and vis["btn"] and vis["btnVisible"], str(vis))
    click(cdp, "#sheetToggle")
    cdp.pause(500)
    sheet = cdp.js("""(() => {
      const app = document.querySelector('.app'); const d = document.querySelector('.drawer');
      const r = d.getBoundingClientRect(); const cs = getComputedStyle(d);
      const mask = getComputedStyle(document.querySelector('#sheetMask') || document.createElement('div'));
      return { open: app.classList.contains('sheet-open'), vis: cs.visibility,
               top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height),
               left: Math.round(r.left), width: Math.round(r.width),
               vw: window.innerWidth, vh: window.innerHeight, maskOpacity: mask.opacity,
               hasTasks: !!document.querySelector('#tbatch') };
    })()""")
    # 抽屉从右侧弹出（贴右上、竖向满高、宽度小于屏宽），里面是会话任务与批量删除
    check("B2 点「任务」从右侧滑出抽屉（含会话任务/批量删除）",
          sheet["open"] and sheet["vis"] == "visible" and sheet["top"] <= 2
          and abs(sheet["bottom"] - sheet["vh"]) <= 3
          and sheet["left"] > 0 and sheet["width"] <= sheet["vw"]
          and sheet["hasTasks"], str(sheet))
    check("B3 抽屉打开时遮罩可见", float(sheet["maskOpacity"]) > 0.5, str(sheet["maskOpacity"]))
    cdp.shot("m2-sheet")
    click(cdp, "#sheetMask")
    cdp.pause(500)
    closed = cdp.js("""(() => {
      const app = document.querySelector('.app'); const d = document.querySelector('.drawer');
      return { open: app.classList.contains('sheet-open'), vis: getComputedStyle(d).visibility };
    })()""")
    check("B4 点遮罩关掉抽屉", closed["open"] is False, str(closed))

    # ================= C. 历史记录页 =================
    click(cdp, '[data-nav="gallery"]')
    cdp.pause(700)
    gal = cdp.js("""(() => {
      const h2 = document.querySelector('.admin-main h2').getBoundingClientRect();
      const f = document.querySelector('.gfilters').getBoundingClientRect();
      const grid = document.querySelector('.gallery');
      const cols = grid ? getComputedStyle(grid).gridTemplateColumns.split(' ').length : 0;
      return { h2w: Math.round(h2.width), h2h: Math.round(h2.height), filtersRight: Math.round(f.right),
               vw: window.innerWidth, cols, cards: document.querySelectorAll('.gallery .card').length };
    })()""")
    check("C1 历史标题横排（不再被挤成竖排）", gal["h2w"] > gal["h2h"] * 2.5, str(gal))
    check("C2 筛选条不出屏", gal["filtersRight"] <= gal["vw"] + 1, str(gal))
    check("C3 手机端两列相册式网格", gal["cols"] == 2, str(gal))
    cov = overflow_report(cdp)
    check("C4 历史页无横向溢出", cov["overflow"] is False, str(cov))
    cdp.shot("m3-gallery")

    # ================= D. 管理台 =================
    click(cdp, '[data-nav="admin"]')
    cdp.pause(800)
    adm = cdp.js("""(() => {
      const nav = document.querySelector('.admin-nav'); const ns = getComputedStyle(nav);
      const row = document.querySelector('.cap-row');
      const main = row ? row.querySelector('div') : null;
      const nm = row ? row.querySelector('.row b') : null;
      return { navDir: ns.flexDirection, navOx: ns.overflowX,
               navPos: ns.position, navRight: Math.round(nav.getBoundingClientRect().right),
               mainW: main ? Math.round(main.getBoundingClientRect().width) : 0,
               nameH: nm ? Math.round(nm.getBoundingClientRect().height) : 0,
               nameW: nm ? Math.round(nm.getBoundingClientRect().width) : 0 };
    })()""")
    # 管理台菜单和创作台一样收进左侧抽屉
    check("D1 管理台导航收成左侧抽屉", adm["navPos"] == "fixed" and adm["navRight"] <= 2, str(adm))
    check("D2 能力卡文字不再被挤成竖排", adm["mainW"] >= 200 and adm["nameH"] <= 28 and adm["nameW"] >= 40, str(adm))
    dov = overflow_report(cdp)
    check("D3 管理台无横向溢出", dov["overflow"] is False, str(dov))
    cdp.shot("m4-admin")

    # ================= G. 所有页面在 375 宽下都不撑宽视口 =================
    # 用 innerWidth 判定：一旦某个元素把布局撑宽，手机浏览器会把整页缩小（字变得很小），
    # 只看 scrollWidth 是发现不了的（viewport 会跟着变宽）。
    mobile(cdp, 375, 667)
    for hash_, name in [("#studio", "创作台"), ("#gallery", "历史"), ("#admin/caps", "能力与工作流"),
                        ("#admin/grades", "档位表"), ("#admin/service", "服务设置"), ("#admin/diag", "自检")]:
        cdp.js(f"location.hash='{hash_}'")
        cdp.pause(1800 if "admin" in hash_ else 1000)
        w = cdp.js("({vw: innerWidth, sw: document.documentElement.scrollWidth})")
        check(f"G {name}页不撑宽手机视口", w and w["vw"] == 375 and w["sw"] <= 376, str(w))
    cdp.js("location.hash='#studio'")
    cdp.pause(800)

    # ================= E. 输入框高度：最多 5 行，不溢出就自适应 =================
    click(cdp, '[data-nav="studio"]')
    cdp.pause(600)
    desktop(cdp)
    cdp.wait("!!document.querySelector('#promptInput')", 10)
    cdp.js("""window.__setText = (t) => { const ed=document.querySelector('#promptInput');
        ed.focus(); ed.innerHTML=''; if (t) document.execCommand('insertText', false, t);
        ed.dispatchEvent(new Event('input',{bubbles:true})); return true; };""")
    empt = cdp.js("(() => { window.__setText(''); return Math.round(document.querySelector('#promptInput').getBoundingClientRect().height); })()")
    one = cdp.js("(() => { window.__setText(%s); return Math.round(document.querySelector('#promptInput').getBoundingClientRect().height); })()"
                 % json.dumps("一行字的提示词"))
    many_lines = "\n".join(f"第{i + 1}行文字，用来把输入框撑高看看会不会超过五行" for i in range(12))
    many = cdp.js("""(() => { window.__setText(%s);
        const e = document.querySelector('#promptInput'); const lh = parseFloat(getComputedStyle(e).lineHeight);
        return { h: Math.round(e.getBoundingClientRect().height), lh: Math.round(lh),
                 client: e.clientHeight, scroll: e.scrollHeight }; })()""" % json.dumps(many_lines))
    check("E1 空/单行时高度自适应（不预留大块空白）", empt <= 34 and one <= 42, f"空={empt} 单行={one}")
    check("E2 超过 5 行后封顶并出现滚动条",
          many and many["scroll"] > many["client"] + 2 and many["h"] <= many["lh"] * 5 + 16, str(many))

    # ================= F. 参考图：发完即清空 + 基于上一条能带回图片 =================
    ok_idle = wait_idle(240)
    check("F0 队列空闲（能真发一条）", ok_idle)
    click(cdp, '[data-mode="i2i"]')
    cdp.pause(400)
    # 先把编辑器清干净（上一段 E 留了 12 行占位文字）
    cdp.js("(() => { const ed=document.querySelector('#promptInput'); if(ed){ ed.innerHTML=''; ed.dispatchEvent(new Event('input',{bubbles:true})); } })()")
    # 用一张真实成品图当参考图（不是伪造的 PNG 头，否则 LoadImage 会拒绝）
    up = cdp.js("""(async () => {
        const st = await (await fetch('/api/state')).json();
        let url = '';
        for (const j of st.jobs) for (const r of j.runs) {
          if (r.state === 'done' && (r.files||[]).length) { url = `/api/media?run=${encodeURIComponent(r.id)}&i=0`; break; }
          if (url) break;
        }
        if (!url) return {ok:false, why:'没有可用成品图'};
        const blob = await (await fetch(url)).blob();
        await uploadFiles([new File([blob], 's4_ref.png', {type: blob.type || 'image/png'})]);
        return {ok:true, refs: UI.refs.length};
    })()""")
    cdp.pause(900)
    check("F1 图生图上传参考图成功", (up or {}).get("ok") and cdp.js("document.querySelectorAll('.refs .ref-thumb').length") >= 1, str(up))

    # 用 @ 把这张图引用进提示词，顺便验证「引用令牌」在带回来时也不丢
    click(cdp, "[data-refat]")
    cdp.pause(350)
    chip_ok = cdp.js("""(() => ({chips: document.querySelectorAll('#promptInput .refchip').length,
        prompt: UI.prompt}))()""")
    check("F1b 点 @ 插入了行内引用、序列化成 <image1>", chip_ok["chips"] >= 1 and "<image1>" in chip_ok["prompt"], str(chip_ok))

    jobs_before = len(api("/api/state").get("jobs", []))
    cdp.js("""(() => { const ed=document.querySelector('#promptInput'); ed.focus();
        const r=document.createRange(); r.selectNodeContents(ed); r.collapse(false);
        const s=getSelection(); s.removeAllRanges(); s.addRange(r);
        document.execCommand('insertText', false, ' 保持人物特征，换到窗边'); ed.dispatchEvent(new Event('input',{bubbles:true})); })()""")
    cdp.pause(200)
    click(cdp, "#sendBtn")
    cdp.pause(1500)
    after = cdp.js("""(() => ({ refs: UI.refs.length, thumbs: document.querySelectorAll('.refs .ref-thumb').length,
        text: (document.querySelector('#promptInput').textContent||'').trim(), mode: UI.mode }))()""")
    jobs_now = api("/api/state").get("jobs", [])
    check("F2 发出去以后参考图被清空（不再是留着不动）",
          after["refs"] == 0 and after["thumbs"] == 0 and after["text"] == "", str(after))
    check("F3 但模式和档位还是我选的图生图", after["mode"] == "i2i", str(after))
    check("F4 服务端确实新建了一条任务", len(jobs_now) == jobs_before + 1, f"{jobs_before} -> {len(jobs_now)}")

    newest = jobs_now[-1] if jobs_now else {}
    check("F5 这条任务带上了参考图并落到参考生图", len(newest.get("refs") or []) >= 1 and newest.get("wf") == "参考生图"
          and len(newest.get("refsInfo") or []) >= 1, f"{newest.get('id')} {newest.get('wf')} refs={len(newest.get('refs') or [])}")

    # 等它跑完（基于上一条的按钮在成品卡片上）
    job_id = newest.get("id", "")
    deadline = time.time() + 300
    while time.time() < deadline:
        st = api("/api/state")
        j = next((x for x in st.get("jobs", []) if x["id"] == job_id), {})
        if any(r.get("state") == "done" for r in j.get("runs", [])):
            break
        if any(r.get("state") == "failed" for r in j.get("runs", [])):
            break
        time.sleep(3)
    cdp.call("Page.reload", {"ignoreCache": True})
    cdp.wait("!!document.querySelector('[data-refine=\"%s\"]')" % job_id, 25)
    click(cdp, f'[data-refine="{job_id}"]')
    cdp.pause(2500)
    back = cdp.js("""(() => {
      const thumbs = [...document.querySelectorAll('.refs .ref-thumb img')];
      return { refs: UI.refs.length, thumbs: thumbs.length,
               loaded: thumbs.filter(i => i.naturalWidth > 0).length,
               mode: UI.mode, prompt: UI.prompt,
               chips: document.querySelectorAll('#promptInput .refchip').length,
               toast: (document.querySelector('.toast')||{}).textContent || '' };
    })()""")
    check("F6 基于上一条把参考图带回来了（缩略图真的能显示）",
          back["refs"] >= 1 and back["thumbs"] >= 1 and back["loaded"] >= 1, str(back))
    check("F7 同时回到图生图、提示词里的 <imageN> 引用还在",
          back["mode"] == "i2i" and "<image1>" in back["prompt"] and back["chips"] >= 1, str(back))

    # ================= Z. 无 JS 异常 =================
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
    if SHOTS.exists():
        print(f"截图：{SHOTS}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
