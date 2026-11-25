"""验证「生成时结果区自动滚到最新一块」：先滚到顶，再提交一条，看是否跟到最新卡。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_canvas_gen import BASE, CDP_PORT, EDGE, C, center, get  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / ".cache" / "edge_autoscroll"


def main() -> int:
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.4",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1180,680", "about:blank",
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
             {"width": 1180, "height": 680, "deviceScaleFactor": 1, "mobile": False})
    problems: list[str] = []
    try:
        cdp.call("Page.navigate", {"url": BASE + "/#studio"})
        time.sleep(4.5)
        cdp.js("(()=>{const b=document.querySelector('[data-cap=\"audio.tts\"]');if(b)b.click();return !!b})()")
        time.sleep(2.0)
        # 滚到最上面
        cdp.js("(()=>{const c=document.querySelector('#chatScroll');if(c)c.scrollTop=0;return 1})()")
        time.sleep(0.5)
        print("内容高/可视高 =", cdp.js("(()=>{const c=document.querySelector('#chatScroll');"
                                        "return c?[Math.round(c.scrollHeight),Math.round(c.clientHeight)]:null})()"))
        before = cdp.js("(()=>{const c=document.querySelector('#chatScroll');"
                        "return c?[Math.round(c.scrollTop),Math.round(c.scrollHeight),Math.round(c.clientHeight)]:null})()")
        print("提交前 滚动 =", before)

        jobs_before = {j["id"] for j in (get("/api/state").get("jobs") or [])}
        box = center(cdp, "#promptInput")
        cdp.click_at(box[0], box[1])
        cdp.call("Input.insertText", {"text": "莫听穿林打叶声，何妨吟啸且徐行"})
        time.sleep(0.4)
        cdp.click_at(*center(cdp, "#sendBtn"))
        print("已提交，等新卡出现…")
        end = time.time() + 300
        new_id = None
        while time.time() < end:
            for j in (get("/api/state").get("jobs") or []):
                if j["id"] not in jobs_before:
                    new_id = j["id"]
                    break
            if new_id:
                break
            time.sleep(2)
        if not new_id:
            problems.append("没提交出新任务")
        else:
            time.sleep(3.0)
            after = cdp.js("(()=>{const c=document.querySelector('#chatScroll');"
                           "return c?[Math.round(c.scrollTop),Math.round(c.scrollHeight),Math.round(c.clientHeight)]:null})()")
            print("新卡出现后 滚动 =", after)
            # 到底部才算跟到最新（留 24px 容差）
            if after[0] + after[2] < after[1] - 24:
                problems.append(f"没滚到最新：scrollTop={after[0]}，可视高={after[2]}，内容高={after[1]}")
            else:
                print("已在底部，跟随成功")
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
