"""给需求文档用的真实界面截图。

和 tools/shot_ui.py 的区别：那边是「验收」，拍的是改动点；这里是「文档配图」，
要的是每个页面的标准样子（含真实任务卡、真实成品），并且支持按元素裁切，
让文档里的「这一块长什么样」配的是那一块本身，而不是整屏缩略图。

用法：
    .venv\\Scripts\\python.exe tools/shot_doc_ui.py
产物：_reference/docs/<名字>.png
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
CDP_PORT = 9272
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_doc"
OUT = APP / "_reference" / "docs"

DESK = {"width": 1440, "height": 1060, "deviceScaleFactor": 2, "mobile": False}
PHONE = {"width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True}


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

    def shot(self, name: str, clip: dict | None = None) -> None:
        p: dict = {"format": "png", "captureBeyondViewport": False}
        if clip:
            p["clip"] = clip
        data = self.call("Page.captureScreenshot", p)
        path = OUT / f"{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        print(f"saved {path.name} {path.stat().st_size // 1024}KB")

    def clip_of(self, selector: str, pad: int = 16) -> dict | None:
        """取元素在页面里的绝对矩形，给 captureScreenshot 的 clip 用。"""
        r = self.js(f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return null;
          const b = el.getBoundingClientRect();
          return {{x: b.left + window.scrollX, y: b.top + window.scrollY,
                   w: b.width, h: b.height}};
        }})()""")
        if not r or r["w"] < 20 or r["h"] < 20:
            return None
        return {"x": max(0, r["x"] - pad), "y": max(0, r["y"] - pad),
                "width": r["w"] + pad * 2, "height": r["h"] + pad * 2, "scale": 1}

    def shot_el(self, name: str, selector: str, pad: int = 16) -> None:
        clip = self.clip_of(selector, pad)
        if clip is None:
            print(f"!! 找不到元素 {selector}，跳过 {name}")
            return
        self.shot(name, clip)


def set_ui(cdp: C, js: str, pause: float = 1.5) -> None:
    cdp.js(js)
    time.sleep(pause)


def connect() -> tuple[C, subprocess.Popen]:
    OUT.mkdir(parents=True, exist_ok=True)
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
    return cdp, proc


