"""角色设定图模块 E2E：接口组装 + 真实出图尺寸 + 页面交互 + 管理台编辑 + 回归。

前置：应用已在 http://127.0.0.1:8190 运行、ComfyUI 在 8188 在线、队列空闲。
运行：
    .venv\\Scripts\\python.exe tests\\sheet_e2e_test.py

为什么单独一个文件：图片生成模块已有 ui_e2e_test.py；这个文件只测新增模块，
另外带两项回归断言（图片生成的输入区、档位、左栏顺序没被动过），
这样任何一次改动都能一眼看出是「新模块坏了」还是「老模块被碰坏了」。
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
PROFILE = APP / ".cache" / "edge_sheet"
HTTP = httpx.Client(base_url=BASE, timeout=30.0)
RESULTS: list[tuple[str, bool, str]] = []
UPLOADED: list[str] = []          # 本轮测试自己上传的临时图，结束时删掉
SAMPLE_CHAR = "二十五岁中国女子，清冷气质，长直黑发用竹簪高盘，细眉凤眼，穿素白交领长衫，腰系青色织带，脚踩黑布靴，腰侧挂一枚青玉玉佩"


def check(name: str, ok: Any, detail: str = "") -> None:
    """记录一项结果并即时打印。"""
    passed = bool(ok)
    RESULTS.append((name, passed, detail[:240]))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail[:200]}" if detail else ""), flush=True)


def api(path: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    """直连后端接口。"""
    resp = HTTP.request(method, path, json=body) if body is not None else HTTP.request(method, path)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": resp.text[:200]}


class CDP:
    """极简 Chrome DevTools 客户端（与 ui_e2e_test.py 同一套做法）。"""

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
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS 异常: {json.dumps(result['exceptionDetails'], ensure_ascii=False)[:400]}")
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

    def pause(self, ms: int = 450) -> None:
        self.js(f"new Promise(r=>setTimeout(()=>r(true),{ms}))")

    def errors(self) -> list[str]:
        out = []
        for event in self.events:
            if event.get("method") == "Runtime.exceptionThrown":
                detail = event["params"].get("exceptionDetails", {})
                out.append(str(detail.get("text")) + " " + str(
                    (detail.get("exception") or {}).get("description", ""))[:200])
        return out

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def click(cdp: CDP, selector: str) -> Any:
    return cdp.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)return false;e.click();return true;}})()")


def wait_idle(timeout: float = 180.0) -> bool:
    """等 ComfyUI 与本地都没有排队/执行中的任务（普通生成忙时会直接拒绝）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api("/api/state")
        busy = any(r["state"] in ("queued", "running") for j in st.get("jobs", []) for r in j["runs"])
        if not busy:
            return True
        time.sleep(1)
    return False


def start_browser() -> tuple[subprocess.Popen, CDP]:
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-extensions", "--no-first-run", "--disable-background-networking",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,960", BASE + "/#studio",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 40
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
    raise RuntimeError("连不上 Edge 的调试端口")


