"""1.03 场景 / 道具设定图 · 真实页面 E2E（至少三轮，不同参考图与提示词）。

所有配置、上传、预览、发送都通过真实页面 DOM 控件完成；脚本只通过 CDP 读取
window.S / window.UI 与页面文本做核对，不直接 POST 业务接口。

用法：
    .venv\\Scripts\\python.exe tests\\ext_sheet_e2e.py
结果：
    tests/_ext_sheet_e2e/results.jsonl
"""

from __future__ import annotations

import json
import os
import shutil
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
CDP_PORT = 9231
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_ext_e2e"
OUT = APP / "tests" / "_ext_sheet_e2e"
RESULTS = OUT / "results.jsonl"
OUT.mkdir(parents=True, exist_ok=True)


def log(*parts: Any) -> None:
    print(" ".join(str(p) for p in parts), flush=True)


def record(row: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


class CDP:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=90, suppress_origin=True)
        self.seq = 0

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

    def js(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            raise RuntimeError("JS 异常: " + str(result["exceptionDetails"])[:400])
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 30.0, interval: float = 0.4) -> Any:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                value = self.js(expression)
                if value:
                    return value
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


def _fresh_profile_and_port() -> tuple[Path, int]:
    """每次跑测试都用一份全新的浏览器配置 + 独立调试端口。

    为什么：固定 profile 时，上一轮没退干净的 Edge 会让新进程直接复用它，
    连上的还是那条早已失效的旧页面（报错形如「Cannot find default execution
    context」）。配置目录里塞了磁盘缓存，顺便也就没有旧 app.js 的问题了。
    """
    port = 9231 + (os.getpid() % 60)
    # 每次用独立的新 profile，避免递归删旧目录（沙箱会拦大批量删除）
    profile = PROFILE.parent / f"{PROFILE.name}-{port}-{int(time.time())}"
    profile.mkdir(parents=True, exist_ok=True)
    return profile, port


def start_browser() -> tuple[subprocess.Popen, CDP]:
    profile, port = _fresh_profile_and_port()
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-extensions", "--no-first-run", "--disable-background-networking",
        "--disk-cache-size=1", "--media-cache-size=1",
        "--remote-allow-origins=*", f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}", "--window-size=1440,1000", BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                targets = json.loads(resp.read().decode())
            page = next((t for t in targets if t.get("type") == "page" and "8190" in t.get("url", "")), None)
            if page:
                cdp = CDP(page["webSocketDebuggerUrl"])
                cdp.call("Runtime.enable")
                cdp.call("Page.enable")
                cdp.call("DOM.enable")
                # 绕过磁盘缓存：改了 app.js/index.html 之后，Edge 可能还在跑旧脚本
                cdp.call("Network.enable")
                cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
                return proc, cdp
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("连不上 Edge 调试端口")


def ensure_cap(cdp: CDP, cap_id: str) -> bool:
    if not cdp.wait("typeof UI!=='undefined' && !!document.querySelector('.app .topbar')", 25):
        return False
    if not cdp.js(f"UI.capId==={q(cap_id)}"):
        click(cdp, f'.rail [data-cap="{cap_id}"]')
        cdp.pause(600)
    return bool(cdp.wait(f"UI.capId==={q(cap_id)} && !!document.querySelector('#extText')", 20))


def set_text(cdp: CDP, text: str) -> bool:
    expr = """(()=>{const text=%s;
      const el=document.querySelector('#extText'); if(!el) return false;
      const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
      setter.call(el,text);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      return el.value===text;
    })()""" % q(text)
    return bool(cdp.js(expr))


def set_select(cdp: CDP, selector: str, value: str) -> bool:
    return bool(cdp.js("""([sel,val])=>{
      const el=document.querySelector(sel); if(!el) return false;
      const setter=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;
      setter.call(el,val); el.dispatchEvent(new Event('change',{bubbles:true})); return true;
    }""", )) if False else bool(cdp.js(f"(()=>{{const e=document.querySelector({q(selector)}); if(!e) return false; const s=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set; s.call(e,{q(value)}); e.dispatchEvent(new Event('change',{{bubbles:true}})); return true;}})()"))


def upload_slot(cdp: CDP, slot: str, path: str) -> tuple[bool, str]:
    selector = f'[data-ext-slot-file="{slot}"]'
    got = cdp.call("Runtime.evaluate", {"expression": f"document.querySelector({q(selector)})"})
    oid = (got.get("result") or {}).get("objectId")
    if not oid:
        return False, "页面里没有这个槽位的 file input"
    cdp.call("DOM.setFileInputFiles", {"files": [path], "objectId": oid})
    ok = cdp.wait(f"!!(UI.extForm.refSlots && UI.extForm.refSlots[{q(slot)}])", 8)
    if not ok:
        cdp.js(f"(()=>{{const e=document.querySelector({q(selector)}); if(e) e.dispatchEvent(new Event('change',{{bubbles:true}})); return true;}})()")
        ok = cdp.wait(f"!!(UI.extForm.refSlots && UI.extForm.refSlots[{q(slot)}])", 20)
    return bool(ok), f"slot={slot} refs={cdp.js('UI.refs.length')}"


