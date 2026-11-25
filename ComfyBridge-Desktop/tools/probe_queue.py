"""验证画布视频节点的「队列生成」弹窗取的是视频档位（不是图片档位）。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_canvas_gen import BASE, CDP_PORT, EDGE, OUT, C, center  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / ".cache" / "edge_queue"


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else "cv1795052084190"
    node = sys.argv[2] if len(sys.argv) > 2 else "nB"
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.4",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page":
                    ws = t["webSocketDebuggerUrl"]
                    break
            if ws:
                break
        except Exception:  # noqa: BLE001
            continue
    cdp = C(ws)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    problems: list[str] = []
    try:
        cdp.call("Page.navigate", {"url": BASE + "/#canvas"})
        time.sleep(4.0)
        # 用工具条上的画布下拉切换：直接写 localStorage 会被画布挂载时写回旧值
        cdp.js("(()=>{const s=document.querySelector('.cvs-bar select');if(!s)return 0;"
               "s.value='%s';s.dispatchEvent(new Event('change',{bubbles:true}));return 1})()" % pid)
        time.sleep(2.5)
        if not cdp.wait("!!document.querySelector('.cnode[data-id=%s] [data-queue]')" % node, 30):
            problems.append("节点上没有「队列生成」按钮")
        else:
            b = center(cdp, ".cnode[data-id=%s] [data-queue]" % node)
            cdp.click_at(b[0], b[1])
            time.sleep(1.2)
            txt = cdp.js("(()=>{const m=document.querySelector('.modal,.sheet-modal,.dlg');"
                         "return m?m.innerText.replace(/\\s+/g,' ').slice(0,420):null})()")
            print("弹窗文字 =", txt)
            if not txt:
                problems.append("点了队列生成没弹窗")
            else:
                opts = cdp.js("(()=>{const m=document.querySelector('.modal,.sheet-modal,.dlg');"
                              "if(!m)return [];return [...m.querySelectorAll('[data-qg]')].map(e=>e.dataset.qg)})()")
                print("弹窗档位 =", opts)
                # 视频档位是 默认/快速/精细/竖屏；图片档位只有 默认
                if len(opts or []) < 2:
                    problems.append(f"视频节点的队列弹窗没有视频档位（只有 {opts}）")
                if "步" not in txt and "LoRA" not in txt:
                    problems.append("弹窗里的档位说明不像是视频口径（比例/分辨率/步数）")
            cdp.shot("q_queue")
    finally:
        try:
            cdp.ws.close()
        except Exception:  # noqa: BLE001
            pass
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    print("\n问题：", "无" if not problems else "")
    for p in problems:
        print(" -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
