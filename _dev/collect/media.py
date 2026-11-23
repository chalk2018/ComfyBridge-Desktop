"""下载与派生参考素材。

* 图片：按 Pexels 的尺寸参数下 1280 宽，转成体积可控的 jpg；
* 视频：下 sd 预览版，超过 10 秒的裁到 8 秒并压到 720p，避免素材库爆体积；
* 首尾帧：从同一条真实视频里抽第一帧与最后一帧，天然对应，不会「首帧和尾帧不是一个场景」；
* 故事板：抽 6 帧拼成 3×2 分格板，每格左上角打序号，就是一份真故事板；
* 音频：走 synth_audio 合成。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
MEDIA = HERE / "media"
FFMPEG = "D:/ComfyUI-Desktop/Comfy-Desktop/tools/ffmpeg/bin/ffmpeg.exe"
PROXY = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""


def curl(url: str, out: Path, extra: list[str] | None = None) -> bool:
    if out.exists() and out.stat().st_size > 2000:
        return True
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-sL", "-m", "180", "-A", "Mozilla/5.0 ComfyBridge", url, "-o", str(out)]
    if PROXY:
        cmd[1:1] = ["-x", PROXY]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, capture_output=True)
    ok = r.returncode == 0 and out.exists() and out.stat().st_size > 2000
    if not ok:
        out.unlink(missing_ok=True)
    return ok


def image_of(cat: dict, cid: str, out: Path) -> bool:
    it = next((x for x in cat["photo"] if x["id"] == cid), None)
    if not it:
        raise RuntimeError("catalog 里没有图片 " + cid)
    base = it["file"].split("?")[0]
    url = base + "?auto=compress&cs=tinysrgb&w=1280"
    if not curl(url, out.with_suffix(".jpg")):
        return False
    im = Image.open(out.with_suffix(".jpg")).convert("RGB")
    if im.width > 1280:
        im = im.resize((1280, round(im.height * 1280 / im.width)), Image.LANCZOS)
    im.save(out.with_suffix(".jpg"), "JPEG", quality=86, optimize=True)
    return True


def video_of(cat: dict, cid: str, out: Path) -> bool:
    it = next((x for x in cat["video"] if x["id"] == cid), None)
    if not it:
        raise RuntimeError("catalog 里没有视频 " + cid)
    src = it.get("file") or it.get("hd")
    raw = out.with_suffix(".src.mp4")
    if not curl(src, raw):
        return False
    dur = float(it.get("duration") or 0)
    args = [FFMPEG, "-y", "-loglevel", "error", "-i", str(raw)]
    if dur > 10:
        args += ["-t", "8"]
    args += ["-vf", "scale='min(960,iw)':-2,fps=24",
             "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-an",
             "-movflags", "+faststart", str(out.with_suffix(".mp4"))]
    subprocess.run(args, check=True)
    raw.unlink(missing_ok=True)
    return out.with_suffix(".mp4").exists()


def probe_duration(p: Path) -> float:
    r = subprocess.run([FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error",
                        "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    try:
        return round(float(r.stdout.strip()), 1)
    except ValueError:
        return 0.0


def _frame(video: Path, at: float | None, out: Path, last: bool = False) -> None:
    args = [FFMPEG, "-y", "-loglevel", "error"]
    if last:
        args += ["-sseof", "-0.15"]
    elif at is not None:
        args += ["-ss", f"{at}"]
    args += ["-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)]
    subprocess.run(args, check=True)


def frame_pair(video: Path, out_first: Path, out_last: Path) -> None:
    _frame(video, 0.0, out_first)
    d = probe_duration(video)
    _frame(video, max(0.0, d - 0.15), out_last)
    for p in (out_first, out_last):
        im = Image.open(p).convert("RGB")
        if im.width > 1280:
            im = im.resize((1280, round(im.height * 1280 / im.width)), Image.LANCZOS)
        im.save(p.with_suffix(".jpg"), "JPEG", quality=86, optimize=True)


def storyboard_sheet(video: Path, out: Path, panels: int = 6) -> None:
    d = probe_duration(video) or 6.0
    tmp = out.parent / "_tmp_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    times = [max(0.0, d * (i + 0.5) / panels) for i in range(panels)]
    frames = []
    for i, t in enumerate(times):
        p = tmp / f"f{i}.jpg"
        _frame(video, t, p)
        frames.append(p)
    cols, rows = 3, (panels + 2) // 3
    cell_w, cell_h = 480, 270
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (18, 20, 26))
    dr = ImageDraw.Draw(sheet)
    for i, p in enumerate(frames):
        im = Image.open(p).convert("RGB").resize((cell_w, cell_h), Image.LANCZOS)
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        sheet.paste(im, (x, y))
        dr.rectangle([x + 6, y + 6, x + 34, y + 32], fill=(0, 0, 0))
        dr.text((x + 12, y + 10), str(i + 1), fill=(255, 255, 255))
        dr.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline=(60, 66, 80), width=2)
    sheet.save(out.with_suffix(".jpg"), "JPEG", quality=88, optimize=True)
    for p in frames:
        p.unlink(missing_ok=True)


def run_job(job: dict) -> tuple[bool, dict | None]:
    out = MEDIA / job["file"]
    kind = job["op"]
    try:
        if kind == "image":
            return bool(image_of(cat, job["id"], out.with_suffix(".jpg"))), None
        if kind == "video":
            return bool(video_of(cat, job["id"], out.with_suffix(".mp4"))), None
        if kind == "frames":
            vid = MEDIA / job["video"]
            if not vid.exists():
                video_of(cat, job["id"], vid.with_suffix(".mp4"))
            frame_pair(vid.with_suffix(".mp4"), out.with_name(job["files"][0]),
                       out.with_name(job["files"][1]))
            return True, None
        if kind == "board":
            vid = MEDIA / job["video"]
            if not vid.exists():
                video_of(cat, job["id"], vid.with_suffix(".mp4"))
            storyboard_sheet(vid.with_suffix(".mp4"), out.with_suffix(".jpg"),
                             job.get("panels", 6))
            return True, None
        raise RuntimeError("未知操作 " + kind)
    except Exception as e:  # noqa: BLE001
        return False, {"job": job, "err": type(e).__name__ + ": " + str(e)[:120]}


def main() -> None:
    global cat
    cat = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
    plan = json.loads((HERE / "media_plan.json").read_text(encoding="utf-8"))
    # 去重：同一素材只下一次
    seen, jobs = set(), []
    for job in plan:
        k = (job["op"], job.get("id"), job["file"])
        if k in seen:
            continue
        seen.add(k)
        jobs.append(job)
    MEDIA.mkdir(parents=True, exist_ok=True)
    ok = 0
    fails = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (good, err) in enumerate(pool.map(run_job, jobs), 1):
            ok += good
            if err:
                fails.append(err)
                print("FAIL", err["job"].get("file"), err["err"], flush=True)
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)} 成功 {ok} 失败 {len(fails)}", flush=True)
    print(f"完成 {ok} 个文件，失败 {len(fails)}", flush=True)
    if fails:
        (HERE / "media_failed.json").write_text(
            json.dumps(fails, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
