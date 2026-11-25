"""1.04 视频生成画布验收：五类节点 + 双击菜单 + 拖线自动连接 + 剪刀 + @ 插入。

全程用 CDP 真点真拖（Input.dispatchMouseEvent 会产生 pointer 事件），
不是直接调内部函数，所以能验出「按下去被别的东西吃掉」这类只在真机出现的问题。

用法：
    .venv\\Scripts\\python.exe tools\\shot_canvas104.py
产物：_reference/cv_*.png
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9284
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_shot_canvas"
OUT = APP / "_reference"
ASSET = Path(r"D:\workspace\AI短剧相关\抖音+AI学习\AI短剧练习1\苏轼分镜素材图片")


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
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:800])
        return r.get("result", {}).get("value")

    def wait(self, e: str, t: float = 25.0) -> bool:
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.25)
        return False

    def shot(self, name: str) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path = OUT / f"cv_{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        print("  saved", path.name)

    # ---- 真手 ---- #
    def mouse(self, t: str, x: float, y: float, **kw) -> None:
        p = {"type": t, "x": x, "y": y, "button": "left", "buttons": 1 if t != "mouseReleased" else 0,
             "clickCount": kw.pop("clickCount", 1)}
        p.update(kw)
        self.call("Input.dispatchMouseEvent", p)

    def click_at(self, x: float, y: float) -> None:
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y)
        self.mouse("mouseReleased", x, y)

    def dbl_click(self, x: float, y: float) -> None:
        self.mouse("mouseMoved", x, y)
        self.mouse("mousePressed", x, y, clickCount=1)
        self.mouse("mouseReleased", x, y, clickCount=1)
        self.mouse("mousePressed", x, y, clickCount=2)
        self.mouse("mouseReleased", x, y, clickCount=2)

    def drag(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.mouse("mouseMoved", x1, y1)
        self.mouse("mousePressed", x1, y1)
        for i in range(1, 9):
            self.mouse("mouseMoved", x1 + (x2 - x1) * i / 8, y1 + (y2 - y1) * i / 8)
            time.sleep(0.03)
        self.mouse("mouseReleased", x2, y2)


def center(cdp: C, sel: str) -> tuple[float, float] | None:
    r = cdp.js(
        "(()=>{const e=document.querySelector(%s);if(!e)return null;"
        "const b=e.getBoundingClientRect();"
        "return [b.left+b.width/2,b.top+b.height/2]})()" % json.dumps(sel))
    return (float(r[0]), float(r[1])) if r else None


def size(cdp: C, w: int, h: int, mobile: bool = False) -> None:
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})
    time.sleep(0.7)


def grab_point(cdp: C, sel: str):
    """挑一张真的露在最上面的卡片：被别的卡压住的标题栏是拖不到的。"""
    return cdp.js(
        "(()=>{for(const c of document.querySelectorAll(%s)){"
        "const e=c.querySelector('.cnode-hd .nm');const r=e.getBoundingClientRect();"
        "const x=r.left+r.width/2,y=r.top+r.height/2;const hit=document.elementFromPoint(x,y);"
        "if(hit&&hit.closest('.cnode')===c&&hit.closest('.cnode-hd'))return [x,y];}return null})()"
        % json.dumps(sel))


def path_point(cdp: C):
    """在连线上找一个真能点到的点（曲线中段可能被卡片盖住）。"""
    return cdp.js(
        "(()=>{const p=document.querySelector('.cvs-links path.hit');if(!p)return null;"
        "const L=p.getTotalLength(),m=p.getScreenCTM();"
        "for(let t=0.12;t<=0.88;t+=0.04){const pt=p.getPointAtLength(L*t);"
        "const x=m.a*pt.x+m.c*pt.y+m.e,y=m.b*pt.x+m.d*pt.y+m.f;"
        "const e=document.elementFromPoint(x,y);"
        "if(e&&e.dataset&&e.dataset.edge!==undefined)return [x,y];}return null})()")


def blank_near(cdp: C, port_sel: str):
    """端口附近找一块真正的空白，用来验「拖到空白弹菜单」。"""
    return cdp.js(
        "(()=>{const p=document.querySelector(%s);if(!p)return null;const r=p.getBoundingClientRect();"
        "const cx=r.left+r.width/2,cy=r.top+r.height/2;"
        "const s=document.querySelector('#cvsStage').getBoundingClientRect();"
        "for(let d=100;d<=340;d+=35)for(const ang of [20,50,80,110,-20,-50]){"
        "const rad=ang*Math.PI/180;const x=cx+Math.cos(rad)*d,y=cy+Math.sin(rad)*d;"
        "if(x<s.left+10||x>s.right-10||y<s.top+10||y>s.bottom-10)continue;"
        "const e=document.elementFromPoint(x,y);"
        "if(e&&!e.closest('.cnode')&&!e.closest('.cvs-zoom'))return [x,y];}return null})()"
        % json.dumps(port_sel))


def main() -> int:
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    shot = ASSET / "第15镜_沙湖道中遇雨.jpg"
    if not shot.is_file():
        shot = next(ASSET.glob("*.jpg"))
    print("素材：", shot.name)

    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.5",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", BASE + "/#canvas",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(80):
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
    cdp.call("DOM.enable")
    size(cdp, 1440, 1000)
    problems: list[str] = []
    try:
        cdp.wait("typeof S!=='undefined' && !!S.capabilities && typeof CanvasModule!=='undefined'", 40)
        time.sleep(1.2)

        # 用一块干净的画布，别被上一次的节点干扰
        pid = cdp.js(
            "fetch('/api/canvas/project',{method:'POST',headers:{'Content-Type':'application/json'},"
            "body:JSON.stringify({name:'1.04 验收'})}).then(r=>r.json()).then(j=>j.data&&j.data.id||'')")
        print("新画布", pid)
        cdp.js("(()=>{localStorage.setItem('cb.canvas.pid',%s);UI.route='canvas';render();return 1})()"
               % json.dumps(pid))
        cdp.wait("document.querySelectorAll('.cnode').length===0 && document.querySelector('#cvsStage')", 25)
        time.sleep(1.0)
        cdp.shot("a_empty")

        # ---- 1. 双击空白 → 生成节点菜单 ----
        st = cdp.js("(()=>{const b=document.querySelector('#cvsStage').getBoundingClientRect();"
                    "return [b.left,b.top,b.width,b.height]})()")
        bx, by, bw, bh = [float(v) for v in st]
        cdp.dbl_click(bx + bw * 0.62, by + bh * 0.22)
        time.sleep(0.6)
        if not cdp.js("!!document.querySelector('.cvs-menu')"):
            problems.append("双击空白处没有弹出「生成节点」菜单")
        cdp.shot("b_menu")

        # ---- 2. 菜单里选「图片生成」，在菜单位置建节点 ----
        cdp.js("(()=>{const b=document.querySelector('.cvs-menu [data-mk=imagegen]');if(b)b.click();return !!b})()")
        time.sleep(0.8)
        n_img = cdp.js("(document.querySelectorAll('.cnode.gen').length)||0")
        print("图片生成节点数 =", n_img)
        if n_img != 1:
            problems.append(f"菜单里选了图片生成，节点数却是 {n_img}")
        if cdp.js("!!document.querySelector('.cvs-menu')"):
            problems.append("选完菜单没有关掉")
        cdp.shot("c_imggen")

        # ---- 3. 上传一张参考图 → 素材节点 ----
        doc = cdp.call("DOM.getDocument", {"depth": -1})
        node = cdp.call("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": "#cvsFile"})
        cdp.call("DOM.setFileInputFiles", {"files": [str(shot)], "nodeId": node["nodeId"]})
        cdp.js("(()=>{document.querySelector('#cvsFile').dispatchEvent(new Event('change',{bubbles:true}));return 1})()")
        ok = cdp.wait("document.querySelectorAll('.cnode.mat').length>=1", 20)
        if not ok:
            problems.append("上传图片没有变成素材节点")
        time.sleep(0.8)
        mats = cdp.js("document.querySelectorAll('.cnode.mat').length")
        print("素材节点数 =", mats)
        if mats != 1:
            problems.append(f"选一张图却建出了 {mats} 个素材节点")
        # 用标题栏真拖：素材刚上传时落在画布中间，压住生成节点，得先把它挪到左边
        hd_mat = grab_point(cdp, ".cnode.mat")
        print("拖动前", cdp.js(
            "(()=>[...document.querySelectorAll('.cnode')].map(e=>[e.dataset.kind,"
            "Math.round(e.getBoundingClientRect().left),Math.round(e.getBoundingClientRect().top)]))()"))
        if hd_mat:
            print("抓点", hd_mat)
            cdp.js("window.__ev={mv:0,up:0,cancel:0,down:0,x:0};const s=document.querySelector('#cvsStage');"
                   "['pointermove','pointerup','pointercancel','pointerdown'].forEach(k=>s.addEventListener(k,e=>{"
                   "window.__ev[k==='pointermove'?'mv':k==='pointerup'?'up':k==='pointercancel'?'cancel':'down']++;"
                   "if(e.clientX)window.__ev.x=Math.round(e.clientX)}));"
                   "document.addEventListener('dragstart',e=>{window.__ds=e.target.tagName+'|'+e.target.className},true);")
            cdp.drag(hd_mat[0], hd_mat[1], bx + bw * 0.13, by + bh * 0.22)
            time.sleep(0.5)
            ev = cdp.js("window.__ev")
            print("拖动事件", ev, "原生拖拽源", cdp.js("window.__ds||''"))
            # 拖节点时浏览器如果起了原生拖拽，会 pointercancel 打断拖动，节点就只挪一小步
            if ev and ev.get("cancel"):
                problems.append("拖节点被原生拖拽打断（pointercancel）")
        print("素材/生成 位置", cdp.js(
            "(()=>[...document.querySelectorAll('.cnode')].map(e=>[e.dataset.kind,"
            "Math.round(e.getBoundingClientRect().left),Math.round(e.getBoundingClientRect().top)]))()"))
        cdp.shot("d_material")

        # ---- 4. 素材右侧端口拖到生成节点左侧端口 ----
        p_out = center(cdp, ".cnode.mat .port.out")
        p_in = center(cdp, ".cnode.gen .port.in")
        print("端口", p_out, p_in)
        if not p_out or not p_in:
            problems.append("素材/生成节点的连接点没出来")
        else:
            cdp.drag(p_out[0], p_out[1], p_in[0], p_in[1])
            time.sleep(0.9)
            edges = cdp.js("document.querySelectorAll('.cvs-links path:not(.hit):not(.tmp)').length")
            print("连线数 =", edges)
            if not edges:
                problems.append("从素材拖到生成节点没有连上线")
            cdp.shot("e_linked")

        # ---- 5. 缩略图 + @ 插入到提示词 ----
        chips = cdp.js("document.querySelectorAll('.cnode.gen .nref').length")
        print("输入缩略图 =", chips)
        if not chips:
            problems.append("连上以后生成节点没有显示素材缩略图")
        cdp.js("(()=>{const ta=document.querySelector('.cnode.gen textarea[data-prompt]');if(ta){ta.focus();ta.value='镜头缓慢推进';ta.dispatchEvent(new Event('input',{bubbles:true}));}return 1})()")
        time.sleep(0.3)
        at = cdp.js("(()=>{const b=document.querySelector('.cnode.gen .nref .at');if(!b)return null;"
                    "const r=b.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()")
        if not at:
            problems.append("缩略图上没有 @ 按钮")
        else:
            cdp.click_at(float(at[0]), float(at[1]))
            time.sleep(0.5)
            val = cdp.js("(document.querySelector('.cnode.gen textarea[data-prompt]')||{}).value||''")
            print("提示词 =", repr(val))
            if "[图 1]" not in val:
                problems.append(f"点 @ 没有插入 [图 1]，实际是 {val!r}")
            cdp.shot("f_at")

        # ---- 6. 点线中间出剪刀 → 剪断 ----
        mid = path_point(cdp)
        if not mid:
            problems.append("找不到能点到的连线，剪刀没法验")
        else:
            print("线中段", mid)
            cdp.click_at(float(mid[0]), float(mid[1]))
            time.sleep(0.5)
            if not cdp.js("!!document.querySelector('.cvs-scissor')"):
                problems.append("点连线上没出现剪刀图标")
            cdp.shot("g_scissor")
            sc = center(cdp, ".cvs-scissor")
            if sc:
                cdp.click_at(sc[0], sc[1])
                time.sleep(0.7)
                left = cdp.js("document.querySelectorAll('.cvs-links path:not(.hit):not(.tmp)').length")
                print("剪断后连线数 =", left)
                if left:
                    problems.append("点了剪刀连线还在")
                cdp.shot("h_cut")

        # ---- 7. 从端口拖到空白 → 弹菜单 → 选完自动连上 ----
        p_out2 = center(cdp, ".cnode.mat .port.out")
        blank = blank_near(cdp, ".cnode.mat .port.out")
        print("端口/空白落点", p_out2, blank)
        if p_out2 and blank:
            # 分段发事件，方便看「链接有没有真的开始拖」
            cdp.mouse("mouseMoved", p_out2[0], p_out2[1])
            cdp.mouse("mousePressed", p_out2[0], p_out2[1])
            cdp.mouse("mouseMoved", p_out2[0] + 20, p_out2[1] + 10)
            time.sleep(0.3)
            print("拖线中 tmp 路径 =", cdp.js("document.querySelectorAll('.cvs-links path.tmp').length"),
                  "落点元素 =", cdp.js("(()=>{const e=document.elementFromPoint(%f,%f);"
                                     "return e?e.tagName+'.'+(e.className.baseVal!==undefined?e.className.baseVal:e.className):'null'})()"
                                     % (p_out2[0], p_out2[1])))
            cdp.mouse("mouseMoved", float(blank[0]), float(blank[1]))
            time.sleep(0.2)
            cdp.mouse("mouseReleased", float(blank[0]), float(blank[1]))
            time.sleep(0.8)
            if not cdp.js("!!document.querySelector('.cvs-menu')"):
                problems.append("连线拖到空白处没有弹出菜单")
            cdp.shot("i_blankmenu")
            cdp.js("(()=>{const b=document.querySelector('.cvs-menu [data-mk=videogen]');if(b)b.click();return !!b})()")
            time.sleep(1.0)
            both = cdp.js("(()=>{const gs=document.querySelectorAll('.cnode.gen');const es=document.querySelectorAll('.cvs-links path:not(.hit):not(.tmp)');return [gs.length,es.length]})()")
            print("生成节点/连线 =", both)
            if both[0] < 2 or both[1] < 1:
                problems.append(f"拖线到空白建节点后没自动连上（节点 {both[0]}，连线 {both[1]}）")
            cdp.shot("j_autolink")

        # ---- 9. 图片生成节点不该有视频参数，视频生成节点必须有 ----
        vsel = cdp.js("document.querySelectorAll('.cnode.gen [data-vopt=ratio]').length")
        print("视频节点比例下拉 =", vsel)
        if not vsel:
            problems.append("视频生成节点缺比例/分辨率/步数")
        imgsel = cdp.js("(()=>{const g=[...document.querySelectorAll('.cnode.gen')].find(x=>x.dataset.kind==='imagegen');"
                        "return g?g.querySelectorAll('[data-vopt]').length:-1})()")
        if imgsel:
            problems.append("图片生成节点上出现了视频参数")
        cdp.shot("k_videogen")

        # ---- 10. 窄屏 ----
        for w, h, tag, mob in ((820, 900, "w820", True), (390, 844, "w390", True)):
            size(cdp, w, h, mob)
            time.sleep(1.0)
            cdp.shot(f"m_{tag}")
            over = cdp.js("(()=>{const d=document.documentElement;return d.scrollWidth-d.clientWidth})()")
            print(tag, "横向溢出", over)
            if over and over > 2:
                problems.append(f"{tag} 画布横向溢出 {over}px")

        err = cdp.js("(window.__errs||[]).join(' | ')")
        if err:
            problems.append(f"页面报错：{err}")
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    print("\n问题：", problems or "无")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
