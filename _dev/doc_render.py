"""1.03 文档目视校对：headless Edge 打开 HTML，按锚点 id 定位并逐段截图。

用法：
    .venv\\Scripts\\python.exe _dev\\doc_render.py
产物：_dev/docshots/<id>_<n>.png
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc103_shots import C, EDGE  # noqa: E402

DOC = Path(__file__).resolve().parents[2] / "ComfyBridge-需求与功能文档-1.03.html"
OUT = Path(__file__).resolve().parent / "docshots"
PORT = 9282
PROFILE = Path(__file__).resolve().parents[1] / ".cache" / ("edge_docview_" + str(int(time.time())))

IDS = ["t:1.2 左栏：15 个能力", "t:5.7 管理台里长什么样", "t:3.7 管理台里长什么样"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    url = "file:///" + urllib.parse.quote(str(DOC).replace("\\", "/"))
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--no-first-run", "--remote-allow-origins=*",
        f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
        "--window-size=1440,1150", url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(70):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page":
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
    time.sleep(1.5)

    for idv in IDS:
        for k in range(2):
            if idv == "__top__":
                cdp.js(f"window.scrollTo(0, {k * 1000})")
            else:
                if idv.startswith("t:"):
                    sel = "document.querySelectorAll('h1,h2,h3')"
                    code = (f"""(() => {{
                      document.documentElement.style.scrollBehavior = 'auto';
                      const el = [...{sel}].find(e => e.textContent.replace(/\\s+/g,' ').trim().includes({json.dumps(idv[2:])}));
                      if (!el) return false;
                      window.scrollTo({{top: el.getBoundingClientRect().top + window.scrollY - 12 + {k * 1000}, behavior: 'instant'}});
                      return true;
                    }})()""")
                else:
                    code = (f"""(() => {{
                      document.documentElement.style.scrollBehavior = 'auto';
                      const el = document.getElementById({json.dumps(idv)});
                      if (!el) return false;
                      window.scrollTo({{top: el.getBoundingClientRect().top + window.scrollY - 12 + {k * 1000}, behavior: 'instant'}});
                      return true;
                    }})()""")
                ok = cdp.js(code)
                if not ok:
                    print("!! 没有定位", idv)
                    break
            time.sleep(0.7)
            data = cdp.call("Page.captureScreenshot", {"format": "png"})
            p = OUT / (f"{idv}_{k}.png" if idv != "__top__" else f"top_{k}.png")
            p = OUT / (p.name.replace("t:", "txt_"))
            p.write_bytes(base64.b64decode(data["data"]))
            print("saved", p.name)
    proc.terminate()


if __name__ == "__main__":
    main()