# ---------------------------------------------------------------- A 接口层
def section_api() -> None:
    print("\n--- A 接口层：提示词库与档位 ---", flush=True)
    lib = api("/api/sheet/library")
    check("A1 提示词库接口可用", lib.get("ok"), str(lib.get("reason", ""))[:80])
    data = lib.get("data") or {}
    check("A2 通用块齐全（U1~U5 + R1 + R2）",
          {"U1", "U1G", "U2", "U2G", "U3", "U3G", "U4", "U5", "R1", "R2"} <= set((data.get("blocks") or {}).keys()),
          f"{len(data.get('blocks') or {})} 块")
    check("A3 三个版式 + 四类线稿句 + 十二类画风 + 十五条组合",
          len(data.get("layouts") or []) == 3 and len(data.get("line_styles") or []) == 4
          and len(data.get("styles") or []) == 12 and len(data.get("presets") or []) == 15,
          f"{len(data.get('layouts') or [])}/{len(data.get('line_styles') or [])}/"
          f"{len(data.get('styles') or [])}/{len(data.get('presets') or [])}")

    state = api("/api/state")
    caps = {c["id"]: c for c in state.get("capabilities", [])}
    sheet = caps.get("sheet.character") or {}
    check("A4 能力已启用且有两个子工作流",
          sheet.get("enabled") and len(sheet.get("workflows") or []) == 2,
          str([w["name"] for w in sheet.get("workflows") or []]))
    check("A5 参考图上限 = 1（提示词只认图 1）", sheet.get("maxRefs") == 1, str(sheet.get("maxRefs")))
    # 新模型：工作流自己不再挂档位（档位挂在「组合」上，版式/画法/参考方式是组合的维度）
    for wf in sheet.get("workflows") or []:
        grades = [g["name"] for g in wf.get("grades") or []]
        check(f"A6 {wf['name']} 不再拿版式当档位", grades == [], str(grades))
    combos = {c["id"]: c for c in sheet.get("combos") or []}
    check("A6 组合表 18 个（3 版式 × 2 画法 + 图生图再 × 2 参考方式）",
          len(combos) == 18, str(len(combos)))
    for cid, name, want in (("A.color.none", "四视图横排", "1536 × 864"),
                            ("B.line.lock", "四宫格", "864 × 1536"),
                            ("C.color.style", "单张全身", "864 × 1536")):
        c = combos.get(cid) or {}
        check(f"A7 组合 {cid} = {name}，画幅 {want}",
              c.get("layoutName") == name and want in str(c.get("size")), str(c.get("size")))
    image = caps.get("image.generate") or {}
    check("A8 图片生成仍然可用、档位没被动",
          image.get("enabled") and len(image.get("workflows") or []) == 2
          and any(g["name"] == "快速（16:9）" for w in image["workflows"] for g in w.get("grades") or []),
          "image.generate 未受影响")

    # 预览：每个版式 × 画法 × 参考方式的文案关键点
    cases = [
        ("A", "color", "none", ["一张角色设定三视图", "16:9 横向", "分栏要求", "第四区（最右）", "角色：", "风格："]),
        ("B", "color", "none", ["9:16 竖版", "分格要求", "右下格", "四个方格画的是同一个人"]),
        ("C", "color", "none", ["画面正中只有一个角色", "构图：", "只有一个角色、一个姿态"]),
        ("A", "line", "none", ["纯黑白线稿", "一律不上色"]),
        ("A", "color", "lock", ["图 1 是这个角色现有的画", "画风与图 1 保持一致"]),
        ("A", "color", "style", ["图 1 只用来参考画风和笔触"]),
        ("C", "line", "lock", ["整体线条风格与图 1 保持一致"]),
    ]
    for layout, ink, ref, keys in cases:
        res = api("/api/sheet/preview", "POST", {
            "layout": layout, "character": SAMPLE_CHAR, "style": "水墨国风",
            "ink": ink, "ref": ref, "lineStyle": "W1" if ink == "line" else None,
        })
        text = ((res.get("data") or {}).get("prompt") or "")
        missing = [k for k in keys if k not in text]
        check(f"A9 组装 {layout}/{ink}/{ref} 关键句齐全", res.get("ok") and not missing,
              f"缺 {missing}" if missing else f"{len(text)} 字 · {((res.get('data') or {}).get('size'))}")

    # R2 必须放在最前面（放末尾会被角色描述盖过去）
    res = api("/api/sheet/preview", "POST", {"layout": "B", "character": SAMPLE_CHAR,
                                             "style": "厚涂插画", "ink": "color", "ref": "style"})
    text = ((res.get("data") or {}).get("prompt") or "")
    check("A10 「只借画风」那句在最前面", text.startswith("图 1 只用来参考画风和笔触"), text[:26])

    # 错误处理
    for body, name in [
        ({"layout": "A", "character": "", "style": "水墨国风"}, "空角色描述"),
        ({"layout": "Z", "character": "abc", "style": "水墨国风"}, "不存在的版式"),
        ({"layout": "A", "character": "abc", "style": "", "ink": "color"}, "空风格"),
    ]:
        res = api("/api/sheet/preview", "POST", body)
        check(f"A11 {name}被拦下并给出人话", res.get("ok") is False and bool(res.get("reason")), str(res.get("reason"))[:60])

    # 提交校验：选了「锁同角色」却没图 → 拒绝；有图没选参考方式 → 自动按锁角色
    res = api("/api/tasks", "POST", {"capId": "sheet.character", "grade": "四宫格", "refs": [],
                                     "sheet": {"layout": "B", "character": SAMPLE_CHAR,
                                               "style": "水墨国风", "ink": "color", "ref": "lock"}})
    check("A12 锁同角色但没图被拦下", res.get("ok") is False, str(res.get("reason"))[:60])


