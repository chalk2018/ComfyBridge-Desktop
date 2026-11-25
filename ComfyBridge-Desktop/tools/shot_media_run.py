"""创作台真实点击：视频生成（文生视频）与音频生成（音乐）各跑一条。

用法：
    .venv\\Scripts\\python.exe tools/shot_media_run.py vid
    .venv\\Scripts\\python.exe tools/shot_media_run.py aud
产物：_reference/mr_*.png
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_canvas_gen import BASE, CDP_PORT, EDGE, OUT, C, center, get  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / ".cache" / "edge_media_run"


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "vid"
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

    def shot(name: str) -> None:
        data = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (OUT / f"mr_{name}.png").write_bytes(base64.b64decode(data["data"]))
        print("saved", f"mr_{name}.png")

    try:
        cdp.call("Page.navigate", {"url": BASE + "/#studio"})
        time.sleep(4.5)
        cap = "video.generate" if which == "vid" else "audio.music"
        cdp.js("(()=>{const b=document.querySelector('[data-cap=\"%s\"]');if(b)b.click();return !!b})()" % cap)
        time.sleep(2.0)
        if not cdp.wait("!!document.querySelector('#promptInput')", 25):
            problems.append("创作台没渲染出输入框")
        time.sleep(0.6)

        if which == "vid":
            cdp.js("(()=>{const b=document.querySelector('[data-vmode=t2v]');if(b)b.click();return !!b})()")
            time.sleep(0.6)
            # 480P + 4 步：快一点，验证的是链路不是画质
            cdp.js("(()=>{const pick=(k,v)=>{const b=[...document.querySelectorAll('[data-vopt='+k+']')]"
                   ".find(x=>String(x.dataset.vval)===String(v));if(b)b.click();};"
                   "pick('res','480P');pick('steps',4);return 1})()")
            time.sleep(0.5)
            prompt = "夜色书斋，一盏油灯，窗外竹影摇动，镜头极缓慢推近，宋代文士伏案"
            wait_s = 900
        else:
            cdp.js("(()=>{const b=document.querySelector('[data-amode=music]');if(b)b.click();return !!b})()")
            time.sleep(0.6)
            prompt = "古风民乐，古琴与箫，缓慢悠远，适合江边独行"
            wait_s = 600
        cur = cdp.js("(()=>{const f=document.querySelector('[data-vmode].on,[data-amode].on');"
                     "return f?f.textContent.trim():null})()")
        print("当前模式 =", cur, "| 尺寸/步数 =",
              cdp.js("(()=>[...document.querySelectorAll('[data-vopt]')].filter(b=>b.classList.contains('on'))"
                     ".map(b=>b.dataset.vopt+'='+b.dataset.vval).join(','))()"))
        shot("a_before")

        box = center(cdp, "#promptInput")
        if not box:
            problems.append("点不到输入框")
        else:
            cdp.click_at(box[0], box[1])
            time.sleep(0.2)
            cdp.call("Input.insertText", {"text": prompt})
            time.sleep(0.4)
            print("输入框文字 =", cdp.js("(document.querySelector('#promptInput')||{}).innerText||''")[:40])

        jobs_before = {j["id"] for j in (get("/api/state").get("jobs") or [])}
        btn = center(cdp, "#sendBtn")
        if not btn:
            problems.append("找不到发送按钮")
        else:
            cdp.click_at(btn[0], btn[1])
            print("已点发送，等成品…")
            time.sleep(2.0)
            shot("b_running")
            run = None
            end = time.time() + wait_s
            while time.time() < end:
                for j in (get("/api/state").get("jobs") or []):
                    if j["id"] in jobs_before or not j.get("runs"):
                        continue
                    r = j["runs"][-1]
                    if r.get("state") in ("done", "failed"):
                        run = r
                        break
                if run:
                    break
                time.sleep(3)
            if not run:
                problems.append("等太久没等到任务收尾")
            else:
                print("任务 =", run.get("state"), run.get("wf"), run.get("size"), run.get("duration"),
                      [(f.get("path")) for f in (run.get("files") or [])])
                if run.get("state") != "done":
                    problems.append("任务失败：" + str((run.get("err") or {}).get("msg")))
                sel = "video" if which == "vid" else "audio"
                ok = cdp.wait("!!document.querySelector('.msg .rwrap %s, .msg .media-tile %s')" % (sel, sel), 60)
                if not ok:
                    # 结果卡结构可能不同，退一步：只要页面上出现成品地址就算过
                    has = cdp.js("!!document.querySelector('[src*=\"/api/media?run=\"]')")
                    if not has:
                        problems.append("结果卡里没出现成品播放器")
                time.sleep(1.0)
                shot("c_done")
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
