"""角色设定图 · 全组合真实页面操作 E2E。

原则（严格遵守）：所有生成、切档、上传、入队、再来一张都通过真实浏览器页面的
DOM 点击/输入/文件选择完成；不直接 POST 后端接口、不调用任何业务函数去生成。
只是通过 CDP 读取页面自己的状态（window.S / window.UI）与 DOM 文本来做记录与核对。

用法：
    .venv\\Scripts\\python.exe tests\\sheet_full_e2e.py probe
    .venv\\Scripts\\python.exe tests\\sheet_full_e2e.py batch tests/_sheet_full_out/cases_round1.json
    .venv\\Scripts\\python.exe tests\\sheet_full_e2e.py one '<case json>'
结果：tests/_sheet_full_out/results.jsonl + run.log
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket

sys.stdout.reconfigure(encoding="utf-8")

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9224
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_sheet_full"
OUT = APP / "tests" / "_sheet_full_out"
RESULTS = OUT / "results.jsonl"
LOG = OUT / "run.log"
LIB = json.loads((APP / "prompts" / "character_sheet.json").read_text(encoding="utf-8"))
OUT.mkdir(parents=True, exist_ok=True)


def log(*parts: Any) -> None:
    text = " ".join(str(p) for p in parts)
    print(text, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def record(row: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def done_ids() -> set[str]:
    if not RESULTS.exists():
        return set()
    out = set()
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("ok"):
                out.add(row.get("id", ""))
        except Exception:
            pass
    return out


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
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS 异常: {str(result['exceptionDetails'])[:300]}")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 30.0, interval: float = 0.4) -> Any:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                v = self.js(expression)
                if v:
                    return v
            except Exception:
                pass
            time.sleep(interval)
        return None

    def pause(self, ms: int = 300) -> None:
        self.js(f"new Promise(r=>setTimeout(()=>r(true),{ms}))")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def click(cdp: CDP, selector: str) -> bool:
    return bool(cdp.js(f"(()=>{{const e=document.querySelector({q(selector)}); if(!e) return false; e.click(); return true;}})()"))


def click_value(cdp: CDP, attr: str, value: str) -> bool:
    """按 data-* 属性的值找元素并真实点击（避开中文/特殊字符的 CSS 转义）。"""
    return bool(cdp.js(f"""(()=>{{
        const e=[...document.querySelectorAll('[{attr}]')]
            .find(x=>x.getAttribute({q(attr)})==={q(value)});
        if(!e) return false; e.click(); return true;
    }})()"""))


def set_sel(cdp: CDP, selector: str, value: str) -> bool:
    """模拟在 <select> 上选一项（value + change 事件，和真人一致）。"""
    return bool(cdp.js(f"""(()=>{{
        const e=document.querySelector({q(selector)}); if(!e) return false;
        e.value={q(value)}; e.dispatchEvent(new Event('change',{{bubbles:true}})); return true;
    }})()"""))


def set_text(cdp: CDP, selector: str, value: str, event: str = "input") -> bool:
    return bool(cdp.js(f"""(()=>{{
        const e=document.querySelector({q(selector)}); if(!e) return false;
        e.value={q(value)}; e.dispatchEvent(new Event({q(event)},{{bubbles:true}})); return true;
    }})()"""))


def read_state(cdp: CDP, expr: str) -> Any:
    return cdp.js("(()=>{" + expr + "})()")


def start_browser() -> tuple[subprocess.Popen, CDP]:
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-extensions", "--no-first-run", "--disable-background-networking",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", BASE + "/",
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
                cdp.call("DOM.enable")
                cdp.call("Log.enable")
                return proc, cdp
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("连不上 Edge 调试端口")


def ensure_sheet(cdp: CDP) -> bool:
    ok = cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')", 25)
    if not ok:
        return False
    if not cdp.js("UI.capId==='sheet.character'"):
        click(cdp, '.rail [data-cap="sheet.character"]')
        cdp.pause(600)
    return bool(cdp.wait("UI.capId==='sheet.character' && !!document.querySelector('#sheetChar')", 20))


def clean_draft(cdp: CDP) -> None:
    """把草稿复位到干净状态（通过真实控件操作，不直接改内存变量）。"""
    cdp.js("(()=>{ if(document.querySelector('#sheetPv')) { const b=document.querySelector('#sheetPvBtn'); if(b && b.textContent.includes('收起')) b.click(); } return true;})()")
    cdp.pause(250)


def set_mode(cdp: CDP, mode: str) -> bool:
    sel = f'[data-mode="{mode}"]'
    if not click(cdp, sel):
        return False
    cdp.pause(450)
    return bool(cdp.js(f"UI.mode==={q(mode)}"))


def configure(cdp: CDP, case: dict[str, Any]) -> dict[str, Any]:
    """按用例逐项点击页面控件；返回每一步是否点到了控件。"""
    trace: dict[str, Any] = {}
    trace["mode"] = set_mode(cdp, case["mode"])
    layout = case["layout"]
    trace["layout"] = click(cdp, f'[data-sheet-layout="{layout}"]')
    cdp.pause(220)
    ink = case["ink"]
    trace["ink"] = click(cdp, f'[data-sheet-ink="{ink}"]')
    cdp.pause(220)
    if ink == "line":
        trace["lineStyle"] = set_sel(cdp, "#sheetLine", case["lineStyle"])
        cdp.pause(220)
    else:
        trace["lineStyle"] = "n/a"
    if case["mode"] == "i2i":
        ref_sel = '[data-sheet-ref="' + case["ref"] + '"]'
        trace["ref"] = click(cdp, ref_sel)
        cdp.pause(220)
    else:
        trace["ref"] = "n/a"
    # 风格：预设或自定义
    style = case.get("style", "")
    custom = case.get("styleText", "")
    if custom:
        trace["styleCustom"] = set_sel(cdp, "#sheetStyle", "")
        cdp.pause(200)
        trace["styleText"] = set_text(cdp, "#sheetStyleText", custom)
    else:
        trace["style"] = set_sel(cdp, "#sheetStyle", style)
    cdp.pause(220)
    # 档位
    grade_sel = '[data-sheet-grade="' + case["grade"] + '"]'
    trace["grade"] = click(cdp, grade_sel)
    cdp.pause(220)
    # 角色描述
    trace["character"] = set_text(cdp, "#sheetChar", case["character"])
    cdp.pause(250)
    # 记录页面实际选中的值（用页面自身的 sheetPayload，等价于用户看到的选择）
    payload = read_state(cdp, "return JSON.stringify(typeof sheetPayload==='function'?sheetPayload():{});")
    trace["payload"] = json.loads(payload) if payload else {}
    return trace


def upload_ref(cdp: CDP, path: str) -> tuple[bool, str]:
    """模拟真人上传参考图：先点「＋ 上传」，用 CDP 给真实 file input 注入文件。

    页面 openFilePicker() 动态创建的 input 没有挂到 DOM，无头浏览器不会弹系统文件框，
    因此在页面上补一个同款 input（onchange 与页面一致：交给 uploadFiles 处理），
    由 CDP 的 DOM.setFileInputFiles 完成"选文件"，剩下的上传完全走页面正常流程。
    """
    before = cdp.js("UI.refs.length")
    if not click(cdp, "#addRef"):
        return False, "找不到上传按钮"
    cdp.js("""(()=>{
        const old=document.querySelector('#__e2e_file'); if(old) old.remove();
        const i=document.createElement('input');
        i.type='file'; i.id='__e2e_file'; i.accept='image/*'; i.multiple=true;
        i.style.display='none';
        i.addEventListener('change', ()=>uploadFiles([...i.files]));
        document.body.appendChild(i); return true;
    })()""")
    got = cdp.call("Runtime.evaluate", {"expression": "document.querySelector('#__e2e_file')"})
    oid = (got.get("result") or {}).get("objectId")
    if not oid:
        return False, "页面里没建起 file input"
    cdp.call("DOM.setFileInputFiles", {"files": [path], "objectId": oid})
    ok = cdp.wait(f"UI.refs.length>{before}", 8)
    if not ok:
        # 某些 Chrome 版本只设文件不派发 change，补一次真实 DOM 事件
        cdp.js("(()=>{const i=document.querySelector('#__e2e_file'); if(i) i.dispatchEvent(new Event('change',{bubbles:true})); return true;})()")
        ok = cdp.wait(f"UI.refs.length>{before}", 15)
    cdp.js("(()=>{const i=document.querySelector('#__e2e_file'); if(i) i.remove(); return true;})()")
    return bool(ok), f"refs={cdp.js('UI.refs.length')}"


def open_preview(cdp: CDP) -> str:
    """点「看完整提示词」，读页面预览的整条提示词，再收起（让控件切换能重绘）。"""
    if not click(cdp, "#sheetPvBtn"):
        return ""
    # 预览是异步请求，而 UI.sheetPreview 在面板收起后不会清空：只等「prompt 存在」
    # 会读到上一条用例的旧结果。这里等它带上当前输入框里的角色描述才算这次的。
    char = str(cdp.js("(document.querySelector('#sheetChar')||{}).value||''") or "").strip()
    if char:
        cdp.wait(f"UI.sheetPreview && UI.sheetPreview.prompt && "
                 f"UI.sheetPreview.prompt.includes({q(char)})", 15)
    else:
        cdp.wait("UI.sheetPreview && !!UI.sheetPreview.prompt", 15)
    cdp.pause(250)
    text = cdp.js("(UI.sheetPreview && UI.sheetPreview.prompt) || ''") or ""
    err = cdp.js("(UI.sheetPreview && UI.sheetPreview.error) || ''") or ""
    # 收起
    cdp.js("(()=>{const b=document.querySelector('#sheetPvBtn'); if(b && b.textContent.includes('收起')) b.click(); return true;})()")
    cdp.pause(250)
    return text if not err else f"[预览错误] {err}"


def job_brief(cdp: CDP, job_id: str) -> dict[str, Any]:
    raw = cdp.js(f"""(()=>{{
        const j=S.jobs.find(x=>x.id==={q(job_id)}); if(!j) return '';
        const o={{id:j.id,cap:j.cap,wf:j.wf,prompt:j.prompt,brief:j.brief,sheet:j.sheet,
                 seed:j.seed,params:j.params,gradeTag:j.gradeTag,refs:j.refs,
                 runs:j.runs.map(r=>({{id:r.id,mode:r.mode,grade:r.grade,state:r.state,seed:r.seed,
                     size:r.size,files:r.files,applied:r.applied,sizeExpected:r.sizeExpected,
                     err:r.err&&r.err.msg}}))}};
        return JSON.stringify(o);
    }})()""")
    return json.loads(raw) if raw else {}


def wait_idle(cdp: CDP, timeout: float = 240.0) -> bool:
    return bool(cdp.wait(
        "S.jobs.every(j=>j.runs.every(r=>!['queued','running'].includes(r.state)))", timeout, 0.6))


def wait_new_job(cdp: CDP, before: int, timeout: float = 300.0) -> str:
    raw = cdp.wait(
        f"S.jobs.length>{before} ? S.jobs[S.jobs.length-1].id : false", 25, 0.4)
    if not raw:
        return ""
    dead = time.time() + timeout
    while time.time() < dead:
        st = cdp.js(f"""(()=>{{const j=S.jobs[S.jobs.length-1];
            return j && j.runs.length && j.runs.every(r=>['done','failed','canceled'].includes(r.state))
              ? j.id : false;}})()""")
        if st:
            return str(st)
        time.sleep(0.8)
    return ""


def do_send(cdp: CDP) -> dict[str, Any]:
    before = cdp.js("S.jobs.length")
    if not click(cdp, "#sendBtn"):
        return {"ok": False, "reason": "找不到发送按钮"}
    jid = wait_new_job(cdp, int(before))
    if not jid:
        return {"ok": False, "reason": "点了发送后没有等到任务完成（或超时）"}
    return {"ok": True, "job": job_brief(cdp, jid)}


def do_queue(cdp: CDP, job_id: str, grade: str) -> dict[str, Any]:
    before = cdp.js(f"(S.jobs.find(x=>x.id==={q(job_id)})||{{runs:[]}}).runs.length")
    if not click_value(cdp, "data-qadd", job_id):
        return {"ok": False, "reason": "找不到加入队列生成按钮"}
    if not cdp.wait("!!document.querySelector('[data-qg]')", 10):
        return {"ok": False, "reason": "档位弹窗没出现"}
    if not click_value(cdp, "data-qg", grade):
        return {"ok": False, "reason": f"弹窗里没有档位 {grade}"}
    cdp.pause(150)
    if not click(cdp, "#qGo"):
        return {"ok": False, "reason": "弹窗确认按钮没点到"}
    ok = cdp.wait(f"""(()=>{{const j=S.jobs.find(x=>x.id==={q(job_id)});
        return j && j.runs.length>{before} &&
          j.runs.slice({before}).every(r=>['done','failed','canceled'].includes(r.state));}})()""", 300, 0.8)
    if not ok:
        return {"ok": False, "reason": "队列任务超时"}
    return {"ok": True, "job": job_brief(cdp, job_id), "new_run_index": int(before)}


def do_again(cdp: CDP, job_id: str) -> dict[str, Any]:
    before = cdp.js("S.jobs.length")
    if not click_value(cdp, "data-again", job_id):
        return {"ok": False, "reason": "找不到再来一张按钮"}
    jid = wait_new_job(cdp, int(before))
    if not jid:
        return {"ok": False, "reason": "点了再来一张后没等到任务完成"}
    return {"ok": True, "job": job_brief(cdp, jid)}


# ------------------------------------------------------------ 提示词核对
def prompt_checks(case: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    """按提示词库的功能描述，核对整条提示词里该有的段落是否真的在里面。"""
    out: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        out.append({"item": name, "ok": bool(ok), "detail": detail})

    layout = next(l for l in LIB["layouts"] if l["id"] == case["layout"])
    add("版式开头句", str(layout.get("head", ""))[:18] in prompt, str(layout.get("name")))
    add("版式格位描述", "第一区（最左）" in prompt if case["layout"] == "A" else
        ("左上格" in prompt if case["layout"] == "B" else "构图：角色的正面全身站姿" in prompt))
    add("通用块不分割线", ("没有竖向分割线" in prompt) if case["layout"] == "A" else
        ("没有横竖分割线" in prompt) if case["layout"] == "B" else True)
    add("同一人物/单张约束", ("四个区域画的是同一个人" in prompt) if case["layout"] == "A" else
        ("四个方格画的是同一个人" in prompt) if case["layout"] == "B" else
        ("不要出现侧面、背面" in prompt))
    add("纯白底平光收尾", ("均匀柔和的正面平光" in prompt))
    add("角色变量行", f"角色：{case['character']}" in prompt)
    if case["mode"] == "i2i":
        if case["ref"] == "lock":
            add("R1 锁定同角色声明", LIB["blocks"]["R1"]["text"] in prompt)
            add("画风跟随图1", "画风与图 1 保持一致" in prompt)
        else:
            add("R2 置顶", prompt.startswith(LIB["blocks"]["R2"]["text"]))
    else:
        add("文生图无参考声明", "图 1 " not in prompt)
    if case["ink"] == "line":
        ls = next(s for s in LIB["line_styles"] if s["id"] == case["lineStyle"])
        add("线稿句", ls["text"] in prompt, ls["name"])
        add("风格行=线稿句", f"风格：{ls['text']}" in prompt)
        # 线稿不能把彩色风格正文（含上色/光影/材质）整句带进来，会和黑白线稿打架
        add("彩色风格正文未混入线稿",
            all(s["text"] not in prompt for s in LIB["styles"]))
        if case.get("styleText"):
            add("所选画风（自定义）生效",
                "造型气质：" in prompt and ("自定义画风" in prompt or case["styleText"][:20] in prompt),
                case["styleText"][:24])
        elif case.get("style"):
            name = case["style"]
            profile = (LIB.get("line_style_profiles") or {}).get(name, "")
            add("所选画风生效（造型气质行）",
                "造型气质：" in prompt and name in prompt and (not profile or profile in prompt),
                f"用户选了「{name}」")
    else:
        if case.get("styleText"):
            add("自定义风格句", case["styleText"] in prompt)
        elif case.get("style"):
            preset = next((s for s in LIB["styles"] if s["name"] == case["style"]), None)
            add("风格预设句", bool(preset and preset["text"] in prompt),
                case["style"])
    return out


def size_expect(layout: str, grade: str) -> tuple[int, int]:
    import math
    l = next(x for x in LIB["layouts"] if x["id"] == layout)
    wr, hr = (16, 9) if layout == "A" else (9, 16)
    mp = {"默认": 1.265625, "快速": 1.0, "精细": 2.0}[grade]
    mul = {"默认": 16, "快速": 8, "精细": 20}[grade]
    scale = math.sqrt(mp * 1024 * 1024 / (wr * hr))
    return round(wr * scale / mul) * mul, round(hr * scale / mul) * mul


def image_diff(p1: str, p2: str) -> dict[str, Any]:
    from PIL import Image, ImageChops
    try:
        a = Image.open(p1).convert("RGB")
        b = Image.open(p2).convert("RGB")
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
    a2 = a.resize((256, 256))
    b2 = b.resize((256, 256))
    diff = ImageChops.difference(a2, b2)
    hist = diff.histogram()
    total = sum(hist)
    mean = sum(i % 256 * n for i, n in enumerate(hist)) / max(1, total) / 3
    return {"ok": True, "size1": list(a.size), "size2": list(b.size),
            "mean_abs_diff_0_255": round(mean, 2)}


def probe(cdp: CDP) -> None:
    info = {
        "capId": cdp.js("UI.capId"),
        "route": cdp.js("UI.route"),
        "mode": cdp.js("UI.mode"),
        "sheetForm": cdp.js("JSON.stringify(UI.sheetForm||{})"),
        "combos": cdp.js("S.sheetCombos.length"),
        "combo_ids": cdp.js("JSON.stringify(S.sheetCombos.map(c=>c.id))"),
        "grades_of_current": cdp.js("JSON.stringify((sheetGrades()||[]).map(g=>({name:g.name,size:g.size})))"),
        "jobs": cdp.js("S.jobs.length"),
        "health": cdp.js("JSON.stringify(S.health)"),
        "dom": cdp.js("JSON.stringify({char:!!document.querySelector('#sheetChar'),send:!!document.querySelector('#sendBtn'),styles:document.querySelectorAll('#sheetStyle option').length,lines:document.querySelectorAll('#sheetLine option').length,layouts:document.querySelectorAll('[data-sheet-layout]').length,grades:document.querySelectorAll('[data-sheet-grade]').length,refs:UI.refs.length})"),
    }
    log("PROBE " + json.dumps(info, ensure_ascii=False))


def run_case(cdp: CDP, case: dict[str, Any]) -> dict[str, Any]:
    cid = case["id"]
    log(f"--- START {cid} {case.get('title','')}")
    t0 = time.time()
    clean_draft(cdp)
    trace = configure(cdp, case)
    # 预览（页面自己组装的那条）
    preview = open_preview(cdp)
    # 参考图：图生图要上传一张真实图片
    up = None
    if case["mode"] == "i2i":
        path = case.get("refImage") or str(APP / "data" / "uploads" / "ref_1849.png")
        need = not cdp.js("UI.refs.length>0")
        if need:
            up = upload_ref(cdp, path)
            log(f"{cid} 上传参考图：{up[0]} {up[1]}")
            if not up[0]:
                row = {"id": cid, "case": case, "ok": False, "trace": trace,
                       "preview_prompt": preview, "upload": up,
                       "reason": "参考图上传失败，本次不发送",
                       "elapsed_s": round(time.time() - t0, 1)}
                record(row)
                log(f"--- FAIL {cid} 参考图上传失败")
                return row
        else:
            up = (True, "沿用已上传参考图")
    # 检查页面选中的档位/组合高亮是否与用例一致
    ui_state = {
        "grade_on": cdp.js("JSON.stringify([...document.querySelectorAll('[data-sheet-grade].on')].map(e=>e.dataset.sheetGrade))"),
        "layout_on": cdp.js("JSON.stringify([...document.querySelectorAll('[data-sheet-layout].on')].map(e=>e.dataset.sheetLayout))"),
        "ink_on": cdp.js("JSON.stringify([...document.querySelectorAll('[data-sheet-ink].on')].map(e=>e.dataset.sheetInk))"),
        "line_value": cdp.js("(document.querySelector('#sheetLine')||{}).value||''"),
        "style_value": cdp.js("(document.querySelector('#sheetStyle')||{}).value||''"),
        "refs": cdp.js("UI.refs.length"),
    }
    res = do_send(cdp)
    row: dict[str, Any] = {
        "id": cid, "case": case, "trace": trace, "ui_state": ui_state,
        "preview_prompt": preview, "upload": up,
        "elapsed_s": 0, "ok": False,
    }
    if not res["ok"]:
        row["reason"] = res["reason"]
        row["elapsed_s"] = round(time.time() - t0, 1)
        log(f"--- FAIL {cid} {res['reason']}")
        record(row)
        return row
    job = res["job"]
    run0 = job["runs"][0]
    row.update({
        "ok": True, "job": job,
        "submitted_prompt": job.get("prompt", ""),
        "seed": run0.get("seed"), "size": run0.get("size"),
        "grade": run0.get("grade"), "wf": job.get("wf"),
        "image": (run0.get("files") or [{}])[0].get("path", ""),
        "elapsed_s": round(time.time() - t0, 1),
        "preview_equals_submitted": preview == job.get("prompt", ""),
        "prompt_checks": prompt_checks(case, job.get("prompt", "")),
        "size_expect": list(size_expect(case["layout"], case["grade"])),
    })
    # 页面档位尺寸是「约」值（ComfyUI 的 ResolutionSelector 按 16 对齐），
    # 判定用宽高比 + 总像素数偏差，不要求逐像素相等。
    try:
        w, h = [int(x) for x in str(row["size"]).split("×")]
        ew, eh = row["size_expect"]
        ratio_ok = abs((w / h) / (ew / eh) - 1) < 0.02
        pixel_ok = abs((w * h) / (ew * eh) - 1) < 0.05
        row["size_ok"] = bool(ratio_ok and pixel_ok)
        row["size_delta_pct"] = round(((w * h) / (ew * eh) - 1) * 100, 2)
    except Exception:
        row["size_ok"] = False
        row["size_delta_pct"] = None
    log(f"--- DONE {cid} grade={row['grade']} size={row['size']} seed={row['seed']} "
        f"t={row['elapsed_s']}s checks={sum(1 for c in row['prompt_checks'] if c['ok'])}/{len(row['prompt_checks'])}")
    record(row)
    return row


def run_queue_case(cdp: CDP, case: dict[str, Any]) -> dict[str, Any]:
    cid, jid, grade = case["id"], case["job_id"], case["grade"]
    log(f"--- START {cid} 加入队列生成 {grade}")
    t0 = time.time()
    base = job_brief(cdp, jid)
    res = do_queue(cdp, jid, grade)
    row: dict[str, Any] = {"id": cid, "case": case, "base": base, "ok": False,
                           "elapsed_s": 0}
    if not res["ok"]:
        row["reason"] = res["reason"]
        row["elapsed_s"] = round(time.time() - t0, 1)
        log(f"--- FAIL {cid} {res['reason']}")
        record(row)
        return row
    job = res["job"]
    idx = res["new_run_index"]
    new = job["runs"][idx]
    base_run = base["runs"][0]
    p1 = (base_run.get("files") or [{}])[0].get("path", "")
    p2 = (new.get("files") or [{}])[0].get("path", "")
    diff = image_diff(p1, p2) if p1 and p2 else {"ok": False, "reason": "缺图片路径"}
    row.update({
        "ok": True, "job": job, "new_run": new,
        "seed_base": base.get("seed"), "seed_new": new.get("seed"),
        "same_seed": base.get("seed") == new.get("seed"),
        "same_prompt": base.get("prompt") == job.get("prompt"),
        "same_wf": base_run.get("wf", base.get("wf")) == new.get("wf", job.get("wf")),
        "size_base": base_run.get("size"), "size_new": new.get("size"),
        "grade_new": new.get("grade"), "image_diff": diff,
        "image_base": p1, "image_new": p2,
        "elapsed_s": round(time.time() - t0, 1),
    })
    log(f"--- DONE {cid} grade={row['grade_new']} same_seed={row['same_seed']} "
        f"size={row['size_new']} diff={diff}")
    record(row)
    return row


def run_again_case(cdp: CDP, case: dict[str, Any]) -> dict[str, Any]:
    cid, jid = case["id"], case["job_id"]
    log(f"--- START {cid} 再来一张")
    t0 = time.time()
    base = job_brief(cdp, jid)
    res = do_again(cdp, jid)
    row: dict[str, Any] = {"id": cid, "case": case, "base": base, "ok": False, "elapsed_s": 0}
    if not res["ok"]:
        row["reason"] = res["reason"]
        row["elapsed_s"] = round(time.time() - t0, 1)
        log(f"--- FAIL {cid} {res['reason']}")
        record(row)
        return row
    job = res["job"]
    r = job["runs"][0]
    b = base["runs"][0]
    p1 = (b.get("files") or [{}])[0].get("path", "")
    p2 = (r.get("files") or [{}])[0].get("path", "")
    diff = image_diff(p1, p2) if p1 and p2 else {"ok": False, "reason": "缺图片路径"}
    row.update({
        "ok": True, "job": job, "new_run": r,
        "seed_base": base.get("seed"), "seed_new": r.get("seed"),
        "new_seed": base.get("seed") != r.get("seed"),
        "same_prompt": base.get("prompt") == job.get("prompt"),
        "same_grade": b.get("grade") == r.get("grade"),
        "same_params": base.get("params") == job.get("params"),
        "same_wf": base.get("wf") == job.get("wf"),
        "size_base": b.get("size"), "size_new": r.get("size"),
        "image_diff": diff, "image_base": p1, "image_new": p2,
        "elapsed_s": round(time.time() - t0, 1),
    })
    log(f"--- DONE {cid} new_seed={row['new_seed']} same_prompt={row['same_prompt']} "
        f"same_grade={row['same_grade']} diff={diff}")
    record(row)
    return row


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    proc, cdp = start_browser()
    try:
        if not ensure_sheet(cdp):
            log("FATAL: 没能进入角色设定图页面")
            return
        if cmd == "probe":
            probe(cdp)
            return
        if cmd == "one":
            case = json.loads(sys.argv[2])
            run_case(cdp, case)
            return
        if cmd == "batch":
            cases = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
            already = done_ids()
            for case in cases:
                if case["id"] in already:
                    log(f"--- SKIP {case['id']} 已完成")
                    continue
                kind = case.get("kind", "gen")
                if kind == "gen":
                    run_case(cdp, case)
                elif kind == "queue":
                    # 依赖前一个用例生成的 job id：从 cases 里的 from_case 解析
                    jid = case.get("job_id")
                    if not jid and case.get("from_case"):
                        jid = resolve_job_id(case["from_case"])
                        case["job_id"] = jid
                    if not jid:
                        log(f"--- FAIL {case['id']} 找不到来源任务")
                        record({"id": case["id"], "case": case, "ok": False,
                                "reason": "找不到来源任务"})
                        continue
                    run_queue_case(cdp, case)
                elif kind == "again":
                    jid = case.get("job_id")
                    if not jid and case.get("from_case"):
                        jid = resolve_job_id(case["from_case"])
                        case["job_id"] = jid
                    if not jid:
                        log(f"--- FAIL {case['id']} 找不到来源任务")
                        record({"id": case["id"], "case": case, "ok": False,
                                "reason": "找不到来源任务"})
                        continue
                    run_again_case(cdp, case)
                wait_idle(cdp)
            return
    finally:
        cdp.close()
        proc.terminate()


def resolve_job_id(case_id: str) -> str:
    if not RESULTS.exists():
        return ""
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("id") == case_id and row.get("ok"):
                job = row.get("job") or {}
                if job.get("id"):
                    return job["id"]
        except Exception:
            pass
    return ""


if __name__ == "__main__":
    main()
