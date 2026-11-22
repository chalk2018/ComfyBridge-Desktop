"""1.06 提示词模板库：真实浏览器端到端验收。

前置：服务已在 8190 运行，ComfyUI 在线（不需要真的出图）。
运行：.venv\\Scripts\\python.exe tools\\promptlib_e2e.py
产物：.cache/promptlib_*.png，结果打印到终端。
"""
from __future__ import annotations
import atexit, base64, json, shutil, subprocess, sys, time, urllib.request
from datetime import datetime
from pathlib import Path

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
PORT = 9412
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_promptlib_e2e"
OUT = APP / ".cache"
HTTP = httpx.Client(base_url=BASE, timeout=30)
RESULTS: list[tuple[str, bool, str]] = []

# ---------------------------------------------------------------------------
# 用户数据保护：脚本会清空/改写模板与标签，跑之前必须整目录备份，跑完（含异常）
# 原样还原。绝不拿真实数据目录当测试沙箱 —— 之前误删过用户数据，这里强制兜底。
DATA_DIR = APP / "data" / "prompt_templates"
BACKUP_DIR = OUT / ("prompt_templates_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S"))


def backup_user_data():
    if not DATA_DIR.is_dir():
        return
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    shutil.copytree(DATA_DIR, BACKUP_DIR)
    print("已备份用户模板数据 ->", BACKUP_DIR)
    atexit.register(restore_user_data)   # 正常结束或异常退出都会还原


def restore_user_data():
    if not BACKUP_DIR.is_dir():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    kept = set()
    for f in BACKUP_DIR.glob("*.md"):
        shutil.copy2(f, DATA_DIR / f.name)
        kept.add(f.name)
    # 删掉测试期间新建、备份里没有的文件
    for f in DATA_DIR.glob("*.md"):
        if f.name not in kept:
            f.unlink()
    print("已还原用户模板数据 <-", BACKUP_DIR)


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" | " + str(detail)[:160]) if detail else ""))


class C:
    def __init__(self, ws):
        self.ws = websocket.create_connection(ws, timeout=60, suppress_origin=True)
        self.n = 0

    def call(self, method, params=None):
        self.n += 1; mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m: raise RuntimeError(str(m["error"]))
                return m.get("result", {})

    def js(self, e):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return r.get("result", {}).get("value")

    def shot(self, name):
        d = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / name).write_bytes(base64.b64decode(d["data"]))
        print("saved", name)

    def size(self, w, h, mobile=False):
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})


def route(c, r, admin=None):
    patch = {"route": r}
    if admin: patch["adminPage"] = admin
    HTTP.post("/api/ui", json={"patch": patch})
    c.js("(()=>{UI.route='%s';%s render();return true})()" % (r, ("UI.adminPage='%s';" % admin) if admin else ""))
    time.sleep(0.9)


