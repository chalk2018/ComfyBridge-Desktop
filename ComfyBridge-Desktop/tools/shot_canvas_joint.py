"""画布联合测试：图片生成节点 → 连线 → 视频生成节点 → 界面点生成。

用已有的一次出图任务当「图片生成节点」的成品，省掉等待；
重点验证 1) 连线上游成品能进缩略图、2) @ 插入 [图 1]、
3) 视频节点提交时把上游成品带成参考、4) 成品视频回到节点里。

用法：
    .venv\\Scripts\\python.exe tools/shot_canvas_joint.py [图片job_id]
产物：_reference/cj_*.png
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shot_canvas_gen import BASE, CDP_PORT, EDGE, OUT, C, center, get  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / ".cache" / "edge_canvas_joint"


def api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode() if method != "GET" else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    img_job = sys.argv[1] if len(sys.argv) > 1 else "job_5920"
    OUT.mkdir(exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)

    # 先按接口造一块画布：上游图片生成节点已经出过图，下游视频节点还没跑
    made = api("/api/canvas/project", "POST", {"name": "1.04 联合测试"}).get("data") or {}
    pid = made.get("id")
    if not pid:
        print("建画布失败", made)
        return 1
    project = {
        "name": "1.04 联合测试",
        "nodes": [
            {"id": "nA", "kind": "imagegen", "x": 60, "y": 120, "w": 300,
             "title": "图片生成", "prompt": "宋代文士独立江边，远山如黛，水墨质感",
             "grade": "", "refId": "", "jobId": img_job, "video": None},
            {"id": "nB", "kind": "videogen", "x": 460, "y": 120, "w": 300,
             "title": "视频生成", "prompt": "", "grade": "", "refId": "", "jobId": "",
             "video": {"mode": "ref2v", "ratio": "16:9", "res": "480P", "steps": 4}},
        ],
        "edges": [{"from": "nA", "to": "nB"}],
        "view": {"x": 40, "y": 30, "k": 1},
    }
    api(f"/api/canvas/project/{pid}", "PUT", project)

    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.4",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1000", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
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
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    problems: list[str] = []
    try:
        cdp.call("Page.navigate", {"url": BASE + "/#canvas"})
        time.sleep(2.5)
        cdp.js("localStorage.setItem('cb.canvas.pid','%s')" % pid)
        cdp.call("Page.navigate", {"url": BASE + "/#canvas"})
        time.sleep(3.5)
        if not cdp.wait("!!document.querySelector('.cnode[data-id=nB]')", 40):
            problems.append("画布没加载出两个节点")
        time.sleep(1.2)
        cdp.shot("a_loaded")

        # 1) 上游成品的缩略图应该出现在视频节点的 chips 里
        chip = cdp.js("(()=>{const im=document.querySelector('.cnode[data-id=nB] .nref img');"
                      "return im?[im.src.slice(-46), im.naturalWidth, im.naturalHeight]:null})()")
        print("上游缩略图 =", chip)
        if not chip or not chip[1]:
            problems.append("视频节点没显示上游成品的缩略图")

        # 2) 点 @ 应该把 [图 1] 插到提示词里
        at = center(cdp, '.cnode[data-id="nB"] .nref .at')
        if not at:
            problems.append("找不到 @ 按钮")
        else:
            cdp.click_at(at[0], at[1])
            time.sleep(0.5)
            val = cdp.js('(document.querySelector(\'.cnode[data-id="nB"] textarea[data-prompt]\')||{}).value||""')
            print("插入后提示词 =", repr(val))
            if "[图 1]" not in val:
                problems.append("@ 没有把 [图 1] 插进提示词")
            cdp.shot("b_token")

        # 3) 补一句运镜，点生成
        ta = center(cdp, '.cnode[data-id="nB"] textarea[data-prompt]')
        if ta:
            cdp.click_at(ta[0], ta[1])
            cdp.call("Input.insertText", {"text": "镜头极缓推近，江面薄雾流动，衣袂轻摆"})
            time.sleep(0.4)
        jobs_before = {j["id"] for j in (get("/api/state").get("jobs") or [])}
        btn = center(cdp, '.cnode[data-id="nB"] [data-gen]')
        if not btn:
            problems.append("视频节点上没有生成按钮")
        else:
            cdp.click_at(btn[0], btn[1])
            print("已点生成，等视频…")
            time.sleep(2.0)
            cdp.shot("c_running")
            run = None
            end = time.time() + 600
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
                problems.append("等了 10 分钟没等到视频任务收尾")
            else:
                print("视频任务 =", run.get("state"), run.get("size"), run.get("duration"),
                      [(f.get("path")) for f in (run.get("files") or [])])
                if run.get("state") != "done":
                    problems.append("视频任务失败：" + str((run.get("err") or {}).get("msg")))
                # 4) 视频播放器要回到节点里
                ok = cdp.wait("!!document.querySelector('.cnode[data-id=nB] video')", 120)
                if not ok:
                    problems.append("节点里没出现视频播放器")
                time.sleep(1.0)
                dinfo = cdp.js("(()=>{const v=document.querySelector('.cnode[data-id=nB] video');"
                               "return v?[v.src.slice(-40), v.duration, v.videoWidth, v.videoHeight]:null})()")
                print("节点里的播放器 =", dinfo)
                if not dinfo or not dinfo[2]:
                    problems.append("播放器没拿到视频尺寸")
                cdp.shot("d_done")
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
