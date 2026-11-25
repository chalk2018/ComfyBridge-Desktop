"""视频 / 音频八条链的实机 smoke：直接连本机 ComfyUI 提交，不等应用层。

只验证两件事：
1. 工作流本身能跑通（模型齐、节点参数对）；
2. videolib.build() 注入出来的提示词 / 尺寸 / 步数 / 参考槽确实生效。

用法：
    .\\.venv\\Scripts\\python.exe tools\\smoke_video_audio.py t2v ref2v music
    （不给参数就跑全部）
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import videolib  # noqa: E402

WF = ROOT / "workflows"
BASE = "http://127.0.0.1:8188"
FFMPEG = Path("D:/ComfyUI-Desktop/Comfy-Desktop/tools/ffmpeg/bin/ffmpeg.exe")
ASSET = Path("D:/workspace/AI短剧相关/抖音+AI学习/AI短剧练习1")

client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(30.0, connect=5.0))


def upload(path: Path, name: str | None = None) -> str:
    with path.open("rb") as fh:
        r = client.post("/upload/image", files={"image": (name or path.name, fh, "application/octet-stream")},
                        data={"type": "input", "overwrite": "true"}, timeout=120)
    r.raise_for_status()
    d = r.json()
    sub = d.get("subfolder") or ""
    return f"{sub}/{d['name']}" if sub else d["name"]


def load(name: str):
    api = json.loads((WF / f"{name}.json").read_text(encoding="utf-8"))
    bridge = json.loads((WF / f"{name}.bridge.json").read_text(encoding="utf-8"))
    return api, bridge


def run(label: str, api: dict, timeout: int = 900) -> tuple[bool, str, dict]:
    t0 = time.time()
    r = client.post("/prompt", json={"prompt": api, "client_id": "smoke"}, timeout=60)
    if r.status_code != 200:
        return False, f"提交被拒: {r.text[:900]}", {}
    pid = r.json()["prompt_id"]
    while time.time() - t0 < timeout:
        h = client.get(f"/history/{pid}", timeout=30).json().get(pid)
        if h:
            status = h.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                detail = msgs[0][1] if msgs else status
                return False, f"执行失败: {json.dumps(detail, ensure_ascii=False)[:1200]}", {}
            if status.get("completed"):
                outs = h.get("outputs") or {}
                files = []
                for nid, payload in outs.items():
                    for key in ("images", "gifs", "audio", "videos", "video"):
                        for item in payload.get(key) or []:
                            if isinstance(item, dict) and item.get("filename"):
                                files.append({**item, "_node": nid})
                return True, f"{time.time() - t0:.0f}s", {"files": files, "history": h}
        time.sleep(3)
    return False, f"超时 {timeout}s", {}


def probe(path: Path) -> str:
    if not FFMPEG.exists():
        return "无 ffmpeg"
    try:
        out = subprocess.run([str(FFMPEG), "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
        keep = [ln.strip() for ln in out.splitlines()
                if "Duration" in ln or "Stream #" in ln]
        return " | ".join(keep)[:300]
    except Exception as exc:  # noqa: BLE001
        return f"probe 失败 {exc}"


def fetch(entry: dict, out_dir: Path) -> Path | None:
    sub = entry.get("subfolder") or ""
    r = client.get("/view", params={"filename": entry["filename"], "subfolder": sub, "type": entry.get("type", "output")})
    if r.status_code != 200:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / entry["filename"]
    path.write_bytes(r.content)
    return path


def video_case(key: str, wf: str, refs_spec: list, text: str, steps: int = 4,
               res: str = "480P", ratio: str = "16:9", note: str = ""):
    print(f"\n=== {key} {note}")
    api, bridge = load(wf)
    spec = bridge["video_ref"]
    width, height = videolib.SIZES[(ratio, res)]
    opt = {"width": width, "height": height, "steps": steps, "length": 124, "lora": ""}
    refs = []
    for path, kind in refs_spec:
        comfy = upload(Path(path))
        refs.append({"kind": kind, "comfyName": comfy})
        print(f"  上传 {Path(path).name} → {comfy}")
    body = videolib.compose(key, text, images=sum(1 for r in refs if r["kind"] == "image"),
                            videos=sum(1 for r in refs if r["kind"] == "video"),
                            audios=sum(1 for r in refs if r["kind"] == "audio"))
    prompt = videolib.to_h3(body) if "<Picture" not in body else body
    api = videolib.build(api, spec, text=prompt, opt=opt, refs=refs, seed=12345,
                         prefix=f"smoke/{key}")
    print(f"  尺寸 {width}x{height} 步数 {steps} 参考 {len(refs)} 提示词 {len(prompt)} 字")
    ok, msg, res_info = run(key, api)
    if not ok:
        print(f"  ✗ {msg}")
        return False
    files = res_info["files"]
    print(f"  ✓ {msg} 产物 {[f['filename'] for f in files]}")
    for f in files:
        path = fetch(f, ROOT / "_dev" / "smoke")
        if path:
            print(f"    {path}  {path.stat().st_size // 1024}KB  {probe(path)}")
    return True


def audio_case(key: str, wf: str, text: str, ref_audio: Path | None = None, note: str = ""):
    print(f"\n=== {key} {note}")
    api, bridge = load(wf)
    prompt_node = bridge["prompt_targets"][0]["node"]
    api[prompt_node]["inputs"][bridge["prompt_targets"][0]["field"]] = text
    for t in bridge.get("seed_targets") or []:
        api[t["node"]]["inputs"][t["field"]] = 12345
    if bridge.get("duration_target"):
        api[bridge["duration_target"]["node"]]["inputs"][bridge["duration_target"]["field"]] = 24.0
    if ref_audio is not None and bridge.get("audio_ref"):
        comfy = upload(ref_audio)
        print(f"  上传 {ref_audio.name} → {comfy}")
        api[bridge["audio_ref"]["load"]]["inputs"][bridge["audio_ref"]["field"]] = comfy
    ok, msg, info = run(key, api)
    if not ok:
        print(f"  ✗ {msg}")
        return False
    files = info["files"]
    print(f"  ✓ {msg} 产物 {[f['filename'] for f in files]}")
    for f in files:
        path = fetch(f, ROOT / "_dev" / "smoke")
        if path:
            print(f"    {path}  {path.stat().st_size // 1024}KB  {probe(path)}")
    return True


def main(argv: list[str]) -> int:
    first = ASSET / "1-4镜" / "苏轼青年出川期三视图（素色交领儒衫）.jpg"
    second = ASSET / "1-4镜" / "苏洵三视图.jpg"
    scene = ASSET / "1-4镜" / "岷江嘉州段江面.jpg"
    board = ASSET / "苏轼分镜素材图片" / "第1镜_汴梁科举考场大全景.jpg".replace("汴梁", "汴京")
    if not board.exists():
        board = ASSET / "苏轼分镜素材图片" / "第1镜_汴京科举考场大全景.jpg"

    todo = argv or ["t2v", "fl2v", "ref2v", "board", "music", "cover", "tts", "clone"]
    results: dict[str, bool] = {}

    if "t2v" in todo:
        results["t2v"] = video_case(
            "t2v", "视频生成-文生视频-h3", [],
            "一位宋代文人在雨后的竹林小径上缓步前行，长镜头跟随，衣袂被风吹起，"
            "环境音是雨滴与鸟鸣。")
    if "fl2v" in todo:
        results["fl2v"] = video_case(
            "fl2v", "视频生成-首尾帧生视频-h3",
            [(first, "image"), (second, "image")],
            "镜头从第一个人物推近到第二个人物，两人在江边对话，中景，稳定运镜。")
    if "ref2v" in todo:
        results["ref2v"] = video_case(
            "ref2v", "视频生成-参考生视频-h3", [(first, "image")],
            "<Picture 1> 中的人物站在江边眺望远方，风吹动衣角，缓慢推镜。")
    if "board" in todo:
        results["board"] = video_case(
            "board", "视频生成-故事板生视频-h3", [(board, "image")],
            "", steps=4, note="（空提示词，纯宫格）")
    if "music" in todo:
        results["music"] = audio_case(
            "music", "音频-音乐生成",
            "古风民谣，古琴与笛子，男声吟唱，节奏舒缓")
    if "tts" in todo:
        results["tts"] = audio_case(
            "tts", "音频-语音生成",
            "人生如逆旅，我亦是行人。这句诗说的是，每个人都在路上。")
    if "cover" in todo:
        ref = ROOT / "_dev" / "smoke" / "ComfyBridge_tts_00001_.mp3"
        cand = sorted((ROOT / "_dev" / "smoke").glob("*tts*.mp3")) if ref.parent.exists() else []
        ref = cand[0] if cand else None
        if ref is None:
            print("\n=== cover 跳过：没有可复用的 TTS 产物")
            results["cover"] = False
        else:
            results["cover"] = audio_case("cover", "音频-参考生成音乐", "轻快的钢琴伴奏，女声哼唱",
                                           ref_audio=ref, note=f"（参考 {ref.name}）")
    if "clone" in todo:
        cand = sorted((ROOT / "_dev" / "smoke").glob("*tts*.mp3"))
        if not cand:
            print("\n=== clone 跳过：没有可复用的 TTS 产物")
            results["clone"] = False
        else:
            results["clone"] = audio_case("clone", "音频-声音克隆", "这是克隆出来的声音，请听一下效果。",
                                          ref_audio=cand[0], note=f"（参考 {cand[0].name}）")

    print("\n---- 汇总")
    for k, v in results.items():
        print(f"{'✓' if v else '✗'} {k}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
