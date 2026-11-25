"""真实出片组合测试：覆盖档位/宽高比/参考模式/参考场景/队列切档。

12 组真实提交 + 2 组队列切档，逐项检查完成状态、applied 参数、种子与成品。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CLIENT = httpx.Client(base_url=BASE, timeout=60.0)
MODE_NAME = {"fl2v": "首尾帧生成", "ref2v": "参考生成"}

# 矩阵：每档至少 2 次、两种比例/模式/参考场景全覆盖
CASES = [
    ("4采480P", "16:9", "fl2v", "none",       "一只橘猫在窗边看雨"),
    ("4采480P", "9:16", "ref2v", "one_image", "白衣女子走过雨夜街巷"),
    ("8采480P", "16:9", "fl2v", "one_image", "金色麦田里的奔跑"),
    ("8采480P", "9:16", "ref2v", "none",       "水下光影中的水母"),
    ("8采768P", "16:9", "ref2v", "mixed",     "多素材混合参考的city夜景"),
    ("8采768P", "9:16", "fl2v", "one_video",  "参考视频续写的海边日落"),
    ("20采480P", "16:9", "fl2v", "one_audio", "听声音画出的舞台灯光"),
    ("20采480P", "9:16", "ref2v", "one_image", "水墨风的山水长卷"),
    ("20采768P", "16:9", "ref2v", "none",      "星空下的雪山延时"),
    ("20采768P", "9:16", "fl2v", "mixed",     "多素材参考的赛博都市"),
]
# 队列切档：(源用例序号, 目标档位)
QUEUE_CASES = [(4, "4采480P"), (0, "20采768P")]


def get(path):
    return CLIENT.get(path).json()


def post(path, body=None):
    return CLIENT.post(path, json=body or {}).json()


def wait_idle(timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        st = get("/api/state")
        if not any(r["state"] in ("queued", "running", "waiting")
                   for j in st.get("jobs", []) for r in j.get("runs", [])):
            return True
        time.sleep(2)
    return False


def wait_job(jid, runs, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        st = get("/api/state")
        job = next((j for j in st.get("jobs", []) if j["id"] == jid), None)
        if job and len(job.get("runs", [])) >= runs:
            states = [r.get("state") for r in job["runs"][:runs]]
            if all(s in ("done", "error", "cancelled") for s in states):
                return job
        time.sleep(3)
    return None


def upload(path: Path, kind: str) -> str:
    with path.open("rb") as fh:
        r = CLIENT.post("/api/uploads", files={"file": (path.name, fh)}).json()
    ref_id = (r.get("data") or {}).get("id", "")
    assert ref_id, f"上传失败 {path.name}: {r}"
    return ref_id


def prepare_refs():
    ups = ROOT / "data" / "uploads"
    pngs = sorted(ups.glob("ref_*.png"))[:6]
    mp4s = sorted(ups.glob("ref_*.mp4"))[:1]
    wav = ROOT / "_dev" / "test_tone.wav"
    assert len(pngs) == 6 and mp4s and wav.exists(), "素材不足"
    return {
        "images": [upload(p, "image") for p in pngs],
        "videos": [upload(mp4s[0], "video")],
        "audios": [upload(wav, "audio"), upload(wav, "audio")],
    }


def refs_for(scene, pool):
    if scene == "none":
        return []
    if scene == "one_image":
        return pool["images"][:1]
    if scene == "one_video":
        return pool["videos"][:1]
    if scene == "one_audio":
        return pool["audios"][:1]
    return pool["images"] + pool["videos"] + pool["audios"]  # 6图+1视频+2音频


def verify_run(job, idx, grade, ratio, mode, scene, label, check_refs=True):
    run = job["runs"][idx]
    assert run["state"] == "done", f"{label} 状态={run['state']} err={run.get('error')}"
    ext = job.get("videoExt") or {}
    assert ext.get("mode") == mode, f"{label} 模式={ext.get('mode')}"
    applied = run.get("applied") or {}
    assert applied.get("档位") == grade, f"{label} 档位={applied.get('档位')}"
    assert applied.get("参考模式") == MODE_NAME[mode], f"{label} applied模式={applied.get('参考模式')}"
    assert ratio in applied.get("宽高比", ""), f"{label} 宽高比={applied.get('宽高比')}"
    if check_refs:
        expect = {"none": "0 图 / 0 视频 / 0 音频",
                  "one_image": "1 图 / 0 视频 / 0 音频",
                  "one_video": "0 图 / 1 视频 / 0 音频",
                  "one_audio": "0 图 / 0 视频 / 1 音频",
                  "mixed": "6 图 / 1 视频 / 2 音频"}[scene]
        assert applied.get("参考") == expect, f"{label} 参考={applied.get('参考')} 期望={expect}"
    files = run.get("files") or []
    assert files, f"{label} 没有成品文件"
    return run


def main() -> int:
    print("准备素材…", flush=True)
    pool = prepare_refs()
    jobs = []
    results = []
    for i, (grade, ratio, mode, scene, prompt) in enumerate(CASES):
        label = f"#{i+1} {grade}/{ratio}/{mode}/{scene}"
        assert wait_idle(), "队列一直忙，无法提交"
        body = {"capId": "video.ext", "prompt": prompt, "grade": grade,
                "refs": refs_for(scene, pool),
                "videoExt": {"func": "multiRefFast", "ratio": ratio,
                             "mode": mode, "duration": 5.0}}
        r = post("/api/tasks", body)
        assert r.get("ok"), f"{label} 提交失败 {r}"
        jid = r["data"]["job_id"]
        job = wait_job(jid, 1)
        assert job, f"{label} 等待超时"
        run = verify_run(job, 0, grade, ratio, mode, scene, label)
        jobs.append({"jid": jid, "grade": grade, "ratio": ratio, "mode": mode,
                     "scene": scene, "seed": run.get("seed")})
        results.append((label, True, run.get("id", "")))
        print(f"PASS {label} run={run.get('id')} files={len(run.get('files') or [])}", flush=True)

    # 队列切档：模式/比例/种子/参考沿用，只换档位
    for src, target in QUEUE_CASES:
        base = jobs[src]
        label = f"队列 #{src+1}→{target}"
        assert wait_idle(), "队列一直忙，无法入队"
        q = post(f"/api/tasks/{base['jid']}/queue", {"grade": target})
        assert q.get("ok"), f"{label} 入队失败 {q}"
        job = wait_job(base["jid"], 2)
        assert job, f"{label} 等待超时"
        run = verify_run(job, 1, target, base["ratio"], base["mode"],
                         base["scene"], label, check_refs=False)
        assert run.get("seed") == base["seed"], f"{label} 种子没沿用"
        results.append((label, True, run.get("id", "")))
        print(f"PASS {label} run={run.get('id')} seed沿用={run.get('seed')}", flush=True)

    print(f"\n===== {len(results)}/{len(results)} 组通过 =====", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
