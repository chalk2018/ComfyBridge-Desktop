"""1.03 需求文档配图：真实界面截图（含两种真实出图）。

用法：
    .venv\\Scripts\\python.exe _dev\\doc103_shots.py            # 只拍照
    .venv\\Scripts\\python.exe _dev\\doc103_shots.py --gen      # 先真跑一张场景、一张四宫格，再拍照
产物：_reference/docs103/<名字>.png
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9275
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_doc103"
OUT = APP / "_reference" / "docs103"

DESK = {"width": 1440, "height": 1060, "deviceScaleFactor": 2, "mobile": False}
PHONE = {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True}


class C:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=90, suppress_origin=True)
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

    def clip_of(self, selector: str, pad: int = 14) -> dict | None:
        r = self.js(f"""(() => {{
          const el = document.querySelector({json.dumps(selector)});
          if (!el) return null;
          const b = el.getBoundingClientRect();
          return {{x: b.left + window.scrollX, y: b.top + window.scrollY, w: b.width, h: b.height}};
        }})()""")
        if not r or r["w"] < 20 or r["h"] < 20:
            return None
        return {"x": max(0, r["x"] - pad), "y": max(0, r["y"] - pad),
                "width": r["w"] + pad * 2, "height": r["h"] + pad * 2, "scale": 1}

    def shot_el(self, name: str, selector: str, pad: int = 14) -> None:
        clip = self.clip_of(selector, pad)
        if clip is None:
            print(f"!! 找不到元素 {selector}，跳过 {name}")
            return
        self.shot(name, clip)


def set_ui(cdp: C, js: str, pause: float = 1.6) -> None:
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
    for _ in range(70):
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


def jobs_done(cdp: C) -> int:
    return int(cdp.js("document.querySelectorAll('.job.done').length") or 0)


def wait_job(cdp: C, before: int, limit: float = 420) -> bool:
    end = time.time() + limit
    while time.time() < end:
        n = jobs_done(cdp)
        if n > before:
            return True
        time.sleep(1.0)
    return False


def send(cdp: C, kind: str) -> bool:
    """点一次发送（真实提交）。"""
    ok = cdp.js("""(() => {
      const b = document.querySelector('#sendBtn');
      if (!b || b.disabled) return false;
      b.click(); return true;
    })()""")
    print("点击发送:", ok)
    return bool(ok)


# ------------------------------------------------------------------ 真实出图
SCENE_TEXT = ("清晨的江南水乡码头：青石台阶被露水打湿，左侧一排白墙黛瓦的民居，"
              "右侧一条乌篷船系在木桩上，远处一座石拱桥横跨河面，薄雾贴着水面，天光刚亮，柔和偏冷的晨光")


def gen_scene(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='sheet.scene'; UI._draftCap='sheet.scene';
      UI.mode='t2i'; UI.refs=[]; UI.extOpen=false;
      UI.extForm={kind:'scene', layout:'N', sub:'', ink:'color', nochar:'yes', ref:'none',
        grade:'默认', text:'%s', style:'写实摄影', styleCustom:false, styleText:'',
        lineStyle:'W1', refSlots:{}};
      saveDraft(); render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""" % SCENE_TEXT.replace("'", "\\'"), 2.2)
    before = jobs_done(cdp)
    send(cdp, "scene")
    wait_job(cdp, before, 300)


BOARD_CELLS = [
    "清晨的码头，短发女孩背着相机快步走来，远景，薄雾未散",
    "女孩蹲在台阶边整理相机包，中景，侧光",
    "女孩抬头看向河面上的乌篷船，近景，浅景深",
    "乌篷船慢慢划过拱桥，女孩站在岸边目送，大远景，天光变亮",
]


