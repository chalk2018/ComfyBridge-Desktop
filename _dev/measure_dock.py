"""测量提示词速取窗内各控件当前高度。"""
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
from verify_prompt_switch import CDP, BASE, EDGE  # noqa: E402

PORT = 9382
PROFILE = APP / ".cache" / f"edge_dock_{PORT}_{int(time.time())}"
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
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2)); break
        except Exception:
            time.sleep(0.25)
    page = next(t for t in tabs if t.get("type") == "page")
    c = CDP(page["webSocketDebuggerUrl"])
    c.call("Page.enable"); c.call("Runtime.enable")
    c.wait("typeof UI!=='undefined'", 30)
    # 确保开关开启：点顶栏开关（若无则说明已开）
    c.js("(()=>{const b=document.querySelector('#ptTopDockSwitch'); if(b && b.getAttribute('aria-checked')==='false') b.click(); return true})()")
    c.pause(900)
    c.wait("!!document.querySelector('.pt-dock:not([hidden]) .pt-dock-tools input')", 30)
    data = c.js("""(() => {
      const d=document.querySelector('.pt-dock'); if(!d) return {err:'no dock'};
      const h=e=>e?Math.round(e.getBoundingClientRect().height*10)/10:null;
      return {
        head:h(d.querySelector('.pt-dock-head')),
        search:h(d.querySelector('.pt-dock-search input')),
        chips:[...d.querySelectorAll('.pt-dchip')].map(h),
        tagfilter:h(d.querySelector('.pt-tagfilter-btn')),
        add:h(d.querySelector('[data-dadd]')),
        tagmgr:h(d.querySelector('[data-dtags]')),
        listCard:h(d.querySelector('.pt-dcard'))
      };
    })()""")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print("截图：", c.shot("verify_dock_height"))
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
