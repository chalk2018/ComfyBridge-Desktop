"""验证「视频生成 / 音频生成」的基于上一条：子模块 + 档位 + 时长/歌词 + 素材类型 + 原话。
   同时确认视频生成扩展的子功能（子模块）切换。"""
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
CDP_PORT = 9291
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_verify_refine_media"
OUT = APP / "_dev" / "shots"


def get(path: str) -> dict:
    return httpx.get(BASE + path, timeout=30).json()


def post(path: str, payload: dict | None = None) -> dict:
    r = httpx.post(BASE + path, json=payload or {}, timeout=60)
    r.raise_for_status()
    return r.json()


def state() -> dict:
    s = get("/api/state")
    return s.get("data", s)


def upload(fn: str, kind: str) -> str:
    p = APP / "data" / "uploads" / fn
    with p.open("rb") as f:
        r = httpx.post(f"{BASE}/api/uploads", params={"kind": kind},
                       files={"file": (fn, f, "application/octet-stream")}, timeout=60)
    r.raise_for_status()
    return r.json()["data"]["id"]


def cancel_active() -> None:
    for j in state().get("jobs", []):
        for r in j.get("runs", []):
            if r.get("state") in ("queued", "running", "waiting"):
                try:
                    post(f"/api/runs/{r['id']}/cancel")
                except Exception:
                    pass
    time.sleep(0.8)


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