def clear_refs(cdp: CDP) -> None:
    """把上一轮用例/草稿里留下的参考图通过页面上的移除按钮清掉。"""
    for _ in range(8):
        if click(cdp, "[data-ext-slot-rm]"):
            cdp.pause(180)
            continue
        if click(cdp, "[data-rmref]"):
            cdp.pause(180)
            continue
        break


def upload_prop_ref(cdp: CDP, path: str) -> tuple[bool, str]:
    before = cdp.js("UI.refs.length")
    cdp.js("""(()=>{
      const old=document.querySelector('#__ext_e2e_file'); if(old) old.remove();
      const i=document.createElement('input'); i.type='file'; i.id='__ext_e2e_file';
      i.accept='image/*'; i.style.display='none';
      i.addEventListener('change',()=>uploadFiles([...i.files]));
      document.body.appendChild(i); return true;
    })()""")
    got = cdp.call("Runtime.evaluate", {"expression": "document.querySelector('#__ext_e2e_file')"})
    oid = (got.get("result") or {}).get("objectId")
    if not oid:
        return False, "建不起 file input"
    cdp.call("DOM.setFileInputFiles", {"files": [path], "objectId": oid})
    ok = cdp.wait(f"UI.refs.length>{before}", 8)
    if not ok:
        cdp.js("(()=>{const i=document.querySelector('#__ext_e2e_file'); if(i) i.dispatchEvent(new Event('change',{bubbles:true})); return true;})()")
        ok = cdp.wait(f"UI.refs.length>{before}", 20)
    cdp.js("(()=>{const i=document.querySelector('#__ext_e2e_file'); if(i) i.remove(); return true;})()")
    return bool(ok), f"before={before} after={cdp.js('UI.refs.length')}"


def wait_done(cdp: CDP, before_count: int, timeout: float = 300.0) -> dict[str, Any] | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = cdp.js("""(()=>{
          if(!S || S.jobs.length<=window.__extBefore) return '';
          const j=S.jobs[S.jobs.length-1], r=j.runs[0];
          if(!r || !['done','failed','canceled'].includes(r.state)) return '';
          return JSON.stringify({job:j.id, cap:j.cap, wf:j.wf, prompt:j.prompt, grade:r.grade, state:r.state,
            err:r.err||null, sheet:j.sheet, path:(r.files&&r.files[0]&&r.files[0].path)||''});
        })()""")
        if raw:
            return json.loads(raw)
        time.sleep(1.0)
    return None


def run_case(cdp: CDP, case: dict[str, Any]) -> dict[str, Any]:
    log(f"\n=== {case['id']} {case.get('name','')} ===")
    row: dict[str, Any] = {"id": case["id"], "ok": False}
    if not ensure_cap(cdp, case["capId"]):
        row["error"] = "创作台没切到目标能力"
        return row
    clear_refs(cdp)
    row["refsAfterClear"] = cdp.js("UI.refs.length")
    row["slotsAfterClear"] = cdp.js("JSON.stringify(UI.extForm.refSlots||{})")
    cdp.js("window.__extBefore=S.jobs.length")
    cdp.pause(200)
    row["mode"] = click(cdp, f'[data-mode="{case["mode"]}"]')
    cdp.pause(350)
    row["layout"] = click(cdp, f'[data-ext-layout="{case["layout"]}"]')
    if case.get("sub"):
        cdp.pause(250)
        row["sub"] = click(cdp, f'[data-ext-sub="{case["sub"]}"]')
    cdp.pause(250)
    if case.get("ink"):
        row["ink"] = click(cdp, f'[data-ext-ink="{case["ink"]}"]')
        cdp.pause(200)
    if case.get("nochar"):
        row["nochar"] = click(cdp, f'[data-ext-nochar="{case["nochar"]}"]')
        cdp.pause(200)
    for slot, rel in (case.get("slots") or {}).items():
        ok, detail = upload_slot(cdp, slot, str(APP / rel))
        row[f"slot_{slot}"] = {"ok": ok, "detail": detail}
        cdp.pause(400)
    if case.get("propRef"):
        row["propRef"] = upload_prop_ref(cdp, str(APP / case["propRef"]))
        cdp.pause(400)
    row["text"] = set_text(cdp, case["text"])
    cdp.pause(250)
    row["canSend"] = bool(cdp.js("!document.querySelector('#sendBtn') || !document.querySelector('#sendBtn').disabled"))
    row["refCount"] = cdp.js("UI.refs.length")
    max_refs = cdp.js("Number((currentCap()||{}).maxRefs||3)")
    if row["refCount"] > max_refs:
        row["error"] = f"参考图数量 {row['refCount']} 超过能力上限 {max_refs}"
        return row
    if click(cdp, "#extPvBtn"):
        preview = cdp.wait("!!(UI.extPreview && UI.extPreview.prompt) && UI.extPreview.prompt.includes(" + q(case["text"]) + ")", 20)
        row["preview"] = bool(preview)
        row["previewPrompt"] = cdp.js("(UI.extPreview && UI.extPreview.prompt) || ''") if preview else ""
        click(cdp, "#extPvBtn")
        cdp.pause(250)
    if not row["canSend"]:
        row["error"] = "发送按钮是禁用的"
        return row
    if not click(cdp, "#sendBtn"):
        row["error"] = "点不到发送按钮"
        return row
    done = wait_done(cdp, 0)
    row["done"] = done
    if done:
        row["ok"] = done.get("state") == "done" and bool(done["path"]) and case["text"] in done["prompt"]
    else:
        row["error"] = "等待成品超时"
    log("结果：", "PASS" if row["ok"] else "FAIL", done and done.get("path", ""))
    return row


