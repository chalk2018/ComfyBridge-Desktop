"""整页截图（captureBeyondViewport），再按元素坐标裁剪，用于校对图片尺寸。"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc103_shots import C, EDGE  # noqa: E402

DOC = Path(__file__).resolve().parents[2] / "ComfyBridge-需求与功能文档-1.03.html"
PORT = 9293
PROFILE = Path(__file__).resolve().parents[1] / ".cache" / ("edge_full_" + str(int(time.time())))
OUT = Path(__file__).resolve().parent / "docshots"


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
    cdp.call("Page.enable")
    time.sleep(2.0)

    for alt, name in [("u_cap_list", "caplist"), ("u_admin_menu", "adminmenu")]:
        box = cdp.js(f"""(() => {{
          const img = [...document.images].find(i => i.alt === {json.dumps(alt)});
          if (!img) return null;
          const f = img.closest('figure');
          const r = f.getBoundingClientRect();
          return {{x: Math.round(r.left), y: Math.round(r.top + window.scrollY),
                  w: Math.round(r.width), h: Math.round(r.height)}};
        }})()""")
        print(alt, box)
        clip = {"x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"], "scale": 1}
        data = cdp.call("Page.captureScreenshot",
                        {"format": "png", "clip": clip, "captureBeyondViewport": True})
        p = OUT / f"_full_{name}.png"
        p.write_bytes(base64.b64decode(data["data"]))
        im = Image.open(p)
        print("saved", p.name, im.size)
    proc.terminate()


if __name__ == "__main__":
    main()
