"""1.04 端到端验收：走应用自己的接口（/api/tasks）跑完整链路，不经 ComfyUI 直连。

用法：
    .venv\\Scripts\\python.exe tools/e2e_104.py            # 全部
    .venv\\Scripts\\python.exe tools/e2e_104.py img vid    # 只跑某几组
结果写到 _dev/e2e_104.json，并打印一张表。
"""

from __future__ import annotations

import json
import mimetypes
import sys
import time
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8190"
MAT = Path(r"D:\workspace\AI短剧相关\抖音+AI学习\AI短剧练习1")
APP = Path(__file__).resolve().parents[1]
OUT = APP / "_dev" / "e2e_104.json"

SUSHI = MAT / "苏轼分镜素材图片"
SHOTS = MAT / "1-4镜"

IMG_A = SUSHI / "第10镜_超然台中秋望月.jpg"
IMG_B = SHOTS / "苏轼青年出川期三视图（素色交领儒衫）.jpg"
IMG_C = SHOTS / "岷江嘉州段江面.jpg"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read())


def post(path: str, body: dict, timeout: int = 60) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def upload(path: Path, kind: str) -> tuple[str, str]:
    """multipart 上传，返回 (ref_id, name)。"""
    boundary = "----wb" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for key, val in (("kind", kind),):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{val}\r\n".encode())
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
    body = b"".join(parts) + head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/uploads", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    if not d.get("ok"):
        raise RuntimeError(f"上传失败 {path.name}: {d.get('reason')}")
    return d["data"]["id"], d["data"].get("name") or path.name


def job_of(job_id: str) -> dict | None:
    st = get("/api/state")
    for j in st.get("jobs") or []:
        if j.get("id") == job_id:
            return j
    return None


def wait_job(job_id: str, timeout: int = 900) -> dict:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        j = job_of(job_id)
        if j:
            runs = j.get("runs") or []
            if runs:
                r = runs[-1]
                sig = f"{r.get('state')}:{r.get('pct')}"
                if sig != last:
                    last = sig
                if r.get("state") in ("done", "failed"):
                    return {"job": j, "run": r, "secs": round(time.time() - t0, 1)}
        time.sleep(2)
    return {"job": job_of(job_id), "run": None, "secs": round(time.time() - t0, 1), "timeout": True}


def run_case(name: str, payload: dict, timeout: int = 900) -> dict:
    print(f"\n=== {name} ===")
    res = post("/api/tasks", payload)
    if not res.get("ok"):
        print("  提交被拒：", res.get("reason"))
        return {"name": name, "ok": False, "reason": res.get("reason")}
    jid = res["data"]["job_id"]
    print("  job", jid, "等待出片…")
    out = wait_job(jid, timeout)
    run = out.get("run") or {}
    files = [{"path": f.get("path"), "kind": f.get("kind"),
              "size": f"{f.get('width')}x{f.get('height')}" if f.get("width") else "",
              "duration": f.get("duration")} for f in (run.get("files") or [])]
    ok = run.get("state") == "done"
    print("  ->", run.get("state"), run.get("size") or "", run.get("duration") or "",
          f"{out['secs']}s")
    for f in files:
        print("     ", f.get("path"))
    if not ok:
        print("     错误：", (run.get("err") or {}).get("msg"))
    return {"name": name, "ok": ok, "job": jid, "secs": out["secs"],
            "state": run.get("state"), "size": run.get("size"),
            "duration": run.get("duration"), "seed": run.get("seed"),
            "workflow": run.get("wf"), "grade": run.get("grade"),
            "files": files, "err": (run.get("err") or {}).get("msg")}


