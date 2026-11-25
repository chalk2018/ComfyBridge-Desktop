"""测量某个 figure 的实际渲染尺寸。"""

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
PORT = 9289
PROFILE = Path(__file__).resolve().parents[1] / ".cache" / "edge_measure"


def main() -> None:
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
    cdp = C(ws_url)
    cdp.call("Runtime.enable")
    time.sleep(1.5)
    out = cdp.js("""(() => {
      const figs = [...document.querySelectorAll('figure')];
      const res = [];
      for (const f of figs) {
        const img = f.querySelector('img');
        if (!img) continue;
        if (!/data:image/.test(img.getAttribute('src') || '')) continue;
        const r = f.getBoundingClientRect();
        const ir = img.getBoundingClientRect();
        res.push({cls: f.className, style: f.getAttribute('style') || '',
                  fw: Math.round(r.width), iw: Math.round(ir.width),
                  ih: Math.round(ir.height), nat: img.naturalWidth + 'x' + img.naturalHeight});
      }
      return res;
    })()""")
    for row in out:
        print(row)
    proc.terminate()


if __name__ == "__main__":
    main()
