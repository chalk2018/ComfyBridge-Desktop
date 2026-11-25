"""真实浏览器逐功能点 E2E：用 CDP 驱动本机 Edge，逐项点击验证。

为什么不用 Playwright：避免下载浏览器到 C 盘。venv 里已有 websocket-client，
直接连 Edge 的 DevTools 协议即可做真实点击、读取 DOM、抓 JS 异常。

前置：应用已在 http://127.0.0.1:8190 运行、ComfyUI 在 8188 在线、队列空闲。
运行：
    .venv\\Scripts\\python.exe tests\\ui_e2e_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9223
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_e2e"
CONFIG = APP / "config.yaml"
HTTP = httpx.Client(base_url=BASE, timeout=30.0)
RESULTS: list[tuple[str, bool, str]] = []
CONFIG_BACKUP = ""


def check(name: str, ok: Any, detail: str = "") -> None:
    """记录一项测试结果并即时打印。"""
    passed = bool(ok)
    RESULTS.append((name, passed, detail[:240]))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail[:200]}" if detail else ""), flush=True)


def api(path: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    """直接调后端接口（配合 UI 验证服务端状态）。"""
    resp = HTTP.request(method, path, json=body) if body is not None else HTTP.request(method, path)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": resp.text[:200]}


class CDP:
    """极简 Chrome DevTools 客户端。"""

    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=30, suppress_origin=True)
        self.seq = 0
        self.events: list[dict[str, Any]] = []

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """发一条命令并等待同 id 的响应；其它消息存为事件。"""
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
        """在页面上下文执行 JS 并取回值。"""
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS 异常: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 20.0) -> bool:
        """轮询等待页面里的条件成立。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.js(expression):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.35)
        return False

    def pause(self, ms: int = 450) -> None:
        """给前端异步请求 + 重绘留时间。"""
        self.js(f"new Promise(r=>setTimeout(()=>r(true),{ms}))")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def click(cdp: CDP, selector: str) -> Any:
    """真实点击一个 DOM 元素（不是直接调函数）。"""
    return cdp.js(f"(() => {{ const e=document.querySelector({json.dumps(selector)}); if(!e) return false; e.click(); return true; }})()")


def set_editor(cdp: CDP, text: str) -> Any:
    """在 contenteditable 编辑器里设置纯文本并触发序列化（替代旧的 textarea .value）。"""
    return cdp.js(f"""(() => {{
        const t=document.querySelector('#promptInput');
        if(!t) return false;
        t.textContent = {json.dumps(text)};
        t.dispatchEvent(new Event('input', {{bubbles:true}}));
        return true;
    }})()""")


def get_editor(cdp: CDP) -> str:
    """读取编辑器里序列化后的提示词（与提交到服务端的一致）。"""
    return cdp.js("(()=>{const t=document.querySelector('#promptInput'); return t? t.textContent.trim():'';})()") or ""


