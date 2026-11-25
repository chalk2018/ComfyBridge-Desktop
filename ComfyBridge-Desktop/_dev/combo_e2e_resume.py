"""续跑：等待已提交的多参考长任务完成，并继续剩余组合 + 队列测试。

第 5 组（8采768P/mixed/9 个素材）真实耗时远超 10 分钟，原脚本 wait_job 超时太短。
这里把等待上限放宽到 70 分钟，并从第 6 组继续；最后两组混合参考用 3图+1视频+1音频
（上限组合已在第 5 组验证过），避免重复超长等待。
"""
from __future__ import annotations

import time

import httpx

BASE = "http://127.0.0.1:8190"
CLIENT = httpx.Client(base_url=BASE, timeout=60.0)
MODE_NAME = {"fl2v": "首尾帧生成", "ref2v": "参考生成"}
JOB5 = "job_10868"

CASES = [
    (5, "8采768P", "9:16", "fl2v", "one_video",  "参考视频续写的海边日落"),
    (6, "20采480P", "16:9", "fl2v", "one_audio", "听声音画出的舞台灯光"),
    (7, "20采480P", "9:16", "ref2v", "one_image", "水墨风的山水长卷"),
    (8, "20采768P", "16:9", "ref2v", "none",      "星空下的雪山延时"),
    (9, "20采768P", "9:16", "fl2v", "mixed_light", "多素材参考的赛博都市"),
]
EXPECT_REF = {
    "none": "0 图 / 0 视频 / 0 音频", "one_image": "1 图 / 0 视频 / 0 音频",
    "one_video": "0 图 / 1 视频 / 0 音频", "one_audio": "0 图 / 0 视频 / 1 音频",
    "mixed": "6 图 / 1 视频 / 2 音频", "mixed_light": "3 图 / 1 视频 / 1 音频",
}


def get(p): return CLIENT.get(p).json()
def post(p, b=None): return CLIENT.post(p, json=b or {}).json()


def wait_job(jid, runs, timeout=4200):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        st = get("/api/state")
        job = next((j for j in st.get("jobs", []) if j["id"] == jid), None)
        if job and len(job.get("runs", [])) >= runs:
            r = job["runs"][runs - 1]
            if r.get("pct") != last and r.get("state") == "running":
                print(f"  ... {jid} run={r['id']} pct={r.get('pct')} step={r.get('step')}/{r.get('total')}", flush=True)
                last = r.get("pct")
            if r.get("state") in ("done", "error", "cancelled"):
                return job
        time.sleep(5)
    return None


def wait_idle(timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        st = get("/api/state")
        if not any(r["state"] in ("queued", "running", "waiting")
                   for j in st.get("jobs", []) for r in j.get("runs", [])):
            return True
        time.sleep(3)
    return False


def upload(path):
    with open(path, "rb") as fh:
        r = CLIENT.post("/api/uploads", files={"file": (path.split("\\")[-1], fh)}).json()
    rid = (r.get("data") or {}).get("id", "")
    assert rid, r
    return rid


def prepare():
    ups = "D:/workspace/WorkBuddyProject/ComfyBridge-Desktop/data/uploads"
    import glob
    pngs = sorted(glob.glob(ups + "/ref_*.png"))[:6]
    mp4 = sorted(glob.glob(ups + "/ref_*.mp4"))[:1]
    wav = "D:/workspace/WorkBuddyProject/ComfyBridge-Desktop/_dev/test_tone.wav"
    assert len(pngs) == 6 and mp4, "素材不足"
    return {"images": [upload(p) for p in pngs],
            "videos": [upload(mp4[0])],
            "audios": [upload(wav), upload(wav)]}


def refs_for(scene, pool):
    if scene == "none": return []
    if scene == "one_image": return pool["images"][:1]
    if scene == "one_video": return pool["videos"][:1]
    if scene == "one_audio": return pool["audios"][:1]
    if scene == "mixed_light": return pool["images"][:3] + pool["videos"] + pool["audios"][:1]
    return pool["images"] + pool["videos"] + pool["audios"]


def verify(job, idx, grade, ratio, mode, scene, label, check_refs=True):
    run = job["runs"][idx]
    assert run["state"] == "done", f"{label} 状态={run['state']} err={run.get('error')}"
    ext = job.get("videoExt") or {}
    assert ext.get("mode") == mode, f"{label} 模式={ext.get('mode')}"
    ap = run.get("applied") or {}
    assert ap.get("档位") == grade, f"{label} 档位={ap.get('档位')}"
    assert ap.get("参考模式") == MODE_NAME[mode], f"{label} applied模式={ap.get('参考模式')}"
    assert ratio in ap.get("宽高比", ""), f"{label} 宽高比={ap.get('宽高比')}"
    if check_refs:
        assert ap.get("参考") == EXPECT_REF[scene], f"{label} 参考={ap.get('参考')} 期望={EXPECT_REF[scene]}"
    assert run.get("files"), f"{label} 没有成品"
    return run


def main():
    results = []
    # 第 5 组：等已有长任务
    print("等待第 5 组长任务…", flush=True)
    job = wait_job(JOB5, 1)
    assert job, "第 5 组仍超时"
    r = verify(job, 0, "8采768P", "16:9", "ref2v", "mixed", "#5 8采768P/16:9/ref2v/mixed")
    results.append(("#5", r["id"]))
    print(f"PASS #5 8采768P/16:9/ref2v/mixed run={r['id']} files={len(r['files'])}", flush=True)

    pool = prepare()
    jobs = [{"jid": JOB5, "ratio": "16:9", "mode": "ref2v", "seed": r.get("seed")}]
    for num, grade, ratio, mode, scene, prompt in CASES:
        label = f"#{num} {grade}/{ratio}/{mode}/{scene}"
        assert wait_idle(), "队列一直忙"
        body = {"capId": "video.ext", "prompt": prompt, "grade": grade,
                "refs": refs_for(scene, pool),
                "videoExt": {"func": "multiRefFast", "ratio": ratio, "mode": mode, "duration": 5.0}}
        resp = post("/api/tasks", body)
        assert resp.get("ok"), f"{label} 提交失败 {resp}"
        jid = resp["data"]["job_id"]
        job = wait_job(jid, 1)
        assert job, f"{label} 超时"
        r = verify(job, 0, grade, ratio, mode, scene, label)
        jobs.append({"jid": jid, "ratio": ratio, "mode": mode, "seed": r.get("seed")})
        results.append((label, r["id"]))
        print(f"PASS {label} run={r['id']} files={len(r['files'])}", flush=True)

    # 队列切档：用第 6 组和第 8 组作源
    for src, target in [(1, "4采480P"), (3, "20采768P")]:
        base = jobs[src]
        label = f"队列 #{src+5}→{target}"
        assert wait_idle(), "队列一直忙"
        q = post(f"/api/tasks/{base['jid']}/queue", {"grade": target})
        assert q.get("ok"), f"{label} 入队失败 {q}"
        job = wait_job(base["jid"], 2)
        assert job, f"{label} 超时"
        r = verify(job, 1, target, base["ratio"], base["mode"], "", label, check_refs=False)
        assert r.get("seed") == base["seed"], f"{label} 种子没沿用"
        results.append((label, r["id"]))
        print(f"PASS {label} run={r['id']} seed沿用={r.get('seed')}", flush=True)

    print(f"\n===== 续跑 {len(results)}/{len(results)} 组通过 =====", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
