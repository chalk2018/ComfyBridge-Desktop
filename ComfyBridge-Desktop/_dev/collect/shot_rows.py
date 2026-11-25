"""把若干真实采集条目的「素材行」放大截一张，用来核对缩略图尺寸与标记。"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

APP = Path("D:/workspace/WorkBuddyProject/ComfyBridge-Desktop")
sys.path.insert(0, str(APP / "tests"))
import ui_e2e_test as base  # noqa: E402

SHOTS = APP / "_dev" / "_shots500"
SHOTS.mkdir(parents=True, exist_ok=True)


def run() -> None:
    items = json.loads((APP / "_dev/collect/items.json").read_text(encoding="utf-8"))
    picks = []
    for cat, tag in (("audio", "音频"), ("video", "视频"), ("image", "图片")):
        one = next(i for i in items if i["category"] == cat and len(i["files"]) == 2)
        picks.append((one["title"], tag))

    base.api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "prompts", "promptDock": False}})
    proc, cdp = base.start_browser()
    try:
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": 1440, "height": 1000, "deviceScaleFactor": 2, "mobile": False})
        cdp.call("Page.navigate", {"url": base.BASE + f"/?v={int(time.time())}#admin/prompts"})
        cdp.wait("document.querySelectorAll('.pt-card').length>2", 40)
        time.sleep(2)
        for i, (title, _tag) in enumerate(picks, 1):
            cdp.js(f"""(()=>{{const q=document.querySelector('#ptQ'); q.value={json.dumps(title)};
              q.dispatchEvent(new Event('input',{{bubbles:true}}));}})()""")
            time.sleep(1.4)
            box = cdp.js(f"""(()=>{{const c=[...document.querySelectorAll('.pt-card')]
              .find(x=>{{const t=x.querySelector('.pt-title'); return t&&t.value.trim()==={json.dumps(title)};}});
              if(!c) return null; const row=c.querySelector('.pt-media'); const b=row.getBoundingClientRect();
              window.scrollTo(0, b.top+scrollY-300);
              const r=row.getBoundingClientRect();
              return {{x:Math.round(r.left)-8,y:Math.round(r.top+scrollY)-8,
                       width:Math.round(r.width)+16,height:Math.round(r.height)+16,
                       thumbs:c.querySelectorAll('.pt-thumb').length,
                       size:[...c.querySelectorAll('.pt-thumb')].map(x=>Math.round(x.getBoundingClientRect().width)+'x'+Math.round(x.getBoundingClientRect().height))}};}})()""")
            print(title, box)
            if not box:
                continue
            for k in ("thumbs", "size"):
                box.pop(k, None)
            time.sleep(0.6)
            data = cdp.call("Page.captureScreenshot", {"format": "png", "clip": {**box, "scale": 1}})["data"]
            (SHOTS / f"row-{i}-{i and picks[i-1][1]}.png").write_bytes(base64.b64decode(data))
            print("  shot", f"row-{i}-{picks[i-1][1]}.png")
    finally:
        cdp.close()
        proc.terminate()


if __name__ == "__main__":
    run()
