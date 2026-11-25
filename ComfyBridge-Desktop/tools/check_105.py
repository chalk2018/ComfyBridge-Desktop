"""1.05 迭代：阶段性前端验收截图 + 控制台错误收集。

用法： .venv\\Scripts\\python.exe tools\\check_105.py
产物： _reference/105_*.png
"""
from __future__ import annotations
import base64, json, subprocess, time, urllib.request
from pathlib import Path
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9351
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_check_105"
OUT = APP / "_reference"

class C:
    def __init__(self, url): self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True); self.n=0
    def call(self, m, p=None):
        self.n+=1; mid=self.n; self.ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
        while True:
            msg=json.loads(self.ws.recv())
            if msg.get("id")==mid:
                if "error" in msg: raise RuntimeError(str(msg["error"]))
                return msg.get("result",{})
    def js(self, e):
        r=self.call("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True})
        if "exceptionDetails" in r: raise RuntimeError(json.dumps(r["exceptionDetails"],ensure_ascii=False)[:800])
        return r.get("result",{}).get("value")
    def wait(self, e, t=30):
        end=time.time()+t
        while time.time()<end:
            try:
                if self.js(e): return True
            except Exception: pass
            time.sleep(0.3)
        return False
    def shot(self, name):
        d=self.call("Page.captureScreenshot",{"format":"png","captureBeyondViewport":False})
        p=OUT/f"105_{name}.png"; p.write_bytes(base64.b64decode(d["data"])); print("saved",p.name)

def size(c,w,h):
    c.call("Emulation.setDeviceMetricsOverride",{"width":w,"height":h,"deviceScaleFactor":1,"mobile":False}); time.sleep(0.8)

def main():
    OUT.mkdir(exist_ok=True); PROFILE.mkdir(parents=True,exist_ok=True)
    proc=subprocess.Popen([EDGE,"--headless=new","--disable-gpu","--no-sandbox","--disable-extensions",
        "--no-first-run","--hide-scrollbars","--force-device-scale-factor=1.5","--remote-allow-origins=*",
        f"--remote-debugging-port={CDP_PORT}",f"--user-data-dir={PROFILE}","--window-size=1440,1060",BASE+"/#studio"],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    ws=None
    for _ in range(60):
        time.sleep(0.4)
        try:
            tabs=json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list",timeout=2).read())
            for t in tabs:
                if t.get("type")=="page" and "8190" in t.get("url",""):
                    ws=t["webSocketDebuggerUrl"]; break
            if ws: break
        except Exception: continue
    assert ws,"no CDP"
    c=C(ws); c.call("Runtime.enable"); c.call("Page.enable"); c.call("Log.enable")
    errs=[]
    size(c,1440,1060)
    try:
        c.wait("typeof UI!=='undefined' && !!S",40); time.sleep(1)
        # 每秒轮询会从服务端草稿恢复 capId；测试里切页必须写回草稿，否则点按钮时早被拽回旧能力
        c.js("(async()=>{UI.route='studio';UI.capId='image.generate';UI._userSwitched=true;UI.mode='t2i';UI.prompt='江边古城，暖色调';UI.railMin=false;UI.drawerMin=false;await saveDraft({capId:UI.capId});render();return true})()")
        time.sleep(1); c.shot("01_image_layout")
        # 设定图模板弹窗（需求2）
        print("image template modal", c.js("(()=>{const b=document.querySelector('[data-sheet-template]');if(b)b.click();return document.querySelectorAll('.modal').length})()"))
        time.sleep(0.5); c.shot("02_image_template")
        c.js("(()=>{const b=document.querySelector('[data-close]');if(b)b.click();return true})()")
        # 看完整提示词（需求1）
        print("image preview", c.js("(async()=>{const b=document.querySelector('#imgPvBtn');if(b)b.click();await new Promise(r=>setTimeout(r,1200));return (document.querySelector('#imgPv')||{}).textContent||'none'})()"))
        c.shot("02b_image_preview")
        c.js("(()=>{const b=document.querySelector('#imgPvBtn');if(b)b.click();return true})()")
        # 视频创作台（需求5/6）
        c.js("(async()=>{UI.capId='video.generate';UI._userSwitched=true;UI.video={mode:'t2v',duration:5.2};UI.prompt='生成一段带环境声的短视频';await saveDraft({capId:UI.capId});render();return true})()")
        time.sleep(1); c.shot("03_video_composer")
        # 时长同步测试（需求6）
        print("duration sync 5.23 ->", c.js("(()=>{const n=document.querySelector('#vDurNum'),r=document.querySelector('#vDurRange');if(!n||!r)return 'none';n.value='5.23';n.dispatchEvent(new Event('input',{bubbles:true}));return 'num='+n.value+' range='+r.value+' draft='+UI.video.duration})()"))
        print("duration sync 7.86 ->", c.js("(async()=>{const n=document.querySelector('#vDurNum'),r=document.querySelector('#vDurRange');if(!n||!r)return 'none';r.value='7.8';r.dispatchEvent(new Event('input',{bubbles:true}));await new Promise(x=>setTimeout(x,700));return 'num='+n.value+' range='+r.value+' draft='+UI.video.duration})()"))
        time.sleep(0.5); c.shot("04_video_dur_sync")
        print("video tile zoom", c.js("(()=>{const b=document.querySelector('.tile [data-zoom]');if(!b)return 'no-tile';b.click();const v=document.querySelector('.modal video');return v?'video-player':'no-player'})()"))
        c.js("(()=>{const b=document.querySelector('[data-close]');if(b)b.click();return true})()")
        # 管理台-视频档位表（需求3/4）
        c.js("(async()=>{UI.route='admin';UI.adminPage='grades';UI.capId='video.generate';UI._userSwitched=true;UI.editingWfIndex=0;VIDEO_GRADE_CACHE=null;await saveDraft({route:'admin',adminPage:'grades',capId:UI.capId});render();return true})()")
        time.sleep(1.5); c.shot("05_video_grades")
        # 音频合并页（需求16）
        c.js("(async()=>{UI.route='studio';UI.capId='audio.generate';UI._userSwitched=true;UI.audio={mode:'music'};await saveDraft({capId:UI.capId});render();return true})()")
        time.sleep(1); c.shot("06_audio")
        # 需求14：跨能力定位（在音频页点图片任务的定位按钮，应该切回图片能力并高亮卡片）
        print("cross-cap jump", c.js("(async()=>{const rows=[...document.querySelectorAll('[data-jump]')];const b=rows.find(x=>{const j=(S.jobs||[]).find(y=>y.id===x.dataset.jump);return j&&j.cap==='image.generate'});if(!b)return 'no-image-job';const id=b.dataset.jump;b.click();await new Promise(r=>setTimeout(r,800));const card=document.getElementById(id);return 'cap='+UI.capId+' card='+(!!card)+' flash='+document.querySelectorAll('.jump-flash').length})()"))
        c.js("(async()=>{UI.capId='audio.generate';UI._userSwitched=true;await saveDraft({capId:UI.capId});render();return 1})()")
        # 管理台音频四合一档位表（需求16）
        c.js("(async()=>{UI.route='admin';UI.adminPage='grades';UI.capId='audio.generate';UI._userSwitched=true;UI.editingWfIndex=0;AUDIO_GRADE_CACHE=null;await saveDraft({route:'admin',adminPage:'grades',capId:UI.capId});render();return true})()")
        time.sleep(1.3); c.shot("06b_audio_grades")
        print("audio tabs", c.js("document.querySelectorAll('[data-wfswitch]').length"))
        # 画布（需求8-12）
        c.js("(()=>{UI.route='canvas';render();return true})()")
        time.sleep(1.5); c.shot("07_canvas")
        print("canvas zoom modal", c.js("(()=>{const b=document.querySelector('[data-zoomrun]');if(!b)return 'no-btn';b.click();return document.querySelectorAll('.modal video,.modal img').length})()"))
        time.sleep(0.4); c.shot("07b_canvas_zoom")
        c.js("(()=>{const b=document.querySelector('[data-close]');if(b)b.click();return true})()")
        # 画布管理页（需求13）
        c.js("(()=>{UI.route='admin';UI.adminPage='canvas';render();return true})()")
        time.sleep(2); c.shot("08_canvas_admin")
        # 手机端画布（需求1 移动端 + 布局）
        c.js("(async()=>{UI.route='studio';UI.capId='image.generate';UI._userSwitched=true;await saveDraft({capId:UI.capId});render();return true})()")
        size(c,390,780); time.sleep(0.8); c.shot("09_mobile")
        # 校验 console
        logs=c.call("Log.enable") or {}
    finally:
        proc.terminate()
    print("DONE")

if __name__ == "__main__": main()
