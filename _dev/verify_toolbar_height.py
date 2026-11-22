"""验证管理台模板库工具栏控件高度是否与顶部 .btn.sm 一致（28px）。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

sys.stdout.reconfigure(encoding="utf-8")
APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "_dev"))
from verify_prompt_switch import CDP, BASE, EDGE, SHOTS  # noqa: E402

PORT = 9371
PROFILE = APP / ".cache" / f"edge_height_{PORT}_{int(time.time())}"
PROFILE.mkdir(parents=True, exist_ok=True)

proc = subprocess.Popen([
    EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
    "--no-first-run", "--disable-background-networking", "--disk-cache-size=1",
    "--remote-allow-origins=*", f"--remote-debugging-port={PORT}",
    f"--user-data-dir={PROFILE}", "--window-size=1440,1000", BASE + "/",
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    tabs = None
    for _ in range(60):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2))
            break
        except Exception:
            time.sleep(0.25)
    if not tabs:
        raise RuntimeError("CDP 未就绪")
    page = next(t for t in tabs if t.get("type") == "page")
    c = CDP(page["webSocketDebuggerUrl"])
    c.call("Page.enable")
    c.call("Runtime.enable")

    c.js(f"location.href='{BASE}/#admin/prompts'")
    if not c.wait("typeof UI!=='undefined' && UI.route==='admin' && UI.adminPage==='prompts'", 30):
        raise RuntimeError("未进入模板库页面")
    if not c.wait("!!(document.querySelector('#ptTagMgr') && document.querySelector('.pt-filter-btn'))", 30):
        diag = c.js("""(() => ({route:UI.route,adminPage:UI.adminPage,
          lib:typeof window.PromptLib, h2:document.querySelector('.pt-head h2')?.textContent||null,
          root:document.querySelector('#root')?.innerHTML.slice(0,300)||null}))()""")
        raise RuntimeError("工具栏未渲染: " + json.dumps(diag, ensure_ascii=False))
    if not c.wait("!!document.querySelector('.pt-card')", 30):
        raise RuntimeError("卡片未渲染")

    data = c.js("""(() => {
      const h = s => { const e=document.querySelector(s); return e ? Math.round(e.getBoundingClientRect().height*10)/10 : null; };
      const btns=[...document.querySelectorAll('.pt-filter-btn')].map(e=>({t:e.textContent.trim().slice(0,14),h:Math.round(e.getBoundingClientRect().height*10)/10}));
      const se=document.querySelector('.pt-searchbox input');
      const card=document.querySelector('.pt-card');
      return {
        tagMgr:h('#ptTagMgr'), add:h('#ptAdd'),
        search:h('.pt-searchbox input'), searchFont:se?getComputedStyle(se).fontSize:null,
        filters:btns, count:h('.pt-count'), bulkDel:h('#ptBulkDel'),
        cardCat:card?Math.round(card.querySelector('.pt-cat').getBoundingClientRect().height*10)/10:null,
        cardTitle:card?Math.round(card.querySelector('.pt-title').getBoundingClientRect().height*10)/10:null,
        cardSave:card?Math.round(card.querySelector('.pt-ico.pt-save').getBoundingClientRect().height*10)/10:null,
        cardDel:card?Math.round(card.querySelector('.pt-ico.danger').getBoundingClientRect().height*10)/10:null,
        bodyOverflow:document.documentElement.scrollWidth>document.documentElement.clientWidth
      };
    })()""")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    ok = (all(v == 28 for v in [data["tagMgr"], data["add"], data["search"],
                                data["cardCat"], data["cardTitle"], data["cardSave"], data["cardDel"]])
          and all(b["h"] == 28 for b in data["filters"]))
    print("结论：", "全部 28px，一致" if ok else "存在不一致")
    p = c.shot("verify_toolbar_height")
    print("截图：", p)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