def gen_board(cdp: C) -> None:
    cells = ",\n".join(json.dumps(x, ensure_ascii=False) for x in BOARD_CELLS)
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='board.story'; UI._draftCap='board.story';
      UI.mode='i2i'; UI.refs=[]; UI.boardOpen=false;
      UI.boardForm={layout:'G4', ink:'color', lineStyle:'W1', grade:'默认', sel:0,
        cells:[%s]};
      saveDraft(); render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""" % cells, 2.2)
    before = jobs_done(cdp)
    send(cdp, "board")
    wait_job(cdp, before, 420)


# ------------------------------------------------------------------ 静态配图
def studio_overview(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='image.generate'; UI._draftCap='image.generate';
      UI._navPending=true; UI.mode='t2i'; UI.refs=[]; UI.prompt=''; UI.extOpen=false; UI.boardOpen=false;
      saveDraft({route:'studio',capId:'image.generate'});
      const app=document.querySelector('.app'); if(app){app.classList.remove('nav-open');app.classList.remove('sheet-open');}
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 2.0)
    cdp.shot("u_studio_overview")
    cdp.shot_el("u_cap_list", ".rail", 12)


def studio_scene(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='sheet.scene'; UI._draftCap='sheet.scene';
      UI._navPending=true;
      UI.mode='t2i'; UI.refs=[]; UI.extOpen=false;
      UI.extForm={kind:'scene', layout:'N', sub:'', ink:'color', nochar:'yes', ref:'none',
        grade:'默认', text:'%s', style:'写实摄影', styleCustom:false, styleText:'',
        lineStyle:'W1', refSlots:{}};
      saveDraft({route:'studio',capId:'sheet.scene'});
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""" % SCENE_TEXT.replace("'", "\\'"), 2.2)
    cdp.shot("u_studio_scene")
    cdp.shot_el("u_composer_scene", ".composer", 16)
    # 找到「场景设定图」的那张完成卡
    card = cdp.js("""(() => {
      const ks=[...document.querySelectorAll('.job.done')].filter(j=>j.textContent.includes('场景设定图')||j.textContent.includes('普通'));
      const j=ks[ks.length-1]; if(!j) return null; j.scrollIntoView({block:'center'}); return j.id;
    })()""")
    if card:
        time.sleep(1.0)
        cdp.shot_el("u_jobcard_scene", f"#{card}", 10)


