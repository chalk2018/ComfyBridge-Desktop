"""验证模板库瀑布流：两列独立、卡片错落、拉高一列不影响另一列。"""
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

PORT = 9391
PROFILE = APP / ".cache" / f"edge_masonry_{PORT}_{int(time.time())}"
PROFILE.mkdir(parents=True, exist_ok=True)


def open_page(width=1440):
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--disable-background-networking", "--disk-cache-size=1",
        "--remote-allow-origins=*", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}", f"--window-size={width},1000", BASE + "/",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tabs = None
    for _ in range(60):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2)); break
        except Exception:
            time.sleep(0.25)
    page = next(t for t in tabs if t.get("type") == "page")
    c = CDP(page["webSocketDebuggerUrl"])
    c.call("Page.enable"); c.call("Runtime.enable")
    c.js(f"location.href='{BASE}/#admin/prompts'")
    c.wait("typeof UI!=='undefined' && UI.route==='admin' && UI.adminPage==='prompts'", 30)
    c.wait("!!document.querySelector('.pt-col .pt-card')", 30)
    return proc, c


SNAP = """(() => {
  const cols=[...document.querySelectorAll('#ptGrid .pt-col')];
  return {
    colCount:cols.length,
    cardsPerCol:cols.map(c=>c.querySelectorAll('.pt-card').length),
    colHeights:cols.map(c=>Math.round(c.getBoundingClientRect().height)),
    firstIds:cols.map(c=>[...c.querySelectorAll('.pt-card')].slice(0,3).map(x=>x.dataset.id.slice(0,8))),
    noRowAlign:cols.length===2 ? Math.abs(cols[0].getBoundingClientRect().height-cols[1].getBoundingClientRect().height)>1 : null,
    gridDisplay:getComputedStyle(document.querySelector('#ptGrid')).display
  };
})()"""

try:
    proc, c = open_page(1440)
    before = c.js(SNAP)
    print("桌面初始:", json.dumps(before, ensure_ascii=False))

    # 拉高第一列第一张卡片的 textarea（模拟用户拖大输入框）
    c.js("""(() => {
      const ta=document.querySelector('#ptGrid .pt-col:first-child .pt-content');
      ta.style.height='200px'; return true;
    })()""")
    c.pause(200)
    after = c.js("""(() => {
      const cols=[...document.querySelectorAll('#ptGrid .pt-col')];
      return {colHeights:cols.map(c=>Math.round(c.getBoundingClientRect().height))};
    })()""")
    print("拉高后:", json.dumps(after, ensure_ascii=False))
    delta0 = after["colHeights"][0] - before["colHeights"][0]
    delta1 = after["colHeights"][1] - before["colHeights"][1] if len(after["colHeights"]) > 1 else 0
    print(f"列0变化={delta0}px, 列1变化={delta1}px")

    p = c.shot("verify_masonry_desktop")
    print("截图:", p)
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()

    # 移动端单列
    proc2, c2 = open_page(390)
    mob = c2.js(SNAP)
    print("移动端(390px):", json.dumps(mob, ensure_ascii=False))
    print("截图:", c2.shot("verify_masonry_mobile"))
    proc2.terminate()
    try: proc2.wait(timeout=5)
    except Exception: proc2.kill()

    ok = (before["colCount"] == 2 and before["gridDisplay"] == "flex"
          and all(x > 0 for x in before["cardsPerCol"])
          and delta0 > 100 and abs(delta1) <= 2
          and mob["colCount"] == 1)
    print("结论:", "瀑布流布局正常" if ok else "存在问题")
finally:
    for p in (globals().get("proc"), globals().get("proc2")):
        if p and p.poll() is None:
            p.terminate()