def main(groups: list[str]) -> int:
    results: list[dict] = []
    ref_img_a, _ = upload(IMG_A, "image")
    ref_img_b, _ = upload(IMG_B, "image")
    ref_img_c, _ = upload(IMG_C, "image")
    print("参考图：", ref_img_a, ref_img_b, ref_img_c)

    if "img" in groups:
        results.append(run_case("图片·文生图·水墨风格", {
            "capId": "image.generate", "grade": "默认",
            "prompt": "宋代文士独立江边，衣袂被江风吹起，远山如黛",
            "image": {"ink": "color", "style": "水墨国风，宣纸质感，淡彩"},
        }, 300))
        results.append(run_case("图片·线稿画法", {
            "capId": "image.generate", "grade": "默认",
            "prompt": "苏轼半身像，正侧四分之三角度",
            "image": {"ink": "line", "lineStyle": "W1"},
        }, 300))
        results.append(run_case("图片·参考生图", {
            "capId": "image.generate", "grade": "默认", "wf": "参考生图",
            "prompt": "同一个人物，站在超然台上，中秋月色，广角",
            "refs": [ref_img_b],
        }, 300))

    if "vid" in groups:
        results.append(run_case("视频·文生视频 480P/4步", {
            "capId": "video.generate", "grade": "默认",
            "prompt": "一位宋代文士站在江边，镜头缓慢推进，江风吹动衣袖，远处雾气流动",
            "video": {"mode": "t2v", "ratio": "16:9", "res": "480P", "steps": 4},
        }, 900))
        results.append(run_case("视频·首尾帧生视频", {
            "capId": "video.generate", "grade": "默认",
            "refs": [ref_img_b, ref_img_a],
            "prompt": "从人物的伫立姿态，缓缓走向高台望月，镜头跟随平移",
            "video": {"mode": "fl2v", "ratio": "16:9", "res": "480P", "steps": 4},
        }, 900))
        results.append(run_case("视频·参考生视频", {
            "capId": "video.generate", "grade": "默认",
            "refs": [ref_img_a],
            "prompt": "参考 [图 1] 的夜景与构图，人物在台上缓缓抬头，月光扫过衣襟",
            "video": {"mode": "ref2v", "ratio": "16:9", "res": "480P", "steps": 4},
        }, 900))

    if "board" in groups:
        # 用《一蓑烟雨任平生》分镜脚本镜 1~4 做一张 G4 宫格，再拿它去跑「故事板生视频」
        cells = [
            "镜1 俯拍汴京礼部科举考场大全景，连片考棚整齐排列，缓慢推进至主考官厅堂，欧阳修拍案惊艳，苏轼在考棚内执笔答题",
            "镜2 眉山苏家书房中景，程氏指着书卷给小苏轼讲范滂事迹，推近至程氏面部近景，再切小苏轼仰头提问，最后二人同框",
            "镜3 岷江嘉州段江面远景缓慢横移，木船顺流而下，推至船头，苏轼衣袂随风、抬手遥指远方吟诗，苏洵苏辙立于身后",
            "镜4 汴京考官值房，欧阳修与梅尧臣同框中景，欧阳修递考卷给梅尧臣大笑，再切两人面部近景，皆面露赞赏",
        ]
        board = run_case("故事板·G4 宫格分镜（苏轼脚本镜1-4）", {
            "capId": "board.story", "grade": "默认",
            "refs": [ref_img_b],
            "board": {"layout": "G4", "cells": cells, "ink": "color"},
        }, 900)
        results.append(board)
        grid_path = (board.get("files") or [{}])[0].get("path") if board.get("ok") else None
        if grid_path and Path(grid_path).is_file():
            grid_ref, _ = upload(Path(grid_path), "image")
            results.append(run_case("视频·故事板生视频（用上面这张宫格）", {
                "capId": "video.generate", "grade": "默认",
                "refs": [grid_ref],
                "prompt": "按故事板逐格生成连续镜头，古装人物，宋制服饰，镜头平稳",
                "video": {"mode": "board", "ratio": "16:9", "res": "480P", "steps": 4,
                          "grid": 4, "cells": cells},
            }, 1200))
        else:
            results.append({"name": "视频·故事板生视频", "ok": False, "reason": "宫格图没生成出来"})

    if "audio" in groups:
        results.append(run_case("音频·音乐生成", {
            "capId": "audio.music", "grade": "默认",
            "prompt": "古风民乐，古筝与箫，舒缓，带一点苍凉",
            "audio": {"mode": "music", "lyrics": "一蓑烟雨任平生\n也无风雨也无晴", "duration": 15},
        }, 600))
        tts = run_case("音频·语音生成", {
            "capId": "audio.tts", "grade": "默认",
            "prompt": "莫听穿林打叶声，何妨吟啸且徐行。",
            "audio": {"mode": "tts"},
        }, 600)
        results.append(tts)

    if "audio2" in groups:
        # 克隆 / 参考生成音乐都要一段参考音频：直接拿上一步 TTS 的成品
        pool = sorted((Path(r"D://ComfyUI-0.37.1//ComfyUI//output//ComfyBridgeOutput//语音生成")).glob("*.mp3"),
                      key=lambda x: x.stat().st_mtime)
        if not pool:
            results.append({"name": "音频·声音克隆", "ok": False, "reason": "没有可用的参考音频"})
        else:
            ref_audio, _ = upload(pool[-1], "audio")
            print("参考音频：", pool[-1].name, ref_audio)
            results.append(run_case("音频·声音克隆", {
                "capId": "audio.clone", "grade": "默认", "refs": [ref_audio],
                "prompt": "莫听穿林打叶声，何妨吟啸且徐行。竹杖芒鞋轻胜马，谁怕？一蓑烟雨任平生。",
                "audio": {"mode": "clone"},
            }, 600))
            results.append(run_case("音频·参考生成音乐", {
                "capId": "audio.music.ref", "grade": "默认", "refs": [ref_audio],
                "prompt": "古风，箫与古琴，缓慢",
                "audio": {"mode": "cover", "lyrics": "一蓑烟雨任平生", "duration": 15},
            }, 600))

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n\n================ 汇总 ================")
    for r in results:
        flag = "OK " if r.get("ok") else "FAIL"
        print(f"[{flag}] {r['name']}  {r.get('size') or ''} {r.get('duration') or ''}  {r.get('secs')}s")
    bad = [r for r in results if not r.get("ok")]
    print(f"\n{len(results) - len(bad)}/{len(results)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a in ("img", "vid", "audio", "audio2", "board")]
    raise SystemExit(main(args or ["img", "vid", "audio", "audio2", "board"]))