def main():
    OUT.mkdir(exist_ok=True); PROFILE.mkdir(parents=True, exist_ok=True)
    backup_user_data()   # 第一件事：备份用户真实数据，atexit 保证任何退出路径都还原
    HTTP.post("/api/ui", json={"patch": {"route": "admin", "adminPage": "prompts",
                                         "promptDock": False, "promptDockPos": None}})
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--remote-allow-origins=*",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
        "--window-size=1440,960", BASE + "/#admin/prompts",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    for _ in range(80):
        time.sleep(0.35)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page" and "8190" in t.get("url", ""):
                    ws = t["webSocketDebuggerUrl"]; break
            if ws: break
        except Exception: pass
    assert ws, "连不上 CDP"
    c = C(ws); c.call("Runtime.enable"); c.call("Page.enable"); c.size(1440, 960)
    for _ in range(100):
        time.sleep(0.25)
        try:
            if c.js("typeof PromptLib!=='undefined' && !!S && !!S.jobs"): break
        except Exception: pass

    c.js("window.__errs=[];window.addEventListener('error',e=>window.__errs.push(String(e.message)));S.ui.promptDockPos=null;true")
    # 清掉上次异常退出可能留下的测试数据
    for t in HTTP.get("/api/prompt-templates").json()["data"]["templates"]:
        HTTP.delete("/api/prompt-templates/item/" + t["id"])
    c.js("PromptLib._state.loaded=false;PromptLib._state.templates=[];PromptLib._state.drafts.clear();PromptLib.ensureLoaded()")
    time.sleep(0.5)

    # 1. 菜单入口
    route(c, "admin", "prompts")
    menu = c.js("[...document.querySelectorAll('.admin-nav .item')].map(x=>x.textContent.trim())")
    check("左侧菜单含提示词模板库且位于画布之后", "4提示词模板库" in [m.replace(" ", "") for m in menu], menu)
    check("页面骨架存在", c.js("!!document.querySelector('.pt-page')"))

    # 2. 默认标签
    tags = c.js("PromptLib._state.tags.map(t=>t.name)")
    for n in ["角色", "场景", "道具", "故事板", "首尾帧", "角色替换", "音乐复刻", "声音克隆"]:
        check("默认标签 " + n, n in tags)
    check("标签颜色互不重复", c.js("new Set(PromptLib._state.tags.map(t=>t.color)).size===PromptLib._state.tags.length"))
    for f in ["图片.md", "视频.md", "音频.md", "标签.md"]:
        check("MD 数据文件存在：" + f, (APP / "data" / "prompt_templates" / f).is_file())

    # 标签增删改查
    c.js("document.querySelector('#ptTagMgr').click()"); time.sleep(0.3)
    c.js("""(()=>{document.querySelector('#ptNewTagName').value='临时标签';
      document.querySelector('#ptNewTagColor').value='#123456';
      document.querySelector('#ptNewTagBtn').click();return true})()"""); time.sleep(0.7)
    new_tag = c.js("PromptLib._state.tags.find(t=>t.name==='临时标签')")
    check("标签新增", bool(new_tag), new_tag)
    check("新增标签写入 MD", "临时标签" in (APP / "data" / "prompt_templates" / "标签.md").read_text(encoding="utf-8"))
    if new_tag:
        tid = new_tag["id"]
        c.js("""(()=>{const i=document.querySelector('[data-tagid="%s"]');i.value='改名标签';
          i.dispatchEvent(new Event('change'));return true})()""" % tid); time.sleep(0.7)
        check("标签改名", c.js("PromptLib._state.tags.some(t=>t.name==='改名标签')"))
        c.js("document.querySelector('[data-tagdel=%s]').click()" % tid); time.sleep(0.3)
        c.js("document.querySelector('#ptDelYes').click()"); time.sleep(0.7)
        check("标签删除", c.js("!PromptLib._state.tags.some(t=>t.id==='%s')" % tid))
        c.js("document.querySelectorAll('.modal-mask [data-close]').forEach(x=>x.click())"); time.sleep(0.3)

    # 3. 新增卡片（草稿态）
    c.js("document.querySelector('#ptAdd').click()"); time.sleep(0.3)
    check("新增后出现可编辑空卡片", c.js("!!document.querySelector('.pt-card.is-new')"))
    check("新卡片保存按钮初始灰色", c.js("document.querySelector('.pt-card.is-new .pt-save').disabled"))
    c.js("""(()=>{const c=document.querySelector('.pt-card.is-new');
      c.querySelector('[data-f=category]').value='video';c.querySelector('[data-f=category]').dispatchEvent(new Event('change'));
      const t=c.querySelector('[data-f=title]');t.value='赛博夜景运镜';t.dispatchEvent(new Event('input'));
      const x=c.querySelector('[data-f=content]');x.value='夜晚的城市霓虹，缓慢推镜\\n镜头穿过雨幕';x.dispatchEvent(new Event('input'));
      return true})()""")
    check("有改动后保存按钮可点", c.js("!document.querySelector('.pt-card.is-new .pt-save').disabled"))
    c.js("document.querySelector('.pt-card.is-new [data-addtag]').click()"); time.sleep(0.2)
    check("标签选择浮层弹出", c.js("!!document.querySelector('.pt-tagpop')"))
    c.js("document.querySelector('.pt-tagpop [data-tid=tag_scene]').click()"); time.sleep(0.2)
    c.js("document.body.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))"); time.sleep(0.2)
    c.js("document.querySelector('.pt-card.is-new .pt-save').click()"); time.sleep(0.8)
    saved = c.js("PromptLib._state.templates.map(t=>({title:t.title,cat:t.category,tags:t.tags,content:t.content}))")
    check("保存后写入模板列表", len(saved) == 1 and saved[0]["title"] == "赛博夜景运镜", saved)

    # 切页回来后卡片仍在（render 会重建骨架，必须重新填网格）
    route(c, "admin", "caps"); route(c, "admin", "prompts")
    check("切页返回后卡片仍然显示", c.js("document.querySelectorAll('.pt-card').length===1"))
    c.shot("promptlib_admin_desktop.png")

    # 4. 查询
    c.js("(()=>{const q=document.querySelector('#ptQ');q.value='不存在的关键字';q.dispatchEvent(new Event('input'));return true})()")
    check("搜索无结果", c.js("document.querySelectorAll('.pt-card').length===0"))
    c.js("(()=>{const q=document.querySelector('#ptQ');q.value='雨幕';q.dispatchEvent(new Event('input'));return true})()")
    check("按内容搜索命中", c.js("document.querySelectorAll('.pt-card').length===1"))
    c.js("(()=>{const q=document.querySelector('#ptQ');q.value='';q.dispatchEvent(new Event('input'));return true})()")
    c.js("(()=>{const s=document.querySelector('#ptCatFilter');s.value='image';s.dispatchEvent(new Event('change'));return true})()")
    check("按分类筛选生效", c.js("document.querySelectorAll('.pt-card').length===0"))
    c.js("(()=>{const s=document.querySelector('#ptCatFilter');s.value='';s.dispatchEvent(new Event('change'));return true})()")
    c.js("(()=>{const s=document.querySelector('#ptTagFilter');s.value='tag_scene';s.dispatchEvent(new Event('change'));return true})()")
    check("按标签筛选生效", c.js("document.querySelectorAll('.pt-card').length===1"))
    c.js("(()=>{const s=document.querySelector('#ptTagFilter');s.value='';s.dispatchEvent(new Event('change'));return true})()")

    # 5. textarea 高度限制
    h = c.js("(()=>{const x=document.querySelector('.pt-card .pt-content');return {min:getComputedStyle(x).minHeight,max:getComputedStyle(x).maxHeight,resize:getComputedStyle(x).resize}})()")
    check("内容框可纵向拉伸且限 3~10 行", h["resize"] == "vertical" and h["min"] == "76px" and h["max"] == "216px", h)

    # 6. 浮动小窗开关
    c.js("(()=>{document.querySelector('.pt-switch').click();return true})()")
    for _ in range(40):   # 开关走异步 /api/ui 落盘，等状态真正生效再断言
        time.sleep(0.15)
        try:
            if c.js("!!S.ui.promptDock"): break
        except Exception: pass
    time.sleep(0.3)
    check("管理台不显示小窗（即使开关打开）", c.js("!!document.querySelector('.pt-dock[hidden]') || !document.querySelector('.pt-dock')"))
    route(c, "studio")
    d = c.js("""(()=>{const e=document.querySelector('.pt-dock');if(!e||e.hidden)return null;const r=e.getBoundingClientRect();
      return {w:r.width,h:r.height,x:r.x,y:r.y,list:document.querySelectorAll('.pt-dcard').length,
              rightGap:innerWidth-r.right,bottomGap:innerHeight-r.bottom}})()""")
    check("创作台显示小窗且约 1:2", d and abs(d["w"] * 2 - d["h"]) < 3, d)
    check("小窗默认在右下角", d and d["x"] > 900 and d["y"] > 200, d)
    check("小窗不贴边，四周留空隙", d and d["rightGap"] >= 23 and d["bottomGap"] >= 23, d)
    check("小窗展示卡片", d and d["list"] == 1, d)
    # 标签筛选改成按钮 + 弹层：工具栏恒定一行，标签再多也不挤卡片列表
    tb = c.js("""(()=>{const b=document.querySelector('.pt-tagfilter-btn');if(!b)return null;
      const r=b.getBoundingClientRect();return {h:r.height,hasRow:!!document.querySelector('.pt-dock-tagrow')}})()""")
    check("标签工具栏只占一行（无平铺标签行）", tb and tb["h"] < 40 and not tb["hasRow"], tb)
    c.js("document.querySelector('.pt-tagfilter-btn').click()"); time.sleep(0.25)
    tg = c.js("""(()=>{const p=document.querySelector('.pt-tagfilter-pop');if(!p)return null;
      const items=[...p.querySelectorAll('.pt-tagfilter-item')];
      return {n:items.length,scrollH:p.querySelector('.pt-tagfilter-list').scrollHeight,
              clientH:p.querySelector('.pt-tagfilter-list').clientHeight}})()""")
    check("标签弹层含全部标签且可滚动", tg and tg["n"] >= 9, tg)   # 8 默认 + 全部标签项
    # 多选：点第一个不关闭，点第二个也不关闭，两个都进筛选
    c.js("document.querySelector('[data-tfid=tag_scene]').click()"); time.sleep(0.2)
    check("选第一个标签后弹层不关闭", c.js("!!document.querySelector('.pt-tagfilter-pop') && PromptLib._state.filters.tags.length===1"))
    c.js("document.querySelector('[data-tfid=tag_prop]').click()"); time.sleep(0.2)
    check("标签支持多选且弹层保持打开",
          c.js("!!document.querySelector('.pt-tagfilter-pop') && PromptLib._state.filters.tags.length===2"))
    # 在弹层内滚动 / 点击滚动条，弹层必须还在
    c.js("(()=>{const l=document.querySelector('.pt-tagfilter-list');l.scrollTop=40;l.dispatchEvent(new Event('scroll',{bubbles:true}));return true})()")
    time.sleep(0.2)
    check("弹层内滚动不会把弹层关掉", c.js("!!document.querySelector('.pt-tagfilter-pop')"))
    c.js("document.querySelector('[data-tfdone]').click()"); time.sleep(0.2)
    check("点完成后弹层关闭且保留多选",
          c.js("!document.querySelector('.pt-tagfilter-pop') && PromptLib._state.filters.tags.length===2"))
    # 按钮上显示 2 个标签名（彩色 chip），不是「已选 2 个」
    cur = c.js("""(()=>{const c=document.querySelector('.pt-tagfilter-cur');
      return {chips:[...c.querySelectorAll('.pt-tagfilter-chip')].map(x=>x.textContent),
              more:c.querySelector('.pt-tagfilter-more')?c.querySelector('.pt-tagfilter-more').textContent:'',
              text:c.textContent}})()""")
    check("按钮显示两个标签名而非「已选 N 个」",
          cur and len(cur["chips"]) == 2 and cur["chips"][0] == "场景" and cur["chips"][1] == "道具"
          and "已选" not in cur["text"] and cur["more"] == "", cur)
    # 选满 3 个：显示前 2 个 + +1，hover 出 tip 列出全部
    c.js("document.querySelector('.pt-tagfilter-btn').click()"); time.sleep(0.2)
    c.js("document.querySelector('[data-tfid=tag_board]').click()"); time.sleep(0.2)
    c.js("document.querySelector('[data-tfdone]').click()"); time.sleep(0.2)
    cur3 = c.js("""(()=>{const c=document.querySelector('.pt-tagfilter-cur');
      return {chips:c.querySelectorAll('.pt-tagfilter-chip').length,
              more:c.querySelector('.pt-tagfilter-more').textContent}})()""")
    check("超过 2 个显示 +N", cur3 and cur3["chips"] == 2 and cur3["more"] == "+1", cur3)
    c.js("(()=>{const b=document.querySelector('.pt-tagfilter-btn');b.dispatchEvent(new MouseEvent('mouseenter',{bubbles:false}));return true})()")
    time.sleep(0.2)
    tip = c.js("""(()=>{const t=document.querySelector('.pt-tag-tip');
      return t?[...t.querySelectorAll('.pt-tag-tip-chip')].map(x=>x.textContent):null})()""")
    check("hover 弹出 tip 列出全部已选标签", tip and len(tip) == 3, tip)
    c.js("document.querySelector('.pt-tagfilter-btn').dispatchEvent(new MouseEvent('mouseleave',{bubbles:false}))")
    check("移开鼠标 tip 消失", c.js("!document.querySelector('.pt-tag-tip')"))
    # 复位到全部标签
    c.js("document.querySelector('.pt-tagfilter-btn').click()"); time.sleep(0.2)
    c.js("document.querySelector('[data-tfclear]').click()"); time.sleep(0.2)
    c.js("document.querySelector('[data-tfdone]').click()"); time.sleep(0.2)
    check("清空筛选恢复全部", c.js("PromptLib._state.filters.tags.length===0"))
    c.shot("promptlib_dock_studio.png")

    # 6.5 回归：冷启动直达创作台（不经过管理台）浮窗也要自动带出数据
    c.call("Page.reload", {"ignoreCache": True})
    for _ in range(120):
        time.sleep(0.25)
        try:
            if c.js("typeof PromptLib!=='undefined' && !!S && !!S.jobs && PromptLib._state.loaded"):
                break
        except Exception:
            pass
    c.js("window.__errs=[];window.addEventListener('error',e=>window.__errs.push(String(e.message)));true")
    cold = c.js("""(()=>{const e=document.querySelector('.pt-dock');
      return {route:UI.route,dock:!!e&&!e.hidden,loaded:PromptLib._state.loaded,
              cards:document.querySelectorAll('.pt-dcard').length,
              templates:PromptLib._state.templates.length}})()""")
    check("冷启动直达创作台浮窗自动加载数据", cold["route"] == "studio" and cold["dock"] and cold["loaded"] and cold["cards"] >= 1, cold)

    # 7. 小窗拖拽
    before = c.js("Math.round(document.querySelector('.pt-dock').getBoundingClientRect().x)")
    c.js("""(()=>{const e=document.querySelector('.pt-dock'),h=e.querySelector('[data-drag]');
      const pd=new PointerEvent('pointerdown',{bubbles:true,clientX:100,clientY:100,pointerId:1});h.dispatchEvent(pd);
      const pm=new PointerEvent('pointermove',{bubbles:true,clientX:40,clientY:60,pointerId:1});h.dispatchEvent(pm);
      const pu=new PointerEvent('pointerup',{bubbles:true,clientX:40,clientY:60,pointerId:1});h.dispatchEvent(pu);
      return true})()"""); time.sleep(0.4)
    after = c.js("Math.round(document.querySelector('.pt-dock').getBoundingClientRect().x)")
    check("小窗可拖拽", after < before, f"{before}->{after}")

    # 7.5 收起态：可拖到屏幕底部；展开后自动回弹不超出屏幕；图标区分
    c.js("document.querySelector('[data-dock-min]').click()"); time.sleep(0.3)
    mini = c.js("""(()=>{const e=document.querySelector('.pt-dock'),b=e.querySelector('[data-dock-min]');
      const r=e.getBoundingClientRect();return {collapsed:e.classList.contains('collapsed'),
      icon:b.textContent.trim(),h:Math.round(r.height),bottom:Math.round(r.bottom),vh:innerHeight}})()""")
    check("收起后只剩标题栏且图标为展开方框",
          mini and mini["collapsed"] and mini["icon"] == "▢" and mini["h"] <= 42, mini)

    c.js("""(()=>{const e=document.querySelector('.pt-dock'),h=e.querySelector('[data-drag]');
      const pd=new PointerEvent('pointerdown',{bubbles:true,clientX:100,clientY:100,pointerId:7});h.dispatchEvent(pd);
      const pm=new PointerEvent('pointermove',{bubbles:true,clientX:100,clientY:900,pointerId:7});h.dispatchEvent(pm);
      const pu=new PointerEvent('pointerup',{bubbles:true,clientX:100,clientY:900,pointerId:7});h.dispatchEvent(pu);
      return true})()"""); time.sleep(0.3)
    low = c.js("""(()=>{const r=document.querySelector('.pt-dock').getBoundingClientRect();
      return {bottom:Math.round(r.bottom),vh:innerHeight}})()""")
    check("收起态可拖到屏幕底部留白处",
          low and low["bottom"] <= low["vh"] - 20 and low["bottom"] > low["vh"] - 60, low)

    c.js("document.querySelector('[data-dock-min]').click()"); time.sleep(0.3)
    restored = c.js("""(()=>{const e=document.querySelector('.pt-dock'),b=e.querySelector('[data-dock-min]');
      const r=e.getBoundingClientRect();return {collapsed:e.classList.contains('collapsed'),
      icon:b.textContent.trim(),bottom:Math.round(r.bottom),vh:innerHeight}})()""")
    check("展开后图标变回收起减号且下沿不超出屏幕",
          restored and not restored["collapsed"] and restored["icon"] == "−"
          and restored["bottom"] <= restored["vh"] - 20, restored)

    # 8. 小窗内新增 + 编辑 + 删除
    c.js("document.querySelector('[data-dadd]').click()"); time.sleep(0.3)
    check("小窗内可打开编辑面板", c.js("!!document.querySelector('.pt-dock-editor:not([hidden])')"))
    c.js("""(()=>{const e=document.querySelector('.pt-dock-editor');
      e.querySelector('[data-ed-f=title]').value='小窗建的模板';
      e.querySelector('[data-ed-f=title]').dispatchEvent(new Event('input'));
      const s=e.querySelector('[data-ed-f=category]');s.value='audio';s.dispatchEvent(new Event('change'));
      const x=e.querySelector('[data-ed-f=content]');x.value='低沉的环境音';x.dispatchEvent(new Event('input'));
      e.querySelector('.pt-ed-save').click();return true})()"""); time.sleep(0.9)
    check("小窗保存成功", c.js("PromptLib._state.templates.some(t=>t.title==='小窗建的模板')"))

    # 9. 管理台/历史隐藏
    route(c, "admin", "prompts")
    check("切回管理台小窗隐藏", c.js("document.querySelector('.pt-dock').hidden"))
    route(c, "gallery")
    check("历史页小窗隐藏", c.js("document.querySelector('.pt-dock').hidden"))
    route(c, "canvas")
    check("画布页小窗显示", c.js("!document.querySelector('.pt-dock').hidden"))

    # 10. 响应式：窄屏无溢出
    for w, h in [(1440, 900), (1024, 768), (768, 900), (390, 844)]:
        c.size(w, h, w < 600); route(c, "admin", "prompts"); time.sleep(0.4)
        o = c.js("""(()=>{const p=document.querySelector('.pt-page');const g=document.querySelector('.pt-grid');
          return {body:document.documentElement.scrollWidth,inner:innerWidth,
            page:p?p.scrollWidth:0,pageW:p?p.clientWidth:0,grid:g?g.scrollWidth:0,gridW:g?g.clientWidth:0}})()""")
        check(f"{w}px 管理台无横向溢出", o["body"] <= o["inner"] + 1 and o["page"] <= o["pageW"] + 1, o)
    c.shot("promptlib_admin_mobile.png")
    c.size(390, 844, True); route(c, "studio"); time.sleep(0.4)
    o = c.js("""(()=>{const e=document.querySelector('.pt-dock');const r=e.getBoundingClientRect();
      return {x:r.x,right:r.right,w:r.width,h:r.height,vw:innerWidth,vh:innerHeight,body:document.documentElement.scrollWidth}})()""")
    check("390px 小窗不溢出屏幕", o["x"] >= 0 and o["right"] <= o["vw"] + 1 and o["body"] <= o["vw"] + 1, o)
    c.shot("promptlib_dock_mobile.png")

    # 11. 无 JS 异常
    errs = c.js("window.__errs || []")
    check("无 JS 运行异常", not errs, errs)

    # 清理测试数据
    for t in c.js("PromptLib._state.templates.map(t=>t.id)"):
        HTTP.delete("/api/prompt-templates/item/" + t)
    HTTP.post("/api/ui", json={"patch": {"promptDock": False, "route": "admin", "adminPage": "prompts"}})

    proc.terminate()
    bad = [x for x in RESULTS if not x[1]]
    print("\n=== %d/%d passed ===" % (len(RESULTS) - len(bad), len(RESULTS)))
    for n, ok, d in bad: print("FAILED:", n, d)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
