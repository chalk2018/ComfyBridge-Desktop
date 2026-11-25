"""验证「基于上一条」对视频生成扩展的回填：档位/比例/模式/时长 + 素材类型 + 提示词原话。"""
from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9287
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_verify_refine"
OUT = APP / "_dev" / "shots"


def get(path: str) -> dict:
    return httpx.get(BASE + path, timeout=30).json()


def post(path: str, payload: dict | None = None) -> dict:
    r = httpx.post(BASE + path, json=payload or {}, timeout=60)
    r.raise_for_status()
    return r.json()


class C:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
        self.n = 0

    def call(self, m: str, p: dict | None = None) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result", {})

    def js(self, e: str):
        r = self.call("Runtime.evaluate", {"expression": e, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:800])
        return r.get("result", {}).get("value")

    def wait(self, e: str, t: float = 30.0) -> bool:
        end = time.time() + t
        while time.time() < end:
            try:
                if self.js(e):
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def shot(self, name: str) -> None:
        data = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        path = OUT / f"{name}.png"
        path.write_bytes(base64.b64decode(data["data"]))
        print("saved", path)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)

    # 等服务就绪
    for _ in range(30):
        try:
            get("/api/state")
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("服务未启动")

    # ① 老任务（用户截图那条）：验证按扩展名恢复图/视频/音频类型
    old = post("/api/tasks/job_10892/refs")
    old_refs = ((old.get("data") or {}).get("refs") or [])
    old_kinds = [r.get("kind") for r in old_refs]
    print("① 老任务 /refs kind:", old_kinds)
    assert "video" in old_kinds and "image" in old_kinds, old_refs
    print("   老任务素材类型恢复: PASS")

    # ② 新提交：验证 rawPrompt 与 refsInfo.kind 落盘
    by_kind = {k: next(r["id"] for r in old_refs if r.get("kind") == k)
               for k in ("image", "video", "audio")}
    prompt = "测试基于上一条参数带回：赛博都市夜景"
    body = {
        "capId": "video.ext", "prompt": prompt, "grade": "8采480P",
        "refs": [by_kind["image"], by_kind["video"], by_kind["audio"]],
        "videoExt": {"func": "multiRefFast", "ratio": "9:16", "mode": "ref2v", "duration": 6.5},
    }
    raw_state = get("/api/state")
    state = raw_state.get("data", raw_state)
    # 先清掉上一次验证残留的活跃任务，否则服务会按「还没跑完」拒收新提交。
    for j in state.get("jobs", []):
        if j.get("cap") != "video.ext":
            continue
        for r in j.get("runs", []):
            if r.get("state") in ("queued", "running", "waiting"):
                try:
                    post(f"/api/runs/{r['id']}/cancel")
                except Exception:
                    pass
    time.sleep(1.0)
    # job.prompt 是 compose 后的成片提示词；原话存在 rawPrompt。
    job = next((j for j in reversed(state.get("jobs", []))
                if j.get("cap") == "video.ext" and j.get("rawPrompt") == prompt), None)
    if not job:
        res = post("/api/tasks", body)
        assert res.get("ok"), res
        job_id = res["data"]["job_id"]
        state = get("/api/state")
        state = state.get("data", state)
        job = next(j for j in state["jobs"] if j["id"] == job_id)
    job_id = job["id"]
    print("② 任务:", job_id, "videoExt:", json.dumps(job.get("videoExt"), ensure_ascii=False))
    print("   rawPrompt:", job.get("rawPrompt"))
    print("   refsInfo:", json.dumps(job.get("refsInfo"), ensure_ascii=False))
    assert job.get("rawPrompt") == prompt
    assert [x.get("kind") for x in job["refsInfo"]] == ["image", "video", "audio"]
    # 取消这次真实出片，避免占用 ComfyUI；任务记录还在，refine 照常可测
    if job.get("runs") and job["runs"][0].get("state") in ("queued", "running", "waiting"):
        post(f"/api/runs/{job['runs'][0]['id']}/cancel")
    print("   任务记录校验: PASS（run 已取消）")

    # ③ 浏览器：把页面先设成默认值，再点这条任务的「基于上一条」
    post("/api/ui", {"patch": {"route": "studio", "adminPage": "caps", "capId": "video.ext"}})
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-extensions",
        "--no-first-run", "--hide-scrollbars", "--force-device-scale-factor=1.5",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,1060", BASE + "/#studio",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2).read())
            for t in tabs:
                if t.get("type") == "page" and "8190" in t.get("url", ""):
                    ws_url = t["webSocketDebuggerUrl"]
                    break
            if ws_url:
                break
        except Exception:
            continue
    assert ws_url, "连不上 CDP"
    cdp = C(ws_url)
    cdp.call("Runtime.enable")
    cdp.call("Page.enable")
    cdp.call("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 1060, "deviceScaleFactor": 1, "mobile": False})
    try:
        assert cdp.wait("typeof UI!=='undefined' && !!S && !!S.videoExt && S.videoExt.funcs.length", 35)
        # 模拟用户截图里的默认态
        cdp.js("(()=>{UI.route='studio';UI.capId='video.ext';"
               "UI.veFunc={id:'multiRefFast',grade:'20采768P',ratio:'16:9',mode:'fl2v',duration:5.2};"
               "UI.prompt='';UI.refs=[];render();return true})()")
        # 验证用的 run 已取消（结果卡不渲染「基于上一条」）；
        # 只在浏览器内存里把它临时当成已完成，停掉轮询避免被服务端快照覆盖。
        cdp.js("(()=>{window.pull=async function(){};"
               f"const j=S.jobs.find(x=>x.id==='{job_id}');"
               "if(j&&j.runs[0]){j.runs[0].state='done';"
               "j.runs[0].files=[{kind:'video',path:'',deleted:false}];}"
               "render();return true})()")
        time.sleep(1.2)
        before = cdp.js("(()=>({grade:(UI.veFunc||{}).grade,ratio:(UI.veFunc||{}).ratio,"
                        "mode:(UI.veFunc||{}).mode,duration:(UI.veFunc||{}).duration,"
                        "refs:UI.refs.length}))()")
        print("③ 点击前:", json.dumps(before, ensure_ascii=False))
        diag = cdp.js("(()=>({route:UI.route,cap:UI.capId,jobs:S.jobs.length,"
                      f"refines:[...document.querySelectorAll('[data-refine]')].map(x=>x.dataset.refine).slice(0,30),"
                      f"hasJob:S.jobs.some(j=>j.id==='{job_id}'),"
                      f"jobState:(S.jobs.find(j=>j.id==='{job_id}')||{{}}).runs?.map(r=>r.state)}}))()")
        print("③ 诊断:", json.dumps(diag, ensure_ascii=False))
        clicked = cdp.js(f"(()=>{{const b=document.querySelector('[data-refine=\"{job_id}\"]');"
                         "if(!b)return false;b.click();return true})()")
        assert clicked, "页面上找不到这条任务的「基于上一条」按钮"
        assert cdp.wait("typeof UI!=='undefined' && (UI.veFunc||{}).ratio==='9:16' "
                        "&& (UI.veFunc||{}).mode==='ref2v'", 25)
        time.sleep(1.0)
        info = cdp.js("(()=>({ve:{...UI.veFunc},"
                      "refs:UI.refs.map(r=>({id:r.id,kind:r.kind,name:r.name})),"
                      "prompt:UI.prompt,meta:document.querySelector('.vdur-meta')?.textContent||'',"
                      "hint:document.querySelector('#pickHint')?.textContent||'',"
                      "slots:[...document.querySelectorAll('.vslot .vslabel')].map(x=>x.textContent).join('|')}))()")
        print("③ 点击后:", json.dumps(info, ensure_ascii=False, indent=2))
        ve = info["ve"]
        assert ve["grade"] == "8采480P", ve
        assert ve["ratio"] == "9:16", ve
        assert ve["mode"] == "ref2v", ve
        assert abs(float(ve["duration"]) - 6.5) < 0.01, ve
        assert [r["kind"] for r in info["refs"]] == ["image", "video", "audio"], info["refs"]
        assert info["prompt"] == prompt, info["prompt"]
        assert "画面里不要出现字幕" not in info["prompt"], info["prompt"]
        cdp.shot("refine_ve_after")
        print("③ 基于上一条 UI 回填: PASS")
        print("VERIFY PASS")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