def studio_image(cdp: C) -> None:
    """创作台 · 图片生成：真实任务卡 + 成品。"""
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='image.generate'; UI._draftCap='image.generate';
      UI.mode='t2i'; UI.refs=[]; UI.prompt='';
      UI.sheetOpen=false; UI.moreOpen=false; UI.selTasks=new Set(); UI.sheet=false;
      const app=document.querySelector('.app'); if(app){app.classList.remove('nav-open');app.classList.remove('sheet-open');}
      render(); window.scrollTo(0, document.body.scrollHeight);
      return true;
    })()""", 2.2)
    time.sleep(0.8)
    cdp.shot("d_studio_image")


def studio_image_parts(cdp: C) -> None:
    """创作台的两块局部：输入区、结果卡。"""
    cdp.shot_el("d_composer", ".composer", 18)
    # 找一张已完成、有成品缩略图的卡
    ok = cdp.js("""(() => {
      const jobs=[...document.querySelectorAll('.job.done')];
      const j=jobs.find(k=>k.querySelector('img')) || jobs[0];
      if(!j) return null; j.scrollIntoView({block:'center'}); return j.id;
    })()""")
    if ok:
        time.sleep(0.8)
        cdp.shot_el("d_jobcard", f"#{ok}", 12)


def studio_sheet(cdp: C) -> None:
    """创作台 · 角色设定图：组合选择（版式/画法/参考）+ 档位。"""
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='sheet.character'; UI._draftCap='sheet.character';
      UI.mode='t2i'; UI.refs=[]; UI.prompt='';
      UI.sheetOpen=true;
      UI.sheetForm={layout:'C', ink:'line', lineStyle:'W3', ref:'lock', grade:'默认',
        character:'二十五岁中国女子，清冷气质，长直黑发用竹簪高盘，细眉凤眼，穿素白交领长衫，腰系青色织带',
        style:'写实摄影', styleText:''};
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 2.0)
    cdp.shot("d_studio_sheet")


def admin(cdp: C) -> None:
    cdp.call("Emulation.setDeviceMetricsOverride", DESK)
    plans = [
        ("d_admin_caps", "UI.adminPage='caps';"),
        ("d_admin_grades_image",
         "UI.capId='image.generate';UI.adminPage='grades';UI.editingWfIndex=0;UI.gOpen=1;"),
        ("d_admin_grades_sheet",
         "UI.capId='sheet.character';UI.adminPage='grades';UI.comboSel='C.line.lock';UI.gOpen=0;"),
        # 模板已不是独立菜单页：先进角色设定图档位表，再单独点按钮弹模态框
        ("d_admin_sheet",
         "UI.capId='sheet.character';UI.adminPage='grades';UI.comboSel='A.color.none';UI.gOpen=0;"),
        ("d_admin_service", "UI.adminPage='service';"),
        ("d_admin_diag", "UI.adminPage='diag';"),
    ]
    for name, code in plans:
        set_ui(cdp, f"(() => {{UI.route='admin';{code}render();window.scrollTo(0,0);return true}})()",
               2.0 if "grades" in name else 1.4)
        if name == "d_admin_sheet":
            # 模板从档位表按钮以模态框打开（不再有独立菜单页）
            opened = cdp.js("""(() => {
              const b=document.querySelector('[data-sheet-template]');
              if(!b) return false; b.click(); return true;
            })()""")
            print("模板模态框:", "已打开" if opened else "未找到入口")
            time.sleep(1.2)
        cdp.shot(name)
    # 设定图模板的「风格预设」表格局部（在模态框里）
    cdp.js("""(() => {
      const t=[...document.querySelectorAll('.presets')].pop();
      if(t) t.scrollIntoView({block:'center'}); return !!t;
    })()""")
    time.sleep(0.7)
    cdp.shot_el("d_sheet_presets", ".sheetlib-modal .presets, .presets", 10)
    # 档位表顶部的切换带局部（白框 + 分段按钮 + 两个动作）
    set_ui(cdp, "(() => {UI.capId='image.generate';UI.adminPage='grades';UI.editingWfIndex=0;"
                "UI.gOpen=1;render();window.scrollTo(0,0);return true})()", 1.6)
    cdp.shot_el("d_grades_bar", ".sheetbar", 14)
    # 已有档位区
    cdp.js("(() => {const g=document.querySelector('.grow-hd'); if(g) g.scrollIntoView({block:'center'}); return true})()")
    time.sleep(0.7)
    cdp.shot_el("d_grades_rows", "[data-page-rows], .grows, .grow-hd", 10)


def close_modals(cdp: C) -> None:
    """拍完模态框必须关掉，否则会遮住后面所有页面的配图。"""
    cdp.js("""(() => {
      document.querySelectorAll('.modal [data-close]').forEach(b => b.click());
      return true;
    })()""")
    time.sleep(0.6)


def gallery(cdp: C) -> None:
    set_ui(cdp, "(() => {UI.route='gallery';UI.galleryFilter='全部';render();window.scrollTo(0,0);return true})()", 1.8)
    cdp.shot("d_gallery")


def phone(cdp: C) -> None:
    cdp.call("Emulation.setDeviceMetricsOverride", PHONE)
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='image.generate'; UI._draftCap='image.generate';
      UI.mode='t2i'; UI.refs=[]; UI.prompt='';
      UI.navOpen=false; UI.sheet=false;
      const app=document.querySelector('.app'); if(app){app.classList.remove('nav-open');app.classList.remove('sheet-open');}
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 2.2)
    cdp.shot("m_studio")
    set_ui(cdp, "(() => {const b=document.querySelector('#navToggle'); if(b) b.click(); return !!b})()", 1.0)
    cdp.shot("m_studio_nav")
    set_ui(cdp, """(() => {
      const app=document.querySelector('.app');
      if(app) app.classList.remove('nav-open'); UI.navOpen=false;
      const b=document.querySelector('#sheetToggle'); if(b) b.click(); return !!b;
    })()""", 1.0)
    cdp.shot("m_studio_sheet")
    # 管理台四页 + 左抽屉
    set_ui(cdp, """(() => {
      const app=document.querySelector('.app'); if(app) app.classList.remove('sheet-open');
      UI.sheet=false; UI.route='admin'; UI.adminPage='caps'; render(); window.scrollTo(0,0); return true;
    })()""", 1.6)
    cdp.shot("m_admin_caps")
    set_ui(cdp, "(() => {const b=document.querySelector('#navToggle'); if(b) b.click(); return !!b})()", 1.0)
    cdp.shot("m_admin_nav")
    set_ui(cdp, """(() => {
      const a=document.querySelector('.admin'); if(a) a.classList.remove('nav-open'); UI.navOpen=false;
      UI.capId='image.generate'; UI.adminPage='grades'; UI.editingWfIndex=0; UI.gOpen=1;
      render(); window.scrollTo(0,0); return true;
    })()""", 1.8)
    cdp.shot("m_admin_grades_image")
    set_ui(cdp, """(() => {
      UI.capId='sheet.character'; UI.adminPage='grades'; UI.comboSel='C.line.lock'; UI.gOpen=0;
      render(); window.scrollTo(0,0); return true;
    })()""", 2.0)
    cdp.shot("m_admin_grades_sheet")
    # 模板：角色设定图档位表 →「✎ 设定图模板」模态框（手机端宽度适配）
    close_modals(cdp)  # 桌面轮拍完的模态框先关掉，保证从档位表正常点开
    set_ui(cdp, """(() => {
      UI.capId='sheet.character'; UI.adminPage='grades'; UI.comboSel='A.color.none';
      UI.gOpen=0; render(); window.scrollTo(0,0); return true;
    })()""", 1.6)
    opened = cdp.js("""(() => {
      const b=document.querySelector('[data-sheet-template]');
      if(!b) return false; b.click(); return true;
    })()""")
    print("手机端模板模态框:", "已打开" if opened else "未找到入口")
    time.sleep(1.2)
    cdp.shot("m_admin_sheet")
    set_ui(cdp, """(() => {
      const t=[...document.querySelectorAll('.presets')].pop();
      if(t) t.scrollIntoView({block:'start'}); return !!t;
    })()""", 0.8)
    cdp.shot("m_admin_sheet_presets")
    close_modals(cdp)  # 同样不能带进手机端「生成历史」配图
    set_ui(cdp, "(() => {UI.route='gallery'; render(); window.scrollTo(0,0); return true})()", 1.6)
    cdp.shot("m_gallery")


def main() -> None:
    httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "adminPage": "caps",
                                                 "capId": "image.generate"}}, timeout=20)
    cdp, proc = connect()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride", DESK)
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities", 25)
        time.sleep(1.2)
        studio_image(cdp)
        studio_image_parts(cdp)
        studio_sheet(cdp)
        admin(cdp)
        close_modals(cdp)  # 模板模态框不能带进「生成历史」配图
        gallery(cdp)
        phone(cdp)
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "image.generate",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