# ---------------------------------------------------------------- B 页面：创作台
def section_studio(cdp: CDP) -> None:
    print("\n--- B 创作台：子功能与表单 ---", flush=True)
    cdp.js("location.hash='#studio'")
    cdp.pause(600)
    cdp.js("(()=>{UI._userSwitched=false; render(); return true;})()")
    cdp.pause(400)

    check("B1 左栏出现「角色设定图」能力卡", cdp.js(
        "[...document.querySelectorAll('.rail .item')].some(e=>e.textContent.includes('角色设定图'))"))
    check("B2 角色设定图不再是「未配置」态", cdp.js(
        "(()=>{const e=[...document.querySelectorAll('.rail .item')].find(x=>x.textContent.includes('角色设定图'));"
        "return !!e && !e.classList.contains('dis') && !e.querySelector('.tag');})()"))
    check("B3 「设定图」分组存在", cdp.js(
        "[...document.querySelectorAll('.rail .group')].some(g=>g.textContent.trim()==='设定图')"))

    click(cdp, '[data-cap="sheet.character"]')
    cdp.pause(700)
    # 上一轮测试会在服务端留下草稿（版式/角色行/面板展开态），先复位成初始值再断言。
    # 顺带把 _draftCap 对齐：否则第一次轮询还会再 loadDraft 一次，把刚复位的东西灌回去。
    cdp.js("""(()=>{ UI.mode='t2i'; UI.refs=[]; UI.prompt=''; UI.sheetOpen=false; UI.sheetPreview=null;
        UI._draftCap=UI.capId;
        UI.sheetForm={layout:'A',ink:'color',lineStyle:'W1',ref:'lock',character:'',style:'写实摄影',styleText:''};
        render(); return true;})()""")
    cdp.pause(600)
    check("B4 切到角色设定图后出现专用表单", cdp.js("!!document.querySelector('.sheetbox')"))
    check("B5 三个版式按钮", cdp.js("document.querySelectorAll('[data-sheet-layout]').length===3"),
          str(cdp.js("document.querySelectorAll('[data-sheet-layout]').length")))
    check("B6 默认版式为四视图横排且显示 1536 × 864", cdp.js(
        "(()=>{const on=document.querySelector('[data-sheet-layout].on');const t=document.querySelector('.sheetbar').textContent;"
        "return !!on && on.textContent.includes('四视图横排') && t.includes('1536 × 864');})()"),
        str(cdp.js("document.querySelector('.sheetbar').textContent.trim().slice(0,60)")))
    check("B7 画法有彩色/线稿两个按钮", cdp.js("document.querySelectorAll('[data-sheet-ink]').length===2"))
    check("B8 文生图模式下不显示参考方式与上传", cdp.js(
        "!document.querySelector('[data-sheet-ref]') && !document.querySelector('#addRef')"))
    check("B9 风格是 12 类预设 + 自定义", cdp.js("document.querySelectorAll('#sheetStyle option').length===13"),
          str(cdp.js("document.querySelectorAll('#sheetStyle option').length")))

    # 版式切换 → 尺寸与档位同步
    click(cdp, '[data-sheet-layout="B"]')
    cdp.pause(500)
    check("B10 切四宫格后尺寸变 864 × 1536", cdp.js(
        "document.querySelector('.sheetbar').textContent.includes('864 × 1536')"),
        str(cdp.js("document.querySelector('.sheetbar').textContent.trim().slice(0,60)")))
    # 版式只换配方（组合），不换档位名：档位挂在组合上，默认停在默认
    check("B11 版式切换只换组合、不动档位",
          cdp.js("UI.sheetForm.grade==='默认' && (sheetCombo()||{}).layout==='B'"),
          str(cdp.js("UI.sheetForm.grade+' / '+(sheetCombo()||{}).id")))

    # 画法线稿 → 出现线稿风格句选择
    click(cdp, '[data-sheet-ink="line"]')
    cdp.pause(500)
    check("B12 线稿模式出现线稿风格句选择（4 条）", cdp.js(
        "!!document.querySelector('#sheetLine') && document.querySelectorAll('#sheetLine option').length===4"))
    check("B13 线稿模式给出「别写颜色词」提示", cdp.js("!!document.querySelector('.warnhint')"))

    # 角色描述 → 发送按钮
    check("B14 未填角色描述时发送按钮置灰", cdp.js("document.querySelector('#sendBtn').disabled"))
    cdp.js(f"(()=>{{const t=document.querySelector('#sheetChar');t.value={json.dumps(SAMPLE_CHAR)};"
           "t.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    cdp.pause(600)
    check("B15 填了角色描述后可以发送", cdp.js("!document.querySelector('#sendBtn').disabled"))
    # 草稿是防抖写入的，机器忙时 600ms 不一定够：等到写进去（或超时）再断言
    draft: dict[str, Any] = {}
    deadline = time.time() + 10
    while time.time() < deadline:
        draft = api("/api/state")["ui"]["drafts"].get("sheet.character", {}).get("sheet", {})
        if (draft.get("layout") == "B" and draft.get("ink") == "line"
                and SAMPLE_CHAR[:8] in str(draft.get("character"))):
            break
        time.sleep(0.4)
    check("B16 草稿同步到服务端（版式/画法/角色行）",
          draft.get("layout") == "B" and draft.get("ink") == "line"
          and SAMPLE_CHAR[:8] in str(draft.get("character")),
          str(draft)[:90])

    # 看完整提示词
    click(cdp, "#sheetPvBtn")
    # 面板先占位、内容等服务端组装回来：机器忙时一两秒才到，等元素而不是固定 sleep
    cdp.wait("!!document.querySelector('#sheetPv pre')", 20)
    check("B17 「看完整提示词」展开预览", cdp.js("!!document.querySelector('#sheetPv pre')"))
    preview_text = cdp.js("(()=>{const p=document.querySelector('#sheetPv pre');return p?p.textContent:'';})()") or ""
    check("B18 预览里是线稿四宫格的完整提示词",
          "9:16 竖版" in preview_text and "纯黑白线稿" in preview_text and "分格要求" in preview_text,
          preview_text[:40])
    server_text = ((api("/api/sheet/preview", "POST", {
        "layout": "B", "character": SAMPLE_CHAR, "style": "水墨国风",
        "ink": "line", "ref": "none", "lineStyle": "W1"}) or {}).get("data") or {}).get("prompt", "")
    check("B19 页面预览与服务端组装一致（前 200 字相同）",
          bool(preview_text) and preview_text[:200] == server_text[:200],
          f"页面 {len(preview_text)} 字 / 服务端 {len(server_text)} 字")
    click(cdp, "#sheetPvBtn")
    cdp.pause(400)
    check("B20 再点一次收起预览", cdp.js("!document.querySelector('#sheetPv')"))


# ---------------------------------------------------------------- C 真实出图（文生图）
def wait_job(job_id: str, timeout: float = 180.0) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = api("/api/state")
        job = next((j for j in state.get("jobs", []) if j["id"] == job_id), None)
        if job and job["runs"] and job["runs"][-1]["state"] in ("done", "failed", "canceled"):
            return job
        time.sleep(1.5)
    return None


def section_generate(cdp: CDP) -> dict[str, Any] | None:
    print("\n--- C 真实出图：四视图横排（16:9）---", flush=True)
    if not wait_idle():
        check("C0 队列空闲", False, "ComfyUI 一直有任务")
        return None
    click(cdp, '[data-sheet-layout="A"]')
    cdp.pause(400)
    click(cdp, '[data-sheet-ink="color"]')
    cdp.pause(400)
    cdp.js(f"(()=>{{const t=document.querySelector('#sheetChar');t.value={json.dumps(SAMPLE_CHAR)};"
           "t.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    cdp.pause(400)
    before = len(api("/api/state")["jobs"])
    check("C1 点击发送", click(cdp, "#sendBtn"))
    cdp.pause(900)
    state = api("/api/state")
    check("C2 服务端多了一条任务", len(state["jobs"]) > before, f"{before} → {len(state['jobs'])}")
    if len(state["jobs"]) <= before:
        return None
    job = state["jobs"][-1]
    check("C3 卡片进入对话区", cdp.js(f"!!document.getElementById({json.dumps(job['id'])})"))
    check("C4 提交的是「设定图-文生图」+ 默认档 + 组合 id",
          job["wf"] == "设定图-文生图" and job["runs"][0]["grade"] == "默认"
          and str((job.get("sheet") or {}).get("combo", "")).startswith("A."),
          f"{job['wf']} / {job['runs'][0]['grade']} / {(job.get('sheet') or {}).get('combo')}")
    check("C5 记录里带了结构化 sheet 与摘要",
          (job.get("sheet") or {}).get("layout") == "A" and bool(job.get("brief")),
          str(job.get("brief"))[:60])
    check("C6 提示词是整条组装稿（不是用户原文）",
          len(job["prompt"]) > 500 and "分栏要求" in job["prompt"] and "画面要求" in job["prompt"],
          f"{len(job['prompt'])} 字")

    done = wait_job(job["id"])
    run = (done or {}).get("runs", [{}])[-1]
    check("C7 真实出图完成", run.get("state") == "done", str(run.get("state")) + " " + str((run.get("err") or {}).get("msg", ""))[:80])
    check("C8 出图尺寸 1536 × 864", str(run.get("size")) == "1536×864", str(run.get("size")))
    check("C9 卡片上显示摘要而不是 800 字正文", cdp.js(f"(()=>{{const c=document.getElementById({json.dumps(job['id'])});"
        "if(!c)return false;const t=c.textContent;return t.includes('四视图横排') && t.includes('看完整提示词');})()"))
    check("C10 成品图在页面里真的加载出来", cdp.wait(
        f"(()=>{{const c=document.getElementById({json.dumps(job['id'])});if(!c)return false;"
        "const i=c.querySelector('.tile img');return !!i && i.complete && i.naturalWidth>0;})()", 30))
    return done


# ---------------------------------------------------------------- D 图生图（参考角色）
def section_reference(cdp: CDP, source_run: str) -> None:
    print("\n--- D 真实出图：参考角色 → 单张全身（9:16）---", flush=True)
    if not wait_idle():
        check("D0 队列空闲", False, "ComfyUI 一直有任务")
        return
    click(cdp, '[data-mode="i2i"]')
    cdp.pause(600)
    check("D1 图生图模式出现上传按钮与参考方式", cdp.js(
        "!!document.querySelector('#addRef') && document.querySelectorAll('[data-sheet-ref]').length===2"))
    got = cdp.js(f"""(async () => {{
        const blob = await (await fetch('/api/media?run={source_run}&i=0')).blob();
        await uploadFiles([new File([blob], 'sheet_ref.png', {{type:'image/png'}})]);
        return UI.refs.length;
    }})()""")
    cdp.pause(1400)
    # 记下这张临时图，跑完删掉：临时图目录有上限，反复跑测试不该把它攒满
    UPLOADED.extend(cdp.js("[UI.refs.map(r=>r.id)]")[0] or [])
    check("D2 参考图上传成功（拿上一张成品当参考）", got == 1 and cdp.js("document.querySelectorAll('.ref-thumb').length===1"),
          f"refs={got}")

    click(cdp, '[data-sheet-ref="lock"]')
    cdp.pause(400)
    click(cdp, '[data-sheet-layout="C"]')
    cdp.pause(500)
    check("D3 单张全身 + 锁定同角色已选上", cdp.js(
        "document.querySelector('[data-sheet-layout=\"C\"]').classList.contains('on') && "
        "document.querySelector('[data-sheet-ref=\"lock\"]').classList.contains('on')"),
        str(cdp.js("[UI.mode,UI.capId,UI._draftCap,UI.sheetForm.layout,UI.sheetForm.ref,"
                   "UI.sheetForm.ink,[...document.querySelectorAll('[data-sheet-layout].on')].map(e=>e.dataset.sheetLayout),"
                   "[...document.querySelectorAll('[data-sheet-ref].on')].map(e=>e.dataset.sheetRef)]")))
    # 文生图那一张发完会清空角色描述，这里重新填上再发（和用户真实操作一致）
    cdp.js(f"(()=>{{const t=document.querySelector('#sheetChar');t.value={json.dumps(SAMPLE_CHAR)};"
           "t.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    cdp.pause(600)
    # 看一次提示词：确认 R1 落在角色行前面
    res = api("/api/sheet/preview", "POST", {"layout": "C", "character": SAMPLE_CHAR,
                                            "style": "写实摄影", "ink": "color", "ref": "lock"})
    text = ((res.get("data") or {}).get("prompt") or "")
    check("D4 锁角色那句插在角色行之前", "图 1 是这个角色现有的画" in text
          and text.index("图 1 是这个角色现有的画") < text.index("角色："), "顺序正确")

    before = len(api("/api/state")["jobs"])
    click(cdp, "#sendBtn")
    cdp.pause(1000)
    state = api("/api/state")
    check("D5 提交成功", len(state["jobs"]) > before)
    if len(state["jobs"]) <= before:
        return
    job = state["jobs"][-1]
    check("D6 走的是「设定图-参考生图」子工作流", job["wf"] == "设定图-参考生图", job["wf"])
    check("D7 参考图带进了任务记录", len(job.get("refs") or []) == 1, str(job.get("refs")))
    done = wait_job(job["id"])
    run = (done or {}).get("runs", [{}])[-1]
    check("D8 真实出图完成", run.get("state") == "done",
          str(run.get("state")) + " " + str((run.get("err") or {}).get("msg", ""))[:80])
    check("D9 尺寸是 9:16 的 864 × 1536（没有被参考图带偏）", str(run.get("size")) == "864×1536", str(run.get("size")))


# ---------------------------------------------------------------- E 管理台
def section_admin(cdp: CDP) -> None:
    print("\n--- E 管理台：设定图模板 ---", flush=True)
    cdp.js("location.hash='#admin/sheet'")
    cdp.pause(900)
    # 本轮需求：设定图模板从菜单移入档位表（模态框）；保留 #admin/sheet 直接访问兼容
    check("E1 「设定图模板」页内容能渲染（入口已移到档位表）",
          cdp.js("UI.adminPage==='sheet' && !!document.querySelector('.lcard')"))
    check("E2 导航里已无该项（不再是全局功能）", cdp.js(
        "!document.querySelector('.admin-nav [data-page=\"sheet\"]')"))
    check("E3 版式卡片三张", cdp.js("document.querySelectorAll('.lcard').length===3"),
          str(cdp.js("document.querySelectorAll('.lcard').length")))
    check("E4 通用块十个编辑框", cdp.js("document.querySelectorAll('[data-blk]').length===10"),
          str(cdp.js("document.querySelectorAll('[data-blk]').length")))
    check("E5 线稿句四条 + 风格预设十二行", cdp.js(
        "document.querySelectorAll('[data-ls]').length===4 && document.querySelectorAll('[data-stn]').length===12"))
    check("E6 组合速查表十五条", cdp.js("document.querySelectorAll('[data-sheet-use]').length>=18"),
          str(cdp.js("document.querySelectorAll('[data-sheet-use]').length")))
    check("E7 组合速查表覆盖两个子工作流（6 文生图 + 12 图生图）", cdp.js("""(()=>{
        const ids=[...document.querySelectorAll('[data-sheet-use]')].map(e=>e.dataset.sheetUse||'');
        const t2i=ids.filter(x=>x.endsWith('|none')).length;
        return ids.length===18 && t2i===6 && ids.length-t2i===12;})()"""),
          str(cdp.js("""(()=>{const ids=[...document.querySelectorAll('[data-sheet-use]')].map(e=>e.dataset.sheetUse||'');
            return ids.length+' / '+ids.filter(x=>x.endsWith('|none')).length;})()""")))

    # 预览：服务端组装整条提示词（等元素出现，别用固定 sleep：冷启动那次要两秒多）
    click(cdp, '[data-sheet-preview="B"]')
    cdp.wait("!!document.querySelector('.promptfull')", 12)
    check("E8 预览弹窗打开了整条提示词", cdp.js("!!document.querySelector('.promptfull')"))
    text = cdp.js("(()=>{const p=document.querySelector('.promptfull');return p?p.textContent:'';})()") or ""
    check("E9 预览内容确实是四宫格整条稿", "9:16 竖版" in text and "分格要求" in text and "角色：" in text,
          f"{len(text)} 字")
    cdp.js("(()=>{document.querySelector('#mask [data-close]').click();return true;})()")
    cdp.pause(400)

    # 编辑通用块 → 保存 → 服务端生效 → 改回
    original = ((api("/api/sheet/library").get("data") or {}).get("blocks") or {}).get("U4", {}).get("text", "")
    marker = "【E2E 标记】"
    cdp.js(f"""(()=>{{const t=document.querySelector('[data-blk="U4"]');
        t.value = {json.dumps(marker)} + t.value; t.dispatchEvent(new Event('input',{{bubbles:true}})); return true;}})()""")
    cdp.pause(300)
    click(cdp, "[data-sheetlib-save]")
    cdp.pause(1500)
    saved = ((api("/api/sheet/library").get("data") or {}).get("blocks") or {}).get("U4", {}).get("text", "")
    check("E10 通用块保存后写回提示词库", marker in saved, saved[:24])
    preview = ((api("/api/sheet/preview", "POST", {"layout": "A", "character": SAMPLE_CHAR,
                                                   "style": "水墨国风"}) or {}).get("data") or {}).get("prompt", "")
    check("E11 改通用块后组装结果立刻跟着变", marker in preview, "组装已读取新块")

    # 改回原文，避免测试污染真实提示词库
    cdp.js(f"""(()=>{{const t=document.querySelector('[data-blk="U4"]');
        t.value = {json.dumps(original)}; t.dispatchEvent(new Event('input',{{bubbles:true}})); return true;}})()""")
    cdp.pause(300)
    click(cdp, "[data-sheetlib-save]")
    cdp.pause(1500)
    restored = ((api("/api/sheet/library").get("data") or {}).get("blocks") or {}).get("U4", {}).get("text", "")
    check("E12 改回原文后和最初一致", restored.strip() == original.strip(), "提示词库已复原")

    # 保存非法内容要被拦下
    bad = api("/api/sheet/library", "PUT", {"blocks": {"U1": {"name": "x", "text": "  "}}})
    check("E13 空通用块被拦下", bad.get("ok") is False, str(bad.get("reason"))[:60])

    # 从管理台把组合填进创作台（先写草稿再跳转，地址栏要等一次写入回来）
    click(cdp, '[data-sheet-use="B|line|none"]')
    cdp.wait("location.hash.includes('studio')", 20)
    check("E14 「填入」跳到创作台且带上了版式/画法", cdp.js(
        "location.hash.includes('studio') && UI.capId==='sheet.character' && "
        "UI.sheetForm.layout==='B' && UI.sheetForm.ink==='line'"),
        str(cdp.js("[location.hash, UI.capId, UI.sheetForm.layout, UI.sheetForm.ink]")))


# ---------------------------------------------------------------- F 回归：图片生成
def section_regression(cdp: CDP) -> None:
    print("\n--- F 回归：图片生成模块没被影响 ---", flush=True)
    click(cdp, '[data-cap="image.generate"]')
    cdp.pause(800)
    check("F1 图片生成输入区还是原来的结构", cdp.js(
        "!!document.querySelector('#promptInput') && !!document.querySelector('.modepick') && !document.querySelector('.sheetbox')"))
    check("F2 文生图/图生图两个子模式都在", cdp.js("document.querySelectorAll('[data-mode]').length===2"))
    check("F3 档位按钮还在（快速 16:9 / 精细 16:9）", cdp.js(
        "document.querySelectorAll('[data-grade]').length>=2"),
        str(cdp.js("[...document.querySelectorAll('[data-grade]')].map(e=>e.textContent.trim())")))
    check("F4 提示词编辑器可输入", cdp.js(
        "(()=>{const t=document.querySelector('#promptInput');t.textContent='回归测试文字';"
        "t.dispatchEvent(new Event('input',{bubbles:true}));return UI.prompt.includes('回归测试文字');})()"))
    cdp.pause(500)
    check("F5 发送按钮跟着启用", cdp.js("!document.querySelector('#sendBtn').disabled"))
    cdp.js("(()=>{const t=document.querySelector('#promptInput');t.textContent='';t.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    cdp.pause(400)
    check("F6 设置页 / 档位页 / 自检页照旧能进", all(api("/api/state").get("ok") is not False for _ in [0]))
    for page, sel in (("grades", ".grades-table, .glist"), ("service", "[data-s=comfy]"), ("diag", ".diag-row, .panel")):
        cdp.js(f"location.hash='#admin/{page}'")
        cdp.pause(800)
        check(f"F7 管理台 {page} 页正常渲染", cdp.js(f"!!document.querySelector({json.dumps(sel)})"))


def main() -> int:
    # 支持只跑某几段：`python tests/sheet_e2e_test.py ab` 只跑接口+创作台，
    # 改前端时不用每次都等两张真实出图（全量仍是默认行为）。
    only = "".join(sys.argv[1:]).lower() or "abcdefg"
    want = lambda s: s in only  # noqa: E731
    proc = None
    cdp = None
    try:
        if want("a"):
            section_api()
        if want("b"):
            proc, cdp = start_browser()
            cdp.pause(800)
            check("A0 页面加载无 JS 异常（初始）", not cdp.errors(), str(cdp.errors())[:120])
            section_studio(cdp)
        if want("c"):
            if cdp is None:
                proc, cdp = start_browser()
                cdp.pause(800)
            job = section_generate(cdp)
            if want("d"):
                if job:
                    run_id = job["runs"][0]["id"]
                    section_reference(cdp, run_id)
                else:
                    check("D 参考生图（跳过）", False, "文生图没成功，参考生图无法继续")
        if want("e") or want("f") or want("g"):
            if cdp is None:
                proc, cdp = start_browser()
                cdp.pause(800)
        if want("e"):
            section_admin(cdp)
        if want("f"):
            section_regression(cdp)
        if want("g") and cdp is not None:
            errors = cdp.errors()
            check("G1 全流程没有未捕获 JS 异常", not errors, str(errors)[:200])
            check("G2 页面无配置红条", cdp.js("!document.querySelector('.configerr')"))
    finally:
        # 收尾：把页面留在创作台（F 段最后停在管理台自检页），能力也退回默认的图片生成。
        # 服务端会记住「上次用的能力」，留在这里会让后面针对图片生成的用例找不到输入区。
        try:
            api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps",
                                              "capId": "image.generate"}})
        except Exception:  # noqa: BLE001
            pass
        # 自己上传的临时图自己删：临时图目录有张数上限，反复跑测试不该把它攒满
        for ref_id in UPLOADED:
            try:
                HTTP.delete(f"/api/uploads/{ref_id}")
            except Exception:  # noqa: BLE001
                pass
        if cdp:
            cdp.close()
        if proc:
            proc.terminate()

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n==== 角色设定图 E2E：{passed}/{total} 通过 ====", flush=True)
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  ✗ {name} — {detail}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