def submit(cap: str, prompt: str, grade: str, refs: list[str], extra: dict) -> dict:
    body = {"capId": cap, "prompt": prompt, "grade": grade, "refs": refs}
    body.update(extra)
    cancel_active()
    res = post("/api/tasks", body)
    assert res.get("ok"), (cap, res)
    jid = res["data"]["job_id"]
    st = state()
    job = next(j for j in st["jobs"] if j["id"] == jid)
    if job.get("runs") and job["runs"][0].get("state") in ("queued", "running", "waiting"):
        post(f"/api/runs/{job['runs'][0]['id']}/cancel")
    print(f"  提交 {cap}: {jid} grade={job.get('grade')} rawPrompt={job.get('rawPrompt')}")
    return job


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    for _ in range(30):
        try:
            get("/api/state")
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("服务未启动")

    img = upload("ref_10751.png", "image")
    vid = upload("ref_10877.mp4", "video")
    aud = upload("ref_10878.wav", "audio")
    print("① 素材:", img, vid, aud)

    v_prompt = "测试视频基于上一条：霓虹雨夜街道慢推镜"
    v_job = submit("video.generate", v_prompt, "精细（16:9）", [img, vid, aud],
                   {"video": {"mode": "ref2v", "ratio": "16:9", "res": "768P", "steps": 8,
                              "duration": 8.5, "grid": 4, "cells": []}})
    assert v_job.get("rawPrompt") == v_prompt, v_job.get("rawPrompt")
    assert (v_job.get("video") or {}).get("mode") == "ref2v", v_job.get("video")
    assert abs((v_job.get("video") or {}).get("duration", 0) - 8.5) < 0.01, v_job.get("video")
    assert [x.get("kind") for x in v_job["refsInfo"]] == ["image", "video", "audio"], v_job["refsInfo"]
    print("   video 任务记录: PASS")

    a_prompt = "测试音频基于上一条：明亮活泼的男声播报"
    a_job = submit("audio.generate", a_prompt, "青年男声", [],
                   {"audio": {"mode": "tts", "lyrics": "", "duration": None}})
    assert a_job.get("rawPrompt") == a_prompt, a_job.get("rawPrompt")
    assert (a_job.get("audio") or {}).get("mode") == "tts", a_job.get("audio")
    print("   audio 任务记录: PASS")

    # 浏览器：先设成默认态，再分别点两条任务的「基于上一条」
    post("/api/ui", {"patch": {"route": "studio", "adminPage": "caps", "capId": "video.generate"}})
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
        assert cdp.wait("typeof UI!=='undefined' && !!S && !!S.video", 35)

        # ---- 视频生成：先把界面设成别的模式（t2v + 默认档 + 5.2s），再点基于上一条 ----
        cdp.js("(()=>{window.pull=async function(){};"
               "UI.route='studio';UI.capId='video.generate';"
               "UI.video={mode:'t2v',ratio:'16:9',res:'768P',steps:8,duration:5.2,grid:4,cells:[]};"
               "UI.params={grade:'默认'};UI.prompt='';UI.refs=[];"
               f"const j=S.jobs.find(x=>x.id==='{v_job['id']}');"
               "if(j&&j.runs[0]){j.runs[0].state='done';"
               "j.runs[0].files=[{kind:'video',path:'',deleted:false}];}"
               "render();return true})()")
        time.sleep(1.0)
        before = cdp.js("(()=>({mode:(UI.video||{}).mode,grade:UI.params.grade,"
                        "dur:(UI.video||{}).duration}))()")
        print("② 视频点击前:", before)
        clicked = cdp.js(f"(()=>{{const b=document.querySelector('[data-refine=\"{v_job['id']}\"]');"
                         "if(!b)return false;b.click();return true})()")
        assert clicked, "找不到视频任务的「基于上一条」"
        assert cdp.wait(f"typeof UI!=='undefined' && (UI.video||{{}}).mode==='ref2v'", 25)
        time.sleep(0.8)
        vi = cdp.js("(()=>({mode:(UI.video||{}).mode,grade:UI.params.grade,"
                    "dur:(UI.video||{}).duration,ratio:(UI.video||{}).ratio,"
                    "prompt:UI.prompt,refs:UI.refs.map(r=>r.kind),"
                    "onMode:[...document.querySelectorAll('[data-vmode].on')].map(x=>x.dataset.vmode),"
                    "onGrade:[...document.querySelectorAll('[data-grade].on')].map(x=>x.dataset.grade),"
                    "hint:document.querySelector('#pickHint')?.textContent||''}))()")
        print("② 视频点击后:", json.dumps(vi, ensure_ascii=False))
        assert vi["mode"] == "ref2v", vi
        assert vi["grade"] == "精细（16:9）", vi
        assert abs(float(vi["dur"]) - 8.5) < 0.01, vi
        assert "onMode" in vi and vi["onMode"] == ["ref2v"], vi
        assert vi["onGrade"] == ["精细（16:9）"], vi
        assert vi["refs"] == ["image", "video", "audio"], vi
        assert vi["prompt"] == v_prompt, vi["prompt"]
        assert "不要出现字幕" not in vi["prompt"], vi["prompt"]
        cdp.shot("refine_video_after")
        print("② 视频生成回填: PASS")

        # ---- 音频生成 ----
        post("/api/ui", {"patch": {"route": "studio", "adminPage": "caps", "capId": "audio.generate"}})
        cdp.js("(()=>{UI.route='studio';UI.capId='audio.generate';"
               "UI.audio={mode:'music',lyrics:'',duration:30};"
               "UI.params={grade:'默认'};UI.prompt='';UI.refs=[];"
               f"const j=S.jobs.find(x=>x.id==='{a_job['id']}');"
               "if(j&&j.runs[0]){j.runs[0].state='done';"
               "j.runs[0].files=[{kind:'audio',path:'',deleted:false}];}"
               "render();return true})()")
        time.sleep(1.0)
        adiag = cdp.js("(()=>({cap:UI.capId,route:UI.route,"
                       f"hasJob:S.jobs.some(j=>j.id==='{a_job['id']}'),"
                       f"jobCap:(S.jobs.find(j=>j.id==='{a_job['id']}')||{{}}).cap,"
                       "refines:[...document.querySelectorAll('[data-refine]')].map(x=>x.dataset.refine).slice(0,20)}))()")
        print("③ 音频诊断:", json.dumps(adiag, ensure_ascii=False))
        clicked = cdp.js(f"(()=>{{const b=document.querySelector('[data-refine=\"{a_job['id']}\"]');"
                         "if(!b)return false;b.click();return true})()")
        assert clicked, "找不到音频任务的「基于上一条」"
        assert cdp.wait("typeof UI!=='undefined' && (UI.audio||{}).mode==='tts'", 25)
        time.sleep(0.8)
        ai = cdp.js("(()=>({mode:(UI.audio||{}).mode,grade:UI.params.grade,"
                    "prompt:UI.prompt,refs:UI.refs.map(r=>r.kind),"
                    "onMode:[...document.querySelectorAll('[data-amode].on')].map(x=>x.dataset.amode),"
                    "onGrade:[...document.querySelectorAll('[data-grade].on')].map(x=>x.dataset.grade)}))()")
        print("③ 音频点击后:", json.dumps(ai, ensure_ascii=False))
        assert ai["mode"] == "tts", ai
        assert ai["grade"] == "青年男声", ai
        assert ai["onMode"] == ["tts"], ai
        assert ai["onGrade"] == ["青年男声"], ai
        assert ai["prompt"] == a_prompt, ai["prompt"]
        assert ai["refs"] == [], ai
        cdp.shot("refine_audio_after")
        print("③ 音频生成回填: PASS")

        # ---- 视频生成扩展：子功能切换（设成错误 id，点基于上一条应切回正确 func）----
        ve_prompt = "测试扩展子功能切换：极速"
        ve_job = submit("video.ext", ve_prompt, "4采480P", [],
                        {"videoExt": {"func": "multiRefFast", "ratio": "16:9", "mode": "fl2v", "duration": 5.5}})
        post("/api/ui", {"patch": {"route": "studio", "adminPage": "caps", "capId": "video.ext"}})
        # window.pull 已被置空，reload 让真实轮询恢复，新任务才会进 S.jobs 与结果卡 DOM。
        cdp.call("Page.reload", {"ignoreCache": True})
        assert cdp.wait("typeof UI!=='undefined' && !!S && !!S.videoExt && "
                        f"S.jobs.some(j=>j.id==='{ve_job['id']}')", 35)
        cdp.js("(()=>{UI.route='studio';UI.capId='video.ext';"
               "UI.veFunc={id:'__nonexistent__',grade:'20采768P',ratio:'9:16',mode:'ref2v',duration:5.2};"
               "UI.params={grade:'默认'};UI.prompt='';UI.refs=[];"
               f"const j=S.jobs.find(x=>x.id==='{ve_job['id']}');"
               "if(j&&j.runs[0]){j.runs[0].state='done';"
               "j.runs[0].files=[{kind:'video',path:'',deleted:false}];}"
               "render();return true})()")
        time.sleep(1.0)
        # 先确认 veFunc() 对错误 id 会回退，但基于上一条必须用服务端 func 覆盖
        ved = cdp.js("(()=>({cap:UI.capId,"
                     f"hasJob:S.jobs.some(j=>j.id==='{ve_job['id']}'),"
                     "jobs:S.jobs.length,"
                     "refines:[...document.querySelectorAll('[data-refine]')].map(x=>x.dataset.refine).slice(0,20)}))()")
        print("④ 扩展诊断:", json.dumps(ved, ensure_ascii=False))
        clicked = cdp.js(f"(()=>{{const b=document.querySelector('[data-refine=\"{ve_job['id']}\"]');"
                         "if(!b)return false;b.click();return true})()")
        assert clicked, "找不到扩展任务的「基于上一条」"
        assert cdp.wait(f"typeof UI!=='undefined' && (UI.veFunc||{{}}).grade==='4采480P'", 25)
        time.sleep(0.8)
        ve = cdp.js("(()=>({ve:{...UI.veFunc},"
                    "onFunc:[...document.querySelectorAll('[data-vefunc].on')].map(x=>x.dataset.vefunc),"
                    "prompt:UI.prompt}))()")
        print("④ 扩展点击后:", json.dumps(ve, ensure_ascii=False))
        assert ve["ve"]["id"] == "multiRefFast", ve
        assert ve["ve"]["grade"] == "4采480P", ve
        assert ve["ve"]["duration"] == 5.5, ve
        assert ve["onFunc"] == ["multiRefFast"], ve
        assert ve["prompt"] == ve_prompt, ve["prompt"]
        print("④ 扩展子功能切换: PASS")

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