def studio_scene_f4(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='sheet.scene'; UI._draftCap='sheet.scene';
      UI._navPending=true;
      UI.mode='t2i'; UI.refs=[]; UI.extOpen=true;
      UI.extForm={kind:'scene', layout:'F9', sub:'', ink:'color', nochar:'yes', ref:'none',
        grade:'默认', text:'%s', style:'写实摄影', styleCustom:false, styleText:'',
        lineStyle:'W1', refSlots:{}};
      saveDraft({route:'studio',capId:'sheet.scene'}); render();
      window.scrollTo(0, document.body.scrollHeight); return true;
    })()""" % SCENE_TEXT.replace("'", "\\'"), 1.6)
    cdp.js("(() => { refreshExtPreview(); return true })()")
    cdp.wait("!!(UI.extPreview && UI.extPreview.prompt)", 25)
    time.sleep(0.8)
    cdp.js("(() => { render(); window.scrollTo(0, document.body.scrollHeight); return true })()")
    time.sleep(0.6)
    cdp.shot("u_studio_scene_f9")
    cdp.shot_el("u_scene_preview", "#extPv", 12)


def studio_scene_expand(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='sheet.scene'; UI._draftCap='sheet.scene';
      UI._navPending=true;
      UI.mode='expand'; UI.refs=[]; UI.extOpen=false;
      UI.extForm={kind:'scene', layout:'PUSH', sub:'L', ink:'color', nochar:'yes', ref:'none',
        grade:'默认', text:'', style:'', styleCustom:false, styleText:'', lineStyle:'W1', refSlots:{}};
      saveDraft({route:'studio',capId:'sheet.scene'});
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 2.0)
    cdp.shot("u_studio_scene_expand")
    cdp.shot_el("u_composer_scene_expand", ".composer", 16)


def studio_prop(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='sheet.prop'; UI._draftCap='sheet.prop';
      UI._navPending=true;
      UI.mode='t2i'; UI.refs=[]; UI.extOpen=false;
      UI.extForm={kind:'prop', layout:'MV', sub:'', ink:'color', nochar:'yes', ref:'none',
        grade:'默认', text:'文房四宝里的石质镇纸：长方形，青灰色端石，顶部浮雕一只卧牛，四角包铜',
        style:'写实摄影', styleCustom:false, styleText:'', lineStyle:'W1', refSlots:{}};
      saveDraft({route:'studio',capId:'sheet.prop'});
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 2.2)
    cdp.shot("u_studio_prop")
    cdp.shot_el("u_composer_prop", ".composer", 16)
    card = cdp.js("""(() => {
      const ks=[...document.querySelectorAll('.job.done')].filter(j=>j.textContent.includes('道具'));
      const j=ks[ks.length-1]; if(!j) return null; j.scrollIntoView({block:'center'}); return j.id;
    })()""")
    if card:
        time.sleep(1.0)
        cdp.shot_el("u_jobcard_prop", f"#{card}", 10)
    # 单视图 + 线稿
    set_ui(cdp, """(() => {
      UI.extForm.layout='SV'; UI.extForm.ink='line'; UI.extForm.lineStyle='W2'; UI.extOpen=false;
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 1.6)
    cdp.shot_el("u_composer_prop_sv", ".composer", 16)


def studio_board(cdp: C) -> None:
    cells = ",\n".join(json.dumps(x, ensure_ascii=False) for x in BOARD_CELLS)
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='board.story'; UI._draftCap='board.story';
      UI._navPending=true;
      UI.mode='i2i'; UI.refs=[]; UI.boardOpen=false;
      UI.boardForm={layout:'G4', ink:'color', lineStyle:'W1', grade:'默认', sel:1,
        cells:[%s]};
      saveDraft({route:'studio',capId:'board.story'});
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""" % cells, 2.2)
    cdp.shot("u_studio_board")
    cdp.shot_el("u_composer_board", ".composer", 16)
    card = cdp.js("""(() => {
      const ks=[...document.querySelectorAll('.job.done')].filter(j=>j.textContent.includes('宫格')||j.textContent.includes('故事板'));
      const j=ks[ks.length-1]; if(!j) return null; j.scrollIntoView({block:'center'}); return j.id;
    })()""")
    if card:
        time.sleep(1.0)
        cdp.shot_el("u_jobcard_board", f"#{card}", 10)


def studio_board_preview(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI._navPending=true; UI.boardOpen=true;
      saveDraft({route:'studio',capId:'board.story'}); render();
      window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 1.2)
    cdp.js("(() => { refreshBoardPreview(); return true })()")
    cdp.wait("!!(UI.boardPreview && UI.boardPreview.prompt)", 25)
    time.sleep(0.8)
    cdp.js("(() => { render(); window.scrollTo(0, document.body.scrollHeight); return true })()")
    time.sleep(0.6)
    cdp.shot("u_studio_board_preview")
    cdp.shot_el("u_board_preview", "#boardPv", 12)


def studio_board_g6(cdp: C) -> None:
    set_ui(cdp, """(() => {
      UI._navPending=true; UI.boardOpen=false;
      UI.boardForm={layout:'G6', ink:'line', lineStyle:'W3', grade:'默认', sel:0, cells:['','','','','','']};
      saveDraft({route:'studio',capId:'board.story'});
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 1.8)
    cdp.shot_el("u_composer_board_g6", ".composer", 16)


def admin_shots(cdp: C) -> None:
    plans = [
        ("u_admin_caps", "UI.capId='image.generate';UI.adminPage='caps';"),
        ("u_admin_grades_scene", "UI.capId='sheet.scene';UI.adminPage='grades';UI.comboSel='F4.t2i.color.yes';UI.gOpen=0;"),
        ("u_admin_grades_prop", "UI.capId='sheet.prop';UI.adminPage='grades';UI.comboSel='MV.t2i.color.none';UI.gOpen=0;"),
        ("u_admin_grades_board", "UI.capId='board.story';UI.adminPage='grades';UI.comboSel='G4.color';UI.gOpen=1;"),
    ]
    for name, code in plans:
        set_ui(cdp, f"(() => {{UI.route='admin';UI._navPending=true;{code}"
                     "saveDraft({route:'admin',adminPage:UI.adminPage,capId:UI.capId});"
                     "render();window.scrollTo(0,0);return true})()",
               2.4 if "grades" in name else 1.8)
        cdp.shot(name)
    set_ui(cdp, """(() => {
      UI.route='admin'; UI._navPending=true; UI.adminPage='grades'; UI.capId='sheet.scene';
      saveDraft({route:'admin',adminPage:'grades',capId:'sheet.scene'});
      render(); window.scrollTo(0,0); return true;
    })()""", 2.0)
    cdp.shot_el("u_admin_menu", ".admin-nav", 10)
    # 场景 / 道具的设定图模板模态框
    for name, cap, cid in (("u_tpl_scene", "sheet.scene", "N.t2i.color.yes"),
                           ("u_tpl_prop", "sheet.prop", "MV.t2i.color.none")):
        set_ui(cdp, f"""(() => {{
          UI.route='admin'; UI._navPending=true; UI.capId='{cap}'; UI.adminPage='grades';
          UI.comboSel='{cid}'; UI.gOpen=0;
          saveDraft({{route:'admin',adminPage:'grades',capId:'{cap}'}});
          render(); window.scrollTo(0,0); return true;
        }})()""", 2.2)
        opened = cdp.js("""(() => {
          const b=document.querySelector('[data-sheet-template]');
          if(!b) return false; b.click(); return true;
        })()""")
        print(name, "模板模态框:", "已打开" if opened else "未找到入口")
        time.sleep(1.6)
        cdp.shot(name)
        cdp.js("""(() => {document.querySelectorAll('.modal [data-close]').forEach(b=>b.click()); return true})()""")
        time.sleep(0.8)


def phone_shots(cdp: C) -> None:
    cdp.call("Emulation.setDeviceMetricsOverride", PHONE)
    set_ui(cdp, """(() => {
      UI.route='studio'; UI.capId='sheet.scene'; UI._draftCap='sheet.scene';
      UI._navPending=true; UI.mode='t2i'; UI.extOpen=false; UI.navOpen=false; UI.sheet=false;
      saveDraft({route:'studio',capId:'sheet.scene'});
      const app=document.querySelector('.app'); if(app){app.classList.remove('nav-open');app.classList.remove('sheet-open');}
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 2.0)
    cdp.shot("u_phone_scene")
    set_ui(cdp, """(() => {
      UI.capId='board.story'; UI._navPending=true; UI.boardOpen=false;
      UI.boardForm={layout:'G4', ink:'color', lineStyle:'W1', grade:'默认', sel:1, cells:[
        '清晨的码头，短发女孩背着相机快步走来，远景','女孩蹲在台阶边整理相机包，中景','','乌篷船慢慢划过拱桥，大远景']};
      saveDraft({route:'studio',capId:'board.story'});
      render(); window.scrollTo(0, document.body.scrollHeight); return true;
    })()""", 1.8)
    cdp.shot("u_phone_board")


def main() -> None:
    gen = "--gen" in sys.argv
    httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "adminPage": "caps",
                                                 "capId": "sheet.scene"}}, timeout=20)
    cdp, proc = connect()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride", DESK)
        cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities", 30)
        time.sleep(1.2)
        if gen:
            gen_scene(cdp)
            gen_board(cdp)
        studio_overview(cdp)
        studio_scene(cdp)
        studio_scene_f4(cdp)
        studio_scene_expand(cdp)
        studio_prop(cdp)
        studio_board(cdp)
        studio_board_preview(cdp)
        studio_board_g6(cdp)
        admin_shots(cdp)
        phone_shots(cdp)
    finally:
        cdp.ws.close()
        proc.terminate()
        httpx.post(f"{BASE}/api/ui", json={"patch": {"route": "studio", "capId": "image.generate",
                                                     "adminPage": "caps"}}, timeout=20)


if __name__ == "__main__":
    main()
