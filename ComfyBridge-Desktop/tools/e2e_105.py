"""1.05 端到端验收：静态口径核对 + 通过应用自身接口跑完整链路。

用法：
    .venv\\Scripts\\python.exe tools/e2e_105.py static
    .venv\\Scripts\\python.exe tools/e2e_105.py img vid audio board canvas
结果写入 _dev/e2e_105.json。
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
APP = Path(__file__).resolve().parents[1]
OUT = APP / "_dev" / "e2e_105.json"
MAT = Path(r"D:\workspace\AI短剧相关\抖音+AI学习\AI短剧练习1")
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


def upload(path: Path, kind: str) -> str:
    boundary = "----wb" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n{kind}\r\n".encode()
            + head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(BASE + "/api/uploads", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    if not d.get("ok"):
        raise RuntimeError(f"上传失败 {path.name}: {d.get('reason')}")
    return d["data"]["id"]


def job_of(job_id: str) -> dict | None:
    for j in (get("/api/state").get("jobs") or []):
        if j.get("id") == job_id:
            return j
    return None


def wait_job(job_id: str, timeout: int = 900) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = job_of(job_id)
        runs = (j or {}).get("runs") or []
        if runs:
            r = runs[-1]
            if r.get("state") in ("done", "failed"):
                return {"job": j, "run": r, "secs": round(time.time() - t0, 1)}
        time.sleep(2)
    return {"job": job_of(job_id), "run": None, "secs": round(time.time() - t0, 1), "timeout": True}


def run_case(name: str, payload: dict, path: str = "/api/tasks", timeout: int = 900,
             expect: dict | None = None) -> dict:
    print(f"\n=== {name} ===", flush=True)
    res = post(path, payload)
    if not res.get("ok"):
        print("  提交被拒：", res.get("reason"), flush=True)
        return {"name": name, "ok": False, "reason": res.get("reason")}
    jid = res["data"]["job_id"]
    out = wait_job(jid, timeout)
    run = out.get("run") or {}
    j = out.get("job") or {}
    files = [{"path": f.get("path"), "kind": f.get("kind"),
              "size": f"{f.get('width')}x{f.get('height')}" if f.get("width") else "",
              "duration": f.get("duration")} for f in (run.get("files") or [])]
    ok = run.get("state") == "done" and all(
        (v is None or (run.get(k) == v)) for k, v in (expect or {}).items())
    real_file = any(Path(f["path"]).is_file() for f in files if f.get("path"))
    ok = ok and real_file
    print("  ->", run.get("state"), run.get("size") or "", run.get("duration") or "",
          f"{out['secs']}s", "file_ok" if real_file else "NO_FILE", flush=True)
    for f in files:
        print("     ", f.get("path"), flush=True)
    if not ok:
        print("     错误：", (run.get("err") or {}).get("msg"), "expect", expect, flush=True)
    return {"name": name, "ok": bool(ok), "job": jid, "secs": out["secs"],
            "state": run.get("state"), "size": run.get("size"), "duration": run.get("duration"),
            "seed": run.get("seed"), "workflow": run.get("wf"), "grade": run.get("grade"),
            "prompt": (run.get("prompt") or "")[:600], "video": j.get("video"),
            "files": files, "err": (run.get("err") or {}).get("msg")}


def static_checks() -> list[dict]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    st = get("/api/state")
    modes = st.get("video", {}).get("modes") or []
    add("视频模式 7 个（4 可用 + 3 禁用）",
        len(modes) == 7 and sum(1 for m in modes if m.get("enabled")) == 4,
        str([(m["name"], m.get("enabled")) for m in modes]))
    add("音频能力合并为 audio.generate",
        [c["id"] for c in st.get("capabilities", []) if str(c["id"]).startswith("audio.")] == ["audio.generate"])
    for m in modes:
        if m.get("enabled") is False:
            continue
        grades = get("/api/video/grades/" + m["id"]).get("data", {}).get("rows") or []
        add(f"{m['name']} 初始只有默认档", len(grades) == 1 and grades[0]["name"] == "默认", str(grades))

    sys.path.insert(0, str(APP))
    from app import videolib
    from app.config import config as app_config
    app_config.load()
    for steps, want in ((4, "4step"), (8, "8step"), (20, None)):
        for mode in ("t2v", "fl2v", "ref2v", "board"):
            o = videolib.opts(mode, "默认", "16:9", "480P", steps)
            key = "fl2v" if mode in ("t2v", "fl2v") else "ref2v"
            lora_ok = (key in str(o.get("lora"))) if want else not o.get("lora")
            add(f"{mode}/{steps}步 LoRA {key if want else '不挂'}", lora_ok,
                f"lora={o.get('lora')} size={o.get('width')}x{o.get('height')} frames={o.get('length')}")

    import yaml
    cfg = yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))
    bad_bridge = []
    for name, spec in (cfg.get("workflows") or {}).items():
        if not isinstance(spec, dict):
            continue
        bp = spec.get("bridge")
        if not bp or not (APP / bp).is_file():
            bad_bridge.append(f"{name}: bridge 不存在")
            continue
        bridge = json.loads((APP / bp).read_text(encoding="utf-8"))
        api = json.loads((APP / spec["file"]).read_text(encoding="utf-8")) if (APP / spec["file"]).is_file() else {}
        for k in ("prompt_targets", "seed_targets", "output_nodes"):
            for target in (bridge.get(k) or []):
                nid = str(target.get("node") if isinstance(target, dict) else target)
                if nid not in api:
                    bad_bridge.append(f"{name}: {k} 指向不存在的节点 {nid}")
        for slot in (bridge.get("ref_slots") or []):
            for target in (slot.get("targets") or []):
                nid = str(target.get("node") or "")
                if nid not in api:
                    bad_bridge.append(f"{name}: ref_slots 指向不存在的节点 {nid}")
        for k in ("video_ref", "audio_ref", "duration_target", "lyrics_target"):
            obj = bridge.get(k)
            if isinstance(obj, dict):
                for nid in (obj.get("node"), obj.get("prompt"), obj.get("switch"),
                            obj.get("steps"), obj.get("base_steps"), obj.get("lora_switch"), obj.get("use8")):
                    if nid and str(nid) not in api:
                        bad_bridge.append(f"{name}: {k} 指向不存在的节点 {nid}")
    add("全部 bridge 注入节点存在", not bad_bridge, "; ".join(bad_bridge[:8]))
    for name, spec in (cfg.get("workflows") or {}).items():
        if isinstance(spec, dict) and str(name).startswith("视频生成-"):
            bridge = json.loads((APP / spec["bridge"]).read_text(encoding="utf-8"))
            add(f"{name} media=video", bridge.get("media") == "video" and bool(bridge.get("video_ref")),
                f"media={bridge.get('media')} video_ref={'yes' if bridge.get('video_ref') else 'no'}")
    return checks


SUSHI_CELLS = [
    "镜1 俯拍汴京礼部科举考场大全景，连片考棚整齐排列，缓慢推进至主考官厅堂，欧阳修拍案惊艳，苏轼在考棚内执笔答题",
    "镜2 眉山苏家书房中景，程氏指着书卷给小苏轼讲范滂事迹，推近至程氏面部近景，再切小苏轼仰头提问",
    "镜3 岷江嘉州段江面远景缓慢横移，木船顺流而下，推至船头，苏轼衣袂随风、抬手遥指远方吟诗",
    "镜4 汴京考官值房，欧阳修与梅尧臣同框中景，欧阳修递考卷给梅尧臣大笑，皆面露赞赏",
]


def main(groups: list[str]) -> int:
    OUT.parent.mkdir(exist_ok=True)
    out_path = OUT if "static" in groups and len(groups) == 1 else OUT.with_name(f"e2e_105_{'_'.join(groups)}.json")
    results: list[dict] = []
    checks = static_checks()
    for c in checks:
        print(("[OK ] " if c["ok"] else "[BAD] ") + c["name"] + (" · " + c["detail"][:180] if c["detail"] else ""), flush=True)
    results.extend(checks)

    ref_a, ref_b, ref_c = (upload(p, "image") for p in (IMG_A, IMG_B, IMG_C))
    print("参考图", ref_a, ref_b, ref_c, flush=True)

    if "img" in groups:
        results.append(run_case("图片·通用风格预设生效", {
            "capId": "image.generate", "grade": "默认",
            "prompt": "宋代文士独立江边，衣袂被江风吹起，远山如黛",
            "image": {"ink": "color", "styleCustom": False, "style": "水墨淡彩"},
        }, timeout=300))
        results.append(run_case("图片·通用线稿 G1", {
            "capId": "image.generate", "grade": "默认",
            "prompt": "苏轼半身像，正侧四分之三角度",
            "image": {"ink": "line", "lineStyle": "G1"},
        }, timeout=300))
        results.append(run_case("图片·参考生图（画布图片节点同源）", {
            "capId": "image.generate", "grade": "默认", "wf": "参考生图",
            "prompt": "同一个人物，站在超然台上，中秋月色，广角", "refs": [ref_b],
        }, timeout=300))

    if "vid" in groups:
        results.append(run_case("视频·文生视频·时长 4.23 秒", {
            "capId": "video.generate", "grade": "默认", "wf": "视频生成-文生视频-h3",
            "prompt": "一位宋代文士站在江边，镜头缓慢推进，江风吹动衣袖",
            "video": {"mode": "t2v", "ratio": "16:9", "res": "480P", "steps": 8, "duration": 4.23},
        }, timeout=900))
        results.append(run_case("视频·首尾帧（8 步 fl2v LoRA）", {
            "capId": "video.generate", "grade": "默认", "wf": "视频生成-首尾帧生视频-h3",
            "refs": [ref_b, ref_a], "prompt": "从伫立姿态缓缓走向高台望月，镜头跟随平移",
            "video": {"mode": "fl2v", "ratio": "16:9", "res": "480P", "steps": 8, "duration": 5.2},
        }, timeout=900))
        results.append(run_case("视频·参考生视频（画布视频节点同源）", {
            "capId": "video.generate", "grade": "默认", "wf": "视频生成-参考生视频-h3",
            "refs": [ref_a], "prompt": "参考 [图 1] 的夜景与构图，人物在台上缓缓抬头",
            "video": {"mode": "ref2v", "ratio": "16:9", "res": "480P", "steps": 8, "duration": 5.2},
        }, timeout=900))

    if "audio" in groups:
        wfs = {"music": "音频-音乐生成", "tts": "音频-语音生成",
               "clone": "音频-声音克隆", "cover": "音频-参考生成音乐"}
        results.append(run_case("音频·music（audio.generate 四 tab 之一）", {
            "capId": "audio.generate", "grade": "默认", "wf": wfs["music"],
            "prompt": "古风民乐，古筝与箫，舒缓，带一点苍凉",
            "audio": {"mode": "music", "lyrics": "一蓑烟雨任平生\n也无风雨也无晴", "duration": 15},
        }, timeout=600))
        tts = run_case("音频·tts（audio.generate 四 tab 之一）", {
            "capId": "audio.generate", "grade": "默认", "wf": wfs["tts"],
            "prompt": "莫听穿林打叶声，何妨吟啸且徐行。", "audio": {"mode": "tts"},
        }, timeout=600)
        results.append(tts)
        # 克隆 / 参考生成音乐必须吃一段真实音频，不能拿图片凑数；用刚生成的 TTS 成品
        pool = sorted(Path(r"D:\ComfyUI-0.37.1\ComfyUI\output\ComfyBridgeOutput\语音生成").glob("*.mp3"),
                      key=lambda p: p.stat().st_mtime)
        audio_ref = upload(pool[-1], "audio") if pool else None
        if not audio_ref:
            results.append({"name": "音频·clone/cover", "ok": False, "reason": "没有可用的参考音频"})
        else:
            print("参考音频", pool[-1].name, audio_ref, flush=True)
            results.append(run_case("音频·clone（audio.generate 四 tab 之一）", {
                "capId": "audio.generate", "grade": "默认", "wf": wfs["clone"],
                "refs": [audio_ref], "prompt": "莫听穿林打叶声，何妨吟啸且徐行。竹杖芒鞋轻胜马，谁怕？",
                "audio": {"mode": "clone"},
            }, timeout=600))
            results.append(run_case("音频·cover（audio.generate 四 tab 之一）", {
                "capId": "audio.generate", "grade": "默认", "wf": wfs["cover"],
                "refs": [audio_ref], "prompt": "古风，箫与古琴，缓慢",
                "audio": {"mode": "cover", "lyrics": "一蓑烟雨任平生", "duration": 15},
            }, timeout=600))

    board = None
    if "board" in groups:
        board = run_case("故事板·苏轼分镜镜1-4", {
            "capId": "board.story", "grade": "默认", "refs": [ref_b],
            "board": {"layout": "G4", "cells": SUSHI_CELLS, "ink": "color"},
        }, timeout=900)
        results.append(board)
        files = board.get("files") or []
        grid = files[0]["path"] if files and files[0].get("path") else None
        if grid and Path(grid).is_file():
            grid_ref = upload(Path(grid), "image")
            results.append(run_case("视频·故事板生视频（苏轼分镜联调）", {
                "capId": "video.generate", "grade": "默认", "wf": "视频生成-故事板生视频-h3",
                "refs": [grid_ref], "prompt": "按故事板逐格生成连续镜头，宋制服饰，镜头平稳",
                "video": {"mode": "board", "ratio": "16:9", "res": "480P", "steps": 8,
                          "grid": 4, "cells": SUSHI_CELLS, "duration": 5.2},
            }, timeout=1200))
        else:
            results.append({"name": "视频·故事板生视频", "ok": False, "reason": "宫格没出图"})

    if "canvas" in groups:
        state = get("/api/canvas")
        funcs = {f["id"]: f for f in state.get("data", state).get("funcs", [])}
        results.append(run_case("画布·图片生成节点（共用参考生图挡位）", {
            "capId": "canvas.video", "prompt": "夕阳下的江面，橙金色，宽幅",
            "grade": (funcs.get("image", {}).get("grades") or [{}])[0].get("name") or "默认",
            "wf": funcs.get("image", {}).get("wf"), "refs": [ref_c],
        }, path="/api/canvas/tasks", timeout=300))
        results.append(run_case("画布·视频生成节点（共用参考生视频挡位）", {
            "capId": "canvas.video", "prompt": "江面波光缓缓流动，镜头缓慢推进",
            "grade": (funcs.get("video", {}).get("grades") or [{}])[0].get("name") or "默认",
            "wf": funcs.get("video", {}).get("wf"), "refs": [ref_a],
            "video": {"mode": "ref2v", "ratio": "16:9", "res": "480P", "steps": 8, "duration": 5.2},
        }, path="/api/canvas/tasks", timeout=900))

    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n========== 汇总 ==========", flush=True)
    bad = [r for r in results if not r.get("ok")]
    for r in results:
        print(("[OK ] " if r.get("ok") else "[BAD] ") + r["name"] + " " + str(r.get("size") or "")
              + " " + str(r.get("duration") or "") + " " + str(r.get("secs") or ""), flush=True)
    print(f"\n{len(results) - len(bad)}/{len(results)} 通过；结果 {out_path}", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a in ("static", "img", "vid", "audio", "board", "canvas")]
    raise SystemExit(main(args or ["static", "img", "vid", "audio", "board", "canvas"]))