def wait_idle(cdp: CDP, timeout: float = 150.0) -> bool:
    """等服务端没有正在跑/排队的任务，便于再次发送（避免忙时拦截）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api("/api/state")
        if not any(r["state"] in ("queued", "running") for j in st["jobs"] for r in j["runs"]):
            return True
        time.sleep(1)
    return False


def _fresh_profile_and_port() -> tuple[Path, int]:
    """每次跑测试都用一份全新的浏览器配置 + 独立调试端口。

    为什么：固定 profile 时，上一轮没退干净的 Edge 会让新进程直接复用它，
    连上的还是那条早已失效的旧页面（报错形如「Cannot find default execution
    context」）。配置目录里塞了磁盘缓存，顺便也就没有旧 app.js 的问题了。
    """
    port = 9223 + (os.getpid() % 60)
    # 端口会复用（同一进程号段），再加一段随机后缀，避免每次都去删上一轮那份大缓存
    profile = PROFILE.parent / f"{PROFILE.name}-{port}-{uuid.uuid4().hex[:6]}"
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True, exist_ok=True)
    return profile, port


def start_browser() -> tuple[subprocess.Popen, CDP]:
    """启动带调试端口的 Edge 并连上页面 target。"""
    profile, port = _fresh_profile_and_port()
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-extensions", "--no-first-run", "--disable-background-networking",
        "--disk-cache-size=1", "--media-cache-size=1",
        "--remote-allow-origins=*", f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}", "--window-size=1440,900", BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    targets: list[dict[str, Any]] = []
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                targets = json.loads(resp.read().decode())
            page = next((t for t in targets if t.get("type") == "page" and "8190" in t.get("url", "")), None)
            if page:
                cdp = CDP(page["webSocketDebuggerUrl"])
                cdp.call("Runtime.enable")
                cdp.call("Page.enable")
                cdp.call("Log.enable")
                # 前端脚本刚改过时，Edge 的磁盘缓存会拿旧 app.js 跑（表现像「改了没生效」），
                # 测试一律绕过缓存并强刷一次。
                cdp.call("Network.enable")
                cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
                return proc, cdp
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("连不上 Edge 的调试端口")


def run_checks(cdp: CDP) -> None:
    """逐项点击测试所有功能点。"""
    # ---------- A. 页面加载与控制台 ----------
    check("A1 页面渲染出创作台骨架", cdp.wait("!!document.querySelector('.app .topbar')", 20),
          "topbar 是 .app 的直接子元素")
    check("A2 四个区域都在", cdp.js("['.rail','.chat','.composer','.drawer'].every(s=>!!document.querySelector(s))"))
    check("A3 顶栏显示已连接", cdp.js("document.querySelector('.health .txt').textContent.includes('已连接')"))
    # 能力数量/未配置数量按服务端实际计算：以后启用新能力时这里不用再改数字
    caps = api("/api/state").get("capabilities") or []
    unconf = sum(1 for c in caps if not (c.get("workflows") or []))
    check(f"A4 左栏 {len(caps)} 个能力、{unconf} 个未配置标签", cdp.js(
        f"document.querySelectorAll('.rail .item').length==={len(caps)} && "
        f"document.querySelectorAll('.rail .item .tag').length==={unconf}"),
        str(cdp.js("[document.querySelectorAll('.rail .item').length,"
                   " document.querySelectorAll('.rail .item .tag').length]")))
    check("A5 控制台没有 JS 异常", not any(
        e.get("method") == "Runtime.exceptionThrown" for e in cdp.events), str(len(cdp.events)) + " 条事件")
    check("A6 配置红条不误报", cdp.js("!document.querySelector('.configerr')"))

    # 复位到「图片生成 · 文生图」：服务端会记住上次选的能力，跑过设定图的测试套件后
    # 页面会停在角色设定图，导致这里的发送按钮/档位按钮全都取不到
    if cdp.js("UI.capId!=='image.generate'"):
        click(cdp, '.rail [data-cap="image.generate"]')
        cdp.pause(400)
    cdp.js("(()=>{UI.mode='t2i'; UI.refs=[]; UI.prompt=''; refreshComposer();})()")
    cdp.pause(300)
    check("B0 复位到图片生成 · 文生图", cdp.js("UI.capId==='image.generate'&&UI.mode==='t2i'"),
          str(cdp.js("[UI.capId,UI.mode]")))

    # ---------- B. 输入区 ----------
    # 服务端会记住上次的草稿，先清空再验证「空描述」行为（contenteditable 用 set_editor）
    set_editor(cdp, "")
    cdp.pause(300)
    check("B1 空描述时发送按钮置灰 + 浅灰说明", cdp.js(
        "document.querySelector('#sendBtn').disabled && getComputedStyle(document.querySelector('#emptyHint')).display!=='none'"))
    set_editor(cdp, "E2E 测试：窗台上的橘猫，午后阳光")
    cdp.pause(350)
    check("B2 输入后发送按钮启用、说明隐藏", cdp.js(
        "!document.querySelector('#sendBtn').disabled && getComputedStyle(document.querySelector('#emptyHint')).display==='none'"))
    check("B3 草稿写到服务端", "E2E 测试" in str(api("/api/state")["ui"]["drafts"]))
    grades_count = cdp.js("document.querySelectorAll('[data-grade]').length")
    check("B4 档位按钮存在", grades_count >= 2, f"{grades_count} 档")
    click(cdp, '[data-grade="快速（16:9）"]')
    cdp.pause(450)
    check("B5 点击档位后选中态变化", cdp.js("document.querySelector('[data-grade=\"快速（16:9）\"]').classList.contains('on')"))
    check("B6 档位选择同步到服务端", api("/api/state")["ui"]["drafts"].get("image.generate", {}).get("grade") == "快速（16:9）")
    click(cdp, '[data-grade="默认"]')
    cdp.pause(300)

    # ---------- C. 参考图上传与上限（上传属于图生图，先切模式） ----------
    check("C0a 文生图模式下上传被拦下（需求 #1：文生图不能传图）", cdp.js("""(async () => {
        UI.refs = [];
        const blob = new Blob([new Uint8Array([137,80,78,71,13,10,26,10])], {type:'image/png'});
        await uploadFiles([new File([blob], 'blocked.png', {type:'image/png'})]);
        return UI.refs.length===0;
    })()"""))
    click(cdp, '[data-mode="i2i"]')
    cdp.pause(400)
    cdp.js("""(async () => {
        const blob = new Blob([new Uint8Array([137,80,78,71,13,10,26,10])], {type:'image/png'});
        const f = new File([blob], 'e2e_ref.png', {type:'image/png'});
        await uploadFiles([f]);
        return true;
    })()""")
    cdp.pause(900)
    check("C1 上传参考图后出现缩略图和 ×", cdp.js(
        "document.querySelectorAll('.refs .ref-thumb').length===1 && !!document.querySelector('[data-rmref]')"))
    cdp.js("""(async () => {
        const blob = new Blob([new Uint8Array([137,80,78,71,13,10,26,10])], {type:'image/png'});
        const f = new File([blob], 'e2e_ref.png', {type:'image/png'});
        await uploadFiles([f,f,f,f]);
        return true;
    })()""")
    cdp.pause(900)
    check("C2 超过能力声明上限被拦住（最多 3 张）", cdp.js("document.querySelectorAll('.refs .ref-thumb').length<=3"))
    cdp.js("""(async () => { const ids=[...document.querySelectorAll('[data-rmref]')].map(e=>e.dataset.rmref);
        for (const id of ids) { await api('/api/uploads/'+id,{method:'DELETE'}); } UI.refs=[]; refreshComposer(); return true; })()""")
    cdp.pause(400)
    check("C3 移除参考图后缩略图清空", cdp.js("document.querySelectorAll('.refs .ref-thumb').length===0"))
    # D 段做纯文生图，切回文生图模式（参考图已清空）
    click(cdp, '[data-mode="t2i"]')
    cdp.pause(400)

    # ---------- D. 真实生成 + 卡片交互 ----------
    before_jobs = len(api("/api/state")["jobs"])
    before_cards = cdp.js("document.querySelectorAll('.job').length")
    check("D1 点击发送提交任务", click(cdp, "#sendBtn"))
    cdp.pause(700)
    check("D2 立刻出现一张新卡片", cdp.js(f"document.querySelectorAll('.job').length>{before_cards}"),
          f"{before_cards} -> 新卡")
    check("D3 输入框已清空", cdp.js("document.querySelector('#promptInput').textContent.trim()===''"))
    # 页面里本来就有历史成品，不能只看 done 卡：按服务端状态等「这一张」真正完成
    latest = None
    deadline = time.time() + 150
    while time.time() < deadline:
        st = api("/api/state")
        if len(st["jobs"]) > before_jobs:
            latest = st["jobs"][-1]
            if latest["runs"][0]["state"] in ("done", "failed", "canceled"):
                break
        time.sleep(1)
    check("D4 真实出图（服务端状态 done）", bool(latest and latest["runs"][0]["state"] == "done"),
          str(latest["runs"][0]["state"]) if latest else "没有新任务")
    check("D4b 成品图片在页面里加载成功", cdp.wait(
        "[...document.querySelectorAll('.job.done .tile img')].some(i=>i.complete && i.naturalWidth>0)", 30))
    run1 = latest["runs"][0]
    check("D5 任务状态 done / 进度 100", run1["state"] == "done" and run1["pct"] == 100)
    check("D6 种子是回读到的真实值", isinstance(run1.get("seed"), int) and run1["seed"] > 0, str(run1.get("seed")))
    state = api("/api/state")
    archive_root = Path(state["service"]["archiveDir"]).resolve()
    file_path = Path(run1["files"][0]["path"]) if run1.get("files") else None
    check("D7 成品归拢到 <成果目录>/<能力名>/", bool(file_path and file_path.exists() and archive_root in file_path.parents
          and file_path.parent.name == "图片生成"), str(file_path))
    check("D8 ComfyUI output 根目录没有残留本次文件", not list(
        Path("D:/ComfyUI-0.37.1/ComfyUI/output").glob(f"*{run1['files'][0]['filename']}")) if run1.get("files") else False)
    check("D9 成品可访问 /api/media", HTTP.get(f"/api/media?run={run1['id']}&i=0").status_code == 200)
    check("D10 卡底显示种子 + 三个动作按钮", cdp.js(
        "!!document.querySelector('.seedline mono') || !!document.querySelector('.seedline b')") and cdp.js(
        "document.querySelectorAll('.job.done [data-qadd],.job.done [data-again],.job.done [data-refine]').length>=3"))
    check("D11 尺寸挪到右下角、不再用挡按钮的「成品 N」标签", cdp.js(
        "!document.querySelector('.tile-label') && !!document.querySelector('.tile-size') && "
        "(document.querySelector('.tile-size').textContent||'').indexOf('×') >= 0"))
    click(cdp, ".job.done .tile [data-zoom]")
    cdp.pause(300)
    # 本轮需求：弹窗删掉「复制图片」按钮（Ctrl+C 复制保留），加「放大」，打开目录/下载移到种子行
    check("D12 大图弹窗：路径 + 放大 + 打开目录 + 下载（无复制图片按钮）", cdp.js(
        "!!document.querySelector('#mask') && !!document.querySelector('#zoomExpand') && "
        "!document.querySelector('#copyImg') && !!document.querySelector('#openDir') && !!document.querySelector('#zoomDl')"))
    check("D13 弹窗里显示归档后的路径", cdp.js(
        "document.querySelector('#mask .m-foot').textContent.includes('ComfyBridgeOutput')"))
    click(cdp, "#mask [data-close]")
    cdp.pause(250)
    check("D14 关闭弹窗", cdp.js("!document.querySelector('#mask')"))
    click(cdp, ".job.done [data-refine]")
    cdp.pause(300)
    check("D15 基于上一条带回描述和档位", cdp.js(
        "document.querySelector('#promptInput').textContent.trim().length>0") and
        bool(api("/api/state")["ui"]["drafts"].get("image.generate", {}).get("prompt")))

    # 忙时拦截（UI 上再点发送，此时若有任务在跑会被拦；空闲则提交真实任务）
    set_editor(cdp, "E2E 连点测试")
    active = api("/api/state")
    has_active = any(r["state"] in ("queued", "running") for j in active["jobs"] for r in j["runs"])
    click(cdp, "#sendBtn")
    cdp.pause(450)
    if has_active:
        check("D16 忙时弹一条拦截提示、输入框文字保留", cdp.js(
            "!!document.querySelector('.tblock') && document.querySelector('#promptInput').textContent.includes('E2E 连点测试')"))
    else:
        check("D16 空闲时正常提交（不排队）", cdp.js("document.querySelectorAll('.job').length>=2"))
        # 等新任务结束，保持队列干净
        cdp.wait("[...document.querySelectorAll('.job')].every(j=>!j.querySelector('.progress')||j.classList.contains('done'))", 120)

    # ---------- E. 管理台：能力与工作流 ----------
    click(cdp, '[data-nav="admin"]')
    cdp.pause(350)
    # 五个页面：能力与工作流 / 档位表 / 设定图模板 / 服务设置 / 自检（设定图模板是新增模块）
    # 本轮需求：设定图模板移入档位表（模态框），菜单从 5 页变 4 页
    check("E1 进入管理台、四页菜单齐全（设定图模板已移入档位表）", cdp.js(
        "document.querySelectorAll('.admin-nav [data-page]').length===4 && "
        "['caps','grades','service','diag'].every(k=>!!document.querySelector(`.admin-nav [data-page=\"${k}\"]`)) && "
        "!document.querySelector('.admin-nav [data-page=\"sheet\"]')"),
        str(cdp.js("[...document.querySelectorAll('.admin-nav [data-page]')].map(e=>e.dataset.page)")))
    check("E2 能力与工作流页列出 17 个能力", cdp.js("document.querySelectorAll('.cap-row').length===17"))
    toggle_before = api("/api/state")["capabilities"][0]["enabled"]
    click(cdp, '[data-toggle="image.generate"]')
    cdp.pause(500)
    check("E3 启停开关生效", api("/api/state")["capabilities"][0]["enabled"] != toggle_before)
    click(cdp, '[data-toggle="image.generate"]')
    cdp.pause(500)
    check("E4 再点一次恢复启用", api("/api/state")["capabilities"][0]["enabled"] == toggle_before)
    caps_before = len(api("/api/state")["capabilities"])
    # 能力清单固定：卡片上的复制/删除、页面上的「重读配置」都去掉了
    check("E5 能力卡片没有复制/删除按钮",
          cdp.js("document.querySelectorAll('.cap-row [data-copy],.cap-row [data-del]').length===0"))
    check("E6 页面没有「重读配置」按钮、能力数不变",
          cdp.js("!document.querySelector('[data-reload]')")
          and len(api("/api/state")["capabilities"]) == caps_before)
    # 「档位表」下面挂子菜单，一点直接跳到对应能力的档位表
    check("E7 档位表下挂能力子菜单", cdp.js(
        "document.querySelectorAll('.nav-sub [data-grades-cap]').length===2"),
        str(cdp.js("[...document.querySelectorAll('.nav-sub [data-grades-cap]')].map(e=>e.dataset.gradesCap)")))
    click(cdp, '.nav-sub [data-grades-cap="sheet.character"]')
    cdp.pause(500)
    # 本轮需求：档位表去掉画法/参考切换（只留子功能+版式），所以断言「版式」在、且没有画法切换按钮
    check("E8 点子菜单直达角色设定图档位表（仅子功能+版式）", cdp.js(
        "UI.adminPage==='grades'&&UI.capId==='sheet.character'"
        "&&document.body.textContent.includes('版式')&&!document.querySelector('[data-combo-ink]')"),
        str(cdp.js("[UI.adminPage,UI.capId]")))
    # 子菜单也能点回图片生成；后面的 F 段测的是图片生成的档位表
    click(cdp, '.nav-sub [data-grades-cap="image.generate"]')
    cdp.pause(500)
    check("E9 子菜单能切回图片生成档位表", cdp.js("UI.capId==='image.generate'"),
          str(cdp.js("[UI.adminPage,UI.capId]")))

    # ---------- F. 档位表（用户报的两个问题） ----------
    click(cdp, '[data-page="grades"]')
    cdp.pause(450)
    check("F1 档位表四步结构齐全", cdp.js(
        "!!document.querySelector('.wffile') && document.body.textContent.includes('② 参数节点') && document.body.textContent.includes('④ 新增档位') && document.body.textContent.includes('⑤ 已有档位')"))
    check("F2 2 个工作流 tab 保留", cdp.js("document.querySelectorAll('[data-wfswitch]').length===2"))
    css_probe = cdp.js("""[...document.querySelectorAll('.node-row')].slice(0,4).map(row => {
        const id=row.querySelector('.id'), cls=row.querySelector('.cls');
        return {txt:id&&id.textContent.trim(), iw:id&&Math.round(id.getBoundingClientRect().width),
                ir:id&&Math.round(id.getBoundingClientRect().right), cl:cls&&Math.round(cls.getBoundingClientRect().left)};
    })""")
    check("F3 节点 id 完整显示且不与 class 名重叠", cdp.js("""[...document.querySelectorAll('.node-row')].every(row => {
        const id=row.querySelector('.id'), cls=row.querySelector('.cls');
        if(!id||!cls) return true;
        // id 内容没有被裁掉，且两列矩形不重叠（真实 id 有 459_451 也有 13 这种短 id）
        return id.scrollWidth <= id.clientWidth+1 && id.getBoundingClientRect().right <= cls.getBoundingClientRect().left+1;
    })"""), str(css_probe))
    check("F4 参数节点与不用设的节点都列出", cdp.js(
        "document.querySelectorAll('[data-picknode]').length>=8 && document.querySelectorAll('.node-row.no').length>=3"))
    before_checked = cdp.js("document.querySelectorAll('[data-picknode] input:checked').length")
    picked_id = cdp.js("""(() => {
        const box=[...document.querySelectorAll('[data-picknode] input')].find(b=>!b.checked);
        if(!box) return '';
        box.click();
        return box.closest('[data-picknode]').dataset.picknode;
    })()""")
    cdp.pause(1200)
    after_checked = cdp.js("document.querySelectorAll('[data-picknode] input:checked').length")
    check("F5 点击 checkbox 真的勾上（核心 bug 回归）", after_checked == before_checked + 1,
          f"{before_checked} -> {after_checked}, node={picked_id}")
    fields_now = api("/api/state")["capabilities"][0]["workflows"][0]["fields"]
    check("F6 勾中的节点参数写进服务端配置", any(str(f["node"]) == str(picked_id) for f in fields_now), str(fields_now)[:180])
    check("F7 右侧 ④ 出现该节点的参数编辑块", cdp.js(
        f"[...document.querySelectorAll('[data-drf]')].some(e=>e.dataset.drf.startsWith('{picked_id}|'))"))
    # 再点一次取消勾选
    cdp.js(f"""(() => {{ const row=[...document.querySelectorAll('[data-picknode]')].find(e=>e.dataset.picknode==='{picked_id}');
        row.querySelector('input').click(); return true; }})()""")
    cdp.pause(1100)
    check("F8 再点一次可以取消勾选", cdp.js("document.querySelectorAll('[data-picknode] input:checked').length") == before_checked)

    # ---------- G. 档位增删改 ----------
    test_grade = "E2E档位"
    # 真实点击【＋ 添加】造档位：先勾一个节点，填名字，再点按钮
    cdp.js(f"""(() => {{ const row=[...document.querySelectorAll('[data-picknode]')].find(e=>e.dataset.picknode==='{picked_id}');
        if(row && !row.querySelector('input').checked) row.querySelector('input').click(); return true; }})()""")
    cdp.pause(1000)
    cdp.js("""(() => { const e=document.querySelector('#newGradeName'); if(!e) return false;
        e.value='E2E档位'; e.dispatchEvent(new Event('input',{bubbles:true})); return true; })()""")
    check("G0 UI 的【＋ 添加】按钮可点", click(cdp, '[data-gadd]'))
    cdp.pause(1200)
    grade_names = [g["name"] for g in api("/api/state")["capabilities"][0]["workflows"][0]["grades"]]
    check("G1 通过 UI 新增档位出现在配置里", test_grade in grade_names, str(grade_names))
    check("G2 档位拷贝文件生成", (APP / "workflows" / "ComfyBridge" / f"文生图__{test_grade}.json").exists())
    renamed = "E2E改名"
    res = api("/api/workflows/%E6%96%87%E7%94%9F%E5%9B%BE", "POST",
              {"gradePatch": {"name": test_grade, "newName": renamed, "note": "改过的说明",
                              "params": {"459_456": {"width": 960, "height": 960}}}})
    cdp.pause(400)
    new_names = [g["name"] for g in api("/api/state")["capabilities"][0]["workflows"][0]["grades"]]
    check("G3 档位改名生效（定位与改名分开）", res.get("ok") and renamed in new_names and test_grade not in new_names, str(new_names))
    grade_row = next((g for g in api("/api/state")["capabilities"][0]["workflows"][0]["grades"] if g["name"] == renamed), {})
    check("G4 档位说明与参数保存生效", grade_row.get("note") == "改过的说明" and grade_row.get("params", {}).get("width") == 960,
          str(grade_row)[:180])
    check("G5 改名后旧拷贝被清理", not (APP / "workflows" / "ComfyBridge" / f"文生图__{test_grade}.json").exists())
    del_res = api(f"/api/workflows/%E6%96%87%E7%94%9F%E5%9B%BE/grades/{urllib.parse.quote(renamed)}", "DELETE")
    check("G6 删除档位", del_res.get("ok") is True)
    def_res = api("/api/workflows/%E6%96%87%E7%94%9F%E5%9B%BE/grades/%E9%BB%98%E8%AE%A4", "DELETE")
    check("G7 默认档不可删", def_res.get("ok") is False)
    check("G8 默认档控件只读", cdp.wait("!!document.querySelector('.grow.def')", 5))

    # ---------- H. 服务设置与自检 ----------
    click(cdp, '[data-page="service"]')
    cdp.pause(600)
    check("H1 服务设置四块齐全", cdp.js(
        "['连接','成品存放','路径','存储与清理'].every(t=>document.body.textContent.includes(t))"))
    check("H2 没有访问口令字段", cdp.js("!document.body.textContent.includes('访问口令')"))
    check("H3 当前占用显示真实目录数据", cdp.js(
        "document.querySelector('#storageText').textContent.includes('MB')"),
        str(cdp.js("document.querySelector('#storageText').textContent"))[:120])
    check("H4 有「打开成果目录」入口", cdp.js("!!document.querySelector('#openArchive')"))
    old_archive = api("/api/state")["service"]["archiveDir"]
    custom_archive = str(APP / "data" / "E2E成品").replace("\\", "/")
    saved = api("/api/config", "POST", {"patch": {"archiveDir": custom_archive}})
    check("H5 成果目录可以指定成别的目录（需求 §2.4）", saved.get("ok") is True, str(saved))
    check("H6 指定结果立刻在内存生效", api("/api/state")["service"]["archiveDir"] == custom_archive)
    api("/api/config", "POST", {"patch": {"archiveDir": old_archive, "keepDays": 7}})
    cdp.pause(300)
    check("H7 恢复默认成果目录", api("/api/state")["service"]["archiveDir"] == old_archive)
    click(cdp, '[data-page="diag"]')
    # 进自检页是异步的（拉接口 → 写草稿 → 重绘），固定 sleep 在机器忙时会抢跑；
    # 等到 DOM 里真的出现两行自检结果再断言，既是更严的判定也不会假失败。
    got_diag = cdp.wait("document.querySelectorAll('.diag-row').length===2", 10)
    check("H8 自检自动出两项结果", got_diag, str(cdp.js("""(() => ({
        route: UI.route, page: UI.adminPage, cap: UI.capId,
        rows: Array.isArray(S.diagRows) ? S.diagRows.length : String(S.diagRows),
        dom: document.querySelectorAll('.diag-row').length,
        busy: !!UI._diagLoading, navPending: !!UI._navPending,
        main: (document.querySelector('.admin-main') || {}).textContent
              ? document.querySelector('.admin-main').textContent.trim().slice(0, 90) : '(无 admin-main)',
    }))()"""))[:300])
    check("H9 自检项包含连接与目录可写", cdp.js(
        "document.querySelector('.diag-row') && document.body.textContent.includes('成果目录可写')"))

    # ---------- I. 历史页 ----------
    click(cdp, '[data-nav="gallery"]')
    cdp.pause(450)
    check("I1 历史网格显示真实成品", cdp.js("document.querySelectorAll('.gallery .card img').length>=1"))
    check("I2 能力/日期两个筛选都有", cdp.js("!!document.querySelector('#gfilter') && !!document.querySelector('#gdate')"))
    cdp.js("(()=>{const b=document.querySelector('[data-sel]'); if(b){b.click();} return true;})()")
    cdp.pause(250)
    check("I3 多选 checkbox 勾选生效", cdp.js("document.querySelector('[data-sel]').checked") or cdp.js("UI.selected.size>=1"))
    check("I4 批量下载按钮计数更新", cdp.js("document.querySelector('#batchDl').textContent.includes('1')"))
    check("I5 批量打包接口返回 zip", api("/api/download/batch", "POST", {"items": [
        {"run": run1["id"], "index": 0}]}) is not None and
        HTTP.post("/api/download/batch", json={"items": [{"run": run1["id"], "index": 0}]}).content[:2] == b"PK")

    # ---------- J. 切页不丢 / 多窗口一致 ----------
    click(cdp, '[data-nav="studio"]')
    cdp.pause(350)
    check("J1 从管理台回来任务卡还在", cdp.js("document.querySelectorAll('.job.done').length>=1"))
    check("J2 服务端状态唯一（刷新后仍在）", len(api("/api/state")["jobs"]) >= 1)

    # ---------- K. 配置损坏红条 + 页面直改 ----------
    original = api("/api/config/raw")["data"]
    CONFIG.write_text("service: [this is broken\n", encoding="utf-8")
    reload_bad = api("/api/config/reload", "POST", {})
    check("K1 配置损坏时重读明确报错", reload_bad.get("ok") is False and bool(reload_bad.get("reason")), str(reload_bad)[:160])
    check("K2 页面顶部出现红条", cdp.wait("!!document.querySelector('.configerr')", 8))
    check("K3 红条指出具体问题", cdp.js(
        "document.querySelector('.configerr').textContent.includes('第') || document.querySelector('.configerr').textContent.includes('配置')"))
    check("K4 提供页面直接修改入口", cdp.js("!!document.querySelector('#fixConfig')"))
    CONFIG.write_text(original, encoding="utf-8")
    reload_ok = api("/api/config/reload", "POST", {})
    check("K5 恢复配置后重读成功、红条消失", reload_ok.get("ok") is True and cdp.wait("!document.querySelector('.configerr')", 8))

    # ---------- L. 对外接口 ----------
    # 看菜单只列「已启用且配了工作流」的能力：图片生成 + 角色设定图（新增模块）
    listed = api("/api/v1/capabilities")["capabilities"]
    enabled_ids = {c["id"] for c in api("/api/state")["capabilities"] if c.get("enabled") and c.get("workflows")}
    check("L1 看菜单只列可用能力", {c["id"] for c in listed} == enabled_ids and "sheet.character" in enabled_ids,
          str(sorted(c["id"] for c in listed)))
    check("L2 健康检查", api("/api/v1/health")["comfy_online"] is True)
    check("L3 技能文本可读", "四步" in HTTP.get("/api/v1/skill").text)
    check("L4 预检接口", "ready" in api("/api/v1/precheck", "POST", {"prompt": "x"}))
    check("L5 任务列表", len(api("/api/v1/tasks")["tasks"]) >= 1)

    # ---------- M. 模式切换 + 运行时工作流路由（需求 #1：保证运行时执行对应工作流） ----------
    click(cdp, '[data-nav="studio"]')
    cdp.pause(400)
    cdp.js("(()=>{UI.mode='t2i'; UI.refs=[]; UI.prompt=''; refreshComposer();})()")
    cdp.pause(300)
    check("M1 模式切换按钮（文生图/图生图）存在", cdp.js("document.querySelectorAll('[data-mode]').length===2"))
    check("M2 默认文生图：隐藏参考图区", cdp.js(
        "document.querySelector('[data-mode=\"t2i\"]').classList.contains('on') && !document.querySelector('#refsRow')"))
    grades_t2i = cdp.js("JSON.stringify([...document.querySelectorAll('[data-grade]')].map(b=>b.dataset.grade))")
    click(cdp, '[data-mode="i2i"]')
    cdp.pause(400)
    check("M3 切到图生图：参考图上传区出现", cdp.js(
        "document.querySelector('[data-mode=\"i2i\"]').classList.contains('on') && !!document.querySelector('#addRef') && !!document.querySelector('#refsRow')"))
    grades_i2i = cdp.js("JSON.stringify([...document.querySelectorAll('[data-grade]')].map(b=>b.dataset.grade))")
    # 档位菜单必须来自「当前模式对应的工作流」：直接拿服务端配置逐项对照
    # （两个工作流档位名可能刚好一样，所以不能只比较两边是否不同）
    _st = api("/api/state")
    _cap = next((c for c in _st["capabilities"] if c["id"] == _st["ui"]["capId"]), {})
    _wf = {w["name"]: w for w in _cap.get("workflows", [])}
    _t2i = [g["name"] for g in _wf.get("文生图", {}).get("grades", [])]
    _i2i = [g["name"] for g in _wf.get("参考生图", {}).get("grades", [])]
    check("M4 切模式时档位菜单跟着切到对应工作流的配置",
          json.loads(grades_t2i) == _t2i and json.loads(grades_i2i) == _i2i,
          f"界面 t2i={grades_t2i} i2i={grades_i2i} / 配置 t2i={_t2i} i2i={_i2i}")
    # 图生图但没图：发送应被拦下
    set_editor(cdp, "图生图但还没传参考图")
    cdp.pause(300)
    jobs_before = len(api("/api/state")["jobs"])
    click(cdp, "#sendBtn")
    cdp.pause(500)
    check("M5 图生图无参考图时被拦下（不新建任务）",
          len(api("/api/state")["jobs"]) == jobs_before)
    # 上传 2 张参考图：用 D 段真实出图作参考，保证 LoadImage 读到合法图片，参考生图工作流才真正跑得通
    ref_src = f"/api/media?run={run1['id']}&i=0"
    cdp.js(f"""(async () => {{ const blob=await (await fetch({json.dumps(ref_src)})).blob();
        for(let i=0;i<2;i++){{ await uploadFiles([new File([blob],'m_ref'+i+'.png',{{type:'image/png'}})]); }} return true; }})()""")
    cdp.pause(1200)
    check("M6 图生图上传 2 张参考图", cdp.js("document.querySelectorAll('.refs .ref-thumb').length===2"))
    check("M6b 参考图缩略图 object-fit:contain（不被裁切）", cdp.js(
        "[...document.querySelectorAll('.ref-thumb img')].every(i=>getComputedStyle(i).objectFit==='contain')"))
    set_editor(cdp, "参考这张图改一版")
    cdp.pause(300)
    wait_idle(cdp)
    click(cdp, "#sendBtn")
    cdp.pause(700)
    m_ref = None
    st = api("/api/state")
    if len(st["jobs"]) > jobs_before:
        m_ref = st["jobs"][-1]
    check("M7 图生图提交后服务端 wf=参考生图（运行时路由正确）",
          bool(m_ref and m_ref["wf"] == "参考生图"), str(m_ref["wf"] if m_ref else "无新任务"))
    # 等它真正出图，证明这个工作流确实在 ComfyUI 跑起来了
    if m_ref:
        did = m_ref["id"]; deadline = time.time() + 150
        while time.time() < deadline:
            j = next((x for x in api("/api/state")["jobs"] if x["id"] == did), None)
            if j and j["runs"][0]["state"] in ("done", "failed", "canceled"):
                break
            time.sleep(1)
        fin = next((x for x in api("/api/state")["jobs"] if x["id"] == did), None)
        check("M8 参考生图工作流真实跑通（出图 done）", bool(fin and fin["runs"][0]["state"] == "done"),
              str(fin["runs"][0]["state"] if fin else "无"))
    # 回到文生图再发一条，确认 wf=文生图
    click(cdp, '[data-mode="t2i"]')
    cdp.pause(400)
    check("M9 切回文生图：参考图清空、上传区隐藏", cdp.js(
        "!document.querySelector('#refsRow') && document.querySelectorAll('.refs .ref-thumb').length===0"))
    set_editor(cdp, "纯文字再生成一张")
    cdp.pause(300)
    jobs_before2 = len(api("/api/state")["jobs"])
    wait_idle(cdp)
    click(cdp, "#sendBtn")
    cdp.pause(700)
    m_t2i = None
    st2 = api("/api/state")
    if len(st2["jobs"]) > jobs_before2:
        m_t2i = st2["jobs"][-1]
    check("M10 文生图提交后服务端 wf=文生图（运行时路由正确）",
          bool(m_t2i and m_t2i["wf"] == "文生图"), str(m_t2i["wf"] if m_t2i else "无新任务"))

    # ---------- N. libtv 风格 @ 引用（需求 #6） ----------
    click(cdp, '[data-mode="i2i"]')
    cdp.pause(400)
    cdp.js(f"""(async () => {{ const blob=await (await fetch({json.dumps(ref_src)})).blob();
        for(let i=0;i<2;i++){{ await uploadFiles([new File([blob],'n_ref'+i+'.png',{{type:'image/png'}})]); }} return true; }})()""")
    cdp.pause(1000)
    check("N1 进入图生图并上传 2 张参考图", cdp.js("document.querySelectorAll('.refs .ref-thumb').length===2"))
    check("N2 每个参考缩略图右下角有 @ 引用按钮", cdp.js("document.querySelectorAll('[data-refat]').length===2"))
    set_editor(cdp, "请参考")
    cdp.pause(250)
    cdp.js("document.querySelectorAll('[data-refat]')[0].click()")   # 点第一张的 @
    cdp.pause(300)
    check("N3 点 @ 后编辑器插入行内引用 chip", cdp.js("document.querySelectorAll('#promptInput .refchip').length===1"))
    check("N4 序列化提示词含 <image1> 令牌（模型可识别）", cdp.js("UI.prompt.includes('<image1>')"), cdp.js("UI.prompt"))
    cdp.js("document.querySelectorAll('[data-refat]')[1].click()")   # 点第二张的 @
    cdp.pause(300)
    check("N5 再点 @ 插入第二个引用，共 2 个 chip", cdp.js("document.querySelectorAll('#promptInput .refchip').length===2"))
    check("N6 两个令牌 <image1><image2> 都在", cdp.js("UI.prompt.includes('<image1>') && UI.prompt.includes('<image2>')"), cdp.js("UI.prompt"))
    cdp.js("document.querySelector('#promptInput .refchip [data-rmchip]').click()")   # 删第一个 chip
    cdp.pause(300)
    check("N7 移除行内 chip 后仅剩一个引用、令牌归一为 <image1>", cdp.js(
        "document.querySelectorAll('#promptInput .refchip').length===1 && UI.prompt.includes('<image1>') && !UI.prompt.includes('<image2>')"),
        cdp.js("UI.prompt"))
    # 真正发送，确认提交到服务端的 prompt 带令牌（Qwen-Image 2.1 识别 <image1> 形式）
    cdp.js("""(()=>{const t=document.querySelector('#promptInput');
        t.insertAdjacentText('beforeend','，以这张图为参考'); t.dispatchEvent(new Event('input',{bubbles:true}));})()""")
    cdp.pause(200)
    jobs_n = len(api("/api/state")["jobs"])
    wait_idle(cdp)
    click(cdp, "#sendBtn")
    cdp.pause(700)
    n_job = None
    stn = api("/api/state")
    if len(stn["jobs"]) > jobs_n:
        n_job = stn["jobs"][-1]
    check("N8 带 @ 引用的提示词提交后服务端 prompt 含 <image1> 令牌",
          bool(n_job and '<image1>' in (n_job.get("prompt") or "")), str((n_job or {}).get("prompt")))

    # ---------- O. 本会话任务批量删除（需求 #5） ----------
    check("O1 批量删除控件存在（全选 + 批量删除）", cdp.js("!!document.querySelector('#tall') && !!document.querySelector('#tbatch')"))
    sel_id = cdp.js("(()=>{const e=document.querySelector('[data-tsel]'); if(!e) return ''; e.click(); return e.dataset.tsel;})()")
    cdp.pause(250)
    check("O2 勾选单个任务后批量按钮可用", cdp.js("UI.selTasks.size===1 && !document.querySelector('#tbatch').disabled"), str(sel_id))
    click(cdp, "#tbatch")
    cdp.pause(300)
    check("O3 弹出确认框", cdp.js("!!document.querySelector('#mask') && document.body.textContent.includes('批量删除')"))
    click(cdp, "#confirmBatchDel")
    cdp.pause(600)
    gone = next((j for j in api("/api/state")["jobs"] if j["id"] == sel_id), {})
    check("O4 批量删除后该任务被标记移出会话（sessionRemoved，文件保留）", bool(gone.get("sessionRemoved")), f"id={sel_id}")
    check("O5 删除后选择集合清空、确认框关闭", cdp.js("UI.selTasks.size===0 && !document.querySelector('#mask')"))

    # ---------- P. 图片完整显示（object-fit:contain）与删除按钮布局（需求 #2/#3/#4） ----------
    check("P1 创作台成品图 object-fit:contain（不被裁切）", cdp.js(
        "document.querySelectorAll('.tile img').length>=1 && [...document.querySelectorAll('.tile img')].every(i=>getComputedStyle(i).objectFit==='contain')"))
    check("P2 卡片头部为删除按钮预留右侧空间（不挡时间）", cdp.js(
        "[...document.querySelectorAll('.job .head')].every(h=>parseFloat(getComputedStyle(h).paddingRight)>=40)"))
    click(cdp, '[data-nav="gallery"]')
    cdp.pause(450)
    check("P3 历史网格图片 object-fit:contain（不被裁切，含历史页）", cdp.js(
        "[...document.querySelectorAll('.gallery .card img')].every(i=>getComputedStyle(i).objectFit==='contain')"))
    check("P4 历史删除/多选为悬浮圆角按钮（美化后）", cdp.js(
        "!!document.querySelector('.gdelone') && !!document.querySelector('.gcheck')"))

    check("Z1 全程无未捕获 JS 异常", not any(
        e.get("method") == "Runtime.exceptionThrown" for e in cdp.events),
        f"{len(cdp.events)} 条 CDP 事件")


def main() -> int:
    """启动浏览器、跑全部检查、恢复配置、输出汇总。"""
    global CONFIG_BACKUP
    if HTTP.get("/api/state").status_code != 200:
        print("服务没有在 127.0.0.1:8190 运行，请先双击启动服务.bat")
        return 1
    CONFIG_BACKUP = CONFIG.read_text(encoding="utf-8")
    proc, cdp = start_browser()
    try:
        # 服务端会记住上次所在页面与上次用的能力（状态唯一在服务端），测试前都复位：
        # 这个用例针对的是图片生成，跑到别的能力上会找不到 #promptInput。
        # 另外必须连地址栏一起复位：页面会把当前路由写进 location.hash，
        # 而 hash 是深链接、优先级高于服务端状态——只 reload 的话会被旧 hash 拽回管理台。
        api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps",
                                          "capId": "image.generate", "galleryFilter": "全部"}})
        cdp.call("Page.navigate", {"url": BASE + "/#studio"})
        cdp.wait("!!document.querySelector('#root .topbar')", 25)
        if not cdp.wait("!!document.querySelector('.app .studio')", 15):
            print("WARN: 页面没有回到创作台，后续创作台用例可能失败", flush=True)
        run_checks(cdp)
    finally:
        try:
            api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps"}})
        except Exception:  # noqa: BLE001
            pass
        # 无论测试成败都把配置恢复成原样，不污染用户配置
        if CONFIG_BACKUP and CONFIG.read_text(encoding="utf-8") != CONFIG_BACKUP:
            CONFIG.write_text(CONFIG_BACKUP, encoding="utf-8")
            try:
                api("/api/config/reload", "POST", {})
            except Exception:  # noqa: BLE001
                pass
        cdp.close()
        proc.terminate()
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n===== UI E2E：{passed}/{len(RESULTS)} 项通过 =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
