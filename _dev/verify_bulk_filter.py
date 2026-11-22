"""验证管理台：分类/标签多选筛选 + 批量删除（多选/全选）。

注意：本脚本会真实删除模板数据！运行前必须已备份 data/prompt_templates/，
运行后由外部脚本/命令从备份恢复。
"""
from __future__ import annotations

import json, os, subprocess, sys, time, urllib.request
from pathlib import Path
import websocket

sys.stdout.reconfigure(encoding="utf-8")
APP = Path(__file__).resolve().parents[1]
BASE = os.environ.get("SHOT_BASE", "http://127.0.0.1:8190")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
SHOTS = APP / "_dev" / "shots"; SHOTS.mkdir(parents=True, exist_ok=True)


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=120, suppress_origin=True); self.seq = 0
    def call(self, m, p=None):
        self.seq += 1; mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg: raise RuntimeError(f"{m}: {msg['error']}")
                return msg.get("result", {})
    def js(self, e):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r: raise RuntimeError("JS:" + str(r["exceptionDetails"])[:500])
        return r.get("result", {}).get("value")
    def wait(self, e, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js(e): return True
            except Exception: pass
            time.sleep(0.25)
        return False
    def pause(self, ms=200): self.js(f"new Promise(r=>setTimeout(r(1),{ms}))")
    def shot(self, name):
        import base64
        d = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
        p = SHOTS / f"{name}.png"; p.write_bytes(base64.b64decode(d["data"])); return str(p)


def start():
    port = 9351 + (os.getpid() % 20)
    prof = APP / ".cache" / f"edge_bulk_{port}_{int(time.time())}"; prof.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--disable-background-networking", "--disk-cache-size=1", "--remote-allow-origins=*",
        f"--remote-debugging-port={port}", f"--user-data-dir={prof}", "--window-size=1440,1000", BASE + "/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + 40
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                for t in json.loads(r.read().decode()):
                    if t.get("type") == "page" and BASE.rsplit(":", 1)[-1] in t.get("url", ""):
                        c = CDP(t["webSocketDebuggerUrl"]); c.call("Runtime.enable"); c.call("Page.enable")
                        c.call("Network.enable"); c.call("Network.setCacheDisabled", {"cacheDisabled": True})
                        return proc, c
        except Exception: pass
        time.sleep(0.5)
    proc.terminate(); raise RuntimeError("连不上 Edge")


def api_count():
    with urllib.request.urlopen(BASE + "/api/prompt-templates") as r:
        return len(json.loads(r.read().decode())["data"]["templates"])


proc, cdp = start()
out = {}
try:
    count0 = api_count()
    out["count_before"] = count0

    cdp.js("try{localStorage.clear()}catch(e){}; location.hash='#admin'; location.reload()")
    assert cdp.wait("typeof UI!=='undefined' && UI.route==='admin'", 40), "没进管理台"
    cdp.js("UI.adminPage='prompts'; render()")
    assert cdp.wait("!!document.querySelector('.pt-toolbar #ptCatFilter') && PromptLib._state.loaded===true", 40), "模板库没加载"
    cdp.pause(500)

    # 1. 分类多选：图片 + 视频
    cdp.js("document.querySelector('#ptCatFilter').click()")
    assert cdp.wait("!!document.querySelector('[data-afid=image]')", 10)
    cdp.js("document.querySelector('[data-afid=image]').click()")
    cdp.js("document.querySelector('[data-afid=video]').click()")
    cdp.js("document.querySelector('[data-afdone]').click()")
    cdp.pause(400)
    out["cat_multi"] = {
        "cats": cdp.js("PromptLib._state.filters.cats"),
        "cur": cdp.js("document.querySelector('#ptCatCur').textContent"),
        "count_text": cdp.js("document.querySelector('#ptCount').textContent"),
        "rows": cdp.js("PromptLib._state.filters.cats.length && [...document.querySelectorAll('.pt-cat')].every(e=>['image','video'].includes(e.value))"),
    }

    # 2. 标签多选：取前两个标签
    tagids = cdp.js("PromptLib._state.tags.slice(0,2).map(t=>t.id)")
    cdp.js("document.querySelector('#ptTagFilter').click()")
    assert cdp.wait("!!document.querySelector('[data-afid]')", 10)
    for tid in tagids:
        cdp.js(f"document.querySelector('[data-afid=\\\"{tid}\\\"]').click()")
    cdp.js("document.querySelector('[data-afdone]').click()")
    cdp.pause(400)
    out["tag_multi"] = {
        "tags": cdp.js("PromptLib._state.filters.tags"),
        "cur": cdp.js("document.querySelector('#ptTagCur').textContent"),
        "count_text": cdp.js("document.querySelector('#ptCount').textContent"),
    }
    out["shot_filtered"] = cdp.shot("verify_bulk_filtered")

    # 3. 清空筛选，恢复全部
    cdp.js("PromptLib._state.filters.cats=[]; PromptLib._state.filters.tags=[]; UI.adminPage='prompts'; render()")
    cdp.wait("!!document.querySelector('#ptCheckAll')", 15)
    cdp.pause(400)

    # 4. 多选删除：勾选前 2 张卡片
    cdp.js("[...document.querySelectorAll('.pt-card [data-pick]')].slice(0,2).forEach(e=>e.click())")
    cdp.pause(200)
    out["picked_2"] = {
        "sel": cdp.js("PromptLib._state.selected.size"),
        "info": cdp.js("document.querySelector('#ptSelInfo').textContent"),
        "delDisabled": cdp.js("document.querySelector('#ptBulkDel').disabled"),
    }
    cdp.js("document.querySelector('#ptBulkDel').click()")
    assert cdp.wait("!!document.querySelector('#ptBulkYes')", 10)
    cdp.js("document.querySelector('#ptBulkYes').click()")
    assert cdp.wait("PromptLib._state.selected.size===0", 20)
    cdp.pause(500)
    out["after_pick_del"] = {"count": api_count(), "delta": count0 - api_count()}

    # 5. 全选删除：先筛一个小分类，全选后删除（避免全删 2000 条）
    cdp.js("PromptLib._state.filters.cats=['video']; UI.adminPage='prompts'; render()")
    cdp.wait("!!document.querySelector('#ptCheckAll')", 15)
    cdp.pause(300)
    filtered_n = cdp.js("document.querySelector('#ptCount').textContent")
    cdp.js("document.querySelector('#ptCheckAll').click()")
    cdp.pause(200)
    out["select_all"] = {
        "count_text": filtered_n,
        "sel": cdp.js("PromptLib._state.selected.size"),
        "allChecked": cdp.js("document.querySelector('#ptCheckAll').checked"),
        "delDisabled": cdp.js("document.querySelector('#ptBulkDel').disabled"),
    }
    cdp.js("document.querySelector('#ptBulkDel').click()")
    assert cdp.wait("!!document.querySelector('#ptBulkYes')", 10)
    cdp.js("document.querySelector('#ptBulkYes').click()")
    assert cdp.wait("PromptLib._state.selected.size===0", 40)
    cdp.pause(600)
    out["after_all_del"] = {"count": api_count(), "video_left": cdp.js("PromptLib._state.templates.filter(t=>t.category==='video').length")}

    print(json.dumps(out, ensure_ascii=False, indent=1))
finally:
    proc.terminate()