CASES = [
    {
        "id": "round1_scene_i2i_panorama",
        "name": "场景-图生图-全景：照片基准 + 简陋草图",
        "capId": "sheet.scene", "mode": "i2i", "layout": "P", "ink": "color", "nochar": "yes",
        "slots": {"ref": "_dev/prompt_lab/refs/scene_ref_room.png",
                  "sketch": "_dev/prompt_lab/refs/floorplan_sketch.png"},
        "text": "同一间海边小屋改画成45度俯视全景：左侧壁炉，右侧书桌，中间地毯，窗外能看见海",
    },
    {
        "id": "round2_scene_expand_turn_right75",
        "name": "场景-扩图-右转75度：全景基准 + 彩色布局图",
        "capId": "sheet.scene", "mode": "expand", "layout": "TURN", "sub": "R75",
        "slots": {"ref": "_dev/prompt_lab/refs/scene_panorama.png",
                  "bird": "_dev/prompt_lab/refs/floorplan_color.png"},
        "text": "保持同一处庭院的布局与道具位置，把镜头移到右侧墙面前，正对右侧墙面拍",
    },
    {
        "id": "round3_prop_i2i_multi",
        "name": "道具-图生图-多视图：锁定参考道具",
        "capId": "sheet.prop", "mode": "i2i", "layout": "MV", "ink": "color",
        "propRef": "_dev/prompt_lab/out/w8_prop_multi_3__LAB_w8_prop_multi_3_00001.png",
        "text": "参考图里的雨伞：展开正面、展开顶部伞面、展开伞底、合上后的侧面",
    },
    {
        "id": "round2b_scene_expand_turn_left45",
        "name": "场景-扩图-左转45度修正版：新基准 + 新彩图，禁止照搬色块",
        "capId": "sheet.scene", "mode": "expand", "layout": "TURN", "sub": "L45",
        "slots": {"ref": "_dev/prompt_lab/out/w1_panorama_45__LAB_w1_panorama_45_00001.png",
                  "bird": "_dev/prompt_lab/refs/floorplan_color2.png"},
        "text": "保持同一间茶室的门窗、矮桌与坐垫位置，把镜头向左转到左侧墙面前，正对左墙拍出真实材质",
    },
    {
        "id": "round3b_prop_multi_six",
        "name": "道具-图生图-多视图六视角修正版：必须两排",
        "capId": "sheet.prop", "mode": "i2i", "layout": "MV", "ink": "color",
        "propRef": "_dev/prompt_lab/out/w8_prop_multi_6__LAB_w8_prop_multi_6_00001.png",
        "text": "参考图里的茶壶：盖上盖正面、侧面、底部、顶部、开盖、壶盖特写",
    },
]


def main() -> None:
    proc, cdp = start_browser()
    try:
        only = sys.argv[1] if len(sys.argv) > 1 else ""
        cases = [c for c in CASES if not only or c["id"] == only]
        results = []
        for case in cases:
            row = run_case(cdp, case)
            results.append(row)
            record(row)
        passed = sum(1 for r in results if r["ok"])
        log(f"\n总计：{passed}/{len(results)} 通过")
    finally:
        cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
