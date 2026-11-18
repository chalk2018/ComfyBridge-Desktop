"""道具多视图批量出图：读 JSON 清单，逐条走项目链路出图。

清单格式：[{"tag": "...", "text": "...", "ref": "可选参考图路径", "style": "写实摄影"}]
没有 ref 就走文生图，有 ref 就走图生图（lock）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import extsheets as ext  # noqa: E402
from app.viewdedup import dedup_views  # noqa: E402

LAB = ROOT / "_dev" / "prompt_lab"


def main() -> int:
    plan_path = Path(sys.argv[1])
    jobs = json.loads(plan_path.read_text(encoding="utf-8"))
    ok = 0
    for i, job in enumerate(jobs, 1):
        tag = job["tag"]
        text = job["text"]
        style = job.get("style", "写实摄影")
        ref = job.get("ref")
        if ref:
            prompt = ext.compose_prop("i2i", "MV", text, ref="lock", ink="color", style=style)
            wf = "设定图-参考生图"
        else:
            prompt = ext.compose_prop("t2i", "MV", text, ink="color", style=style)
            wf = "设定图-文生图"
        pf = LAB / f"_b_{tag}.txt"
        pf.write_text(prompt, encoding="utf-8")
        cmd = [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(LAB / "gen.py"),
               "--wf", wf, "--prompt-file", str(pf), "--tag", tag,
               "--aspect", "16:9 (Widescreen)", "--megapixels", "1.265625", "--multiple", "16"]
        if ref:
            cmd += ["--refs", ref]
        print(f"[{i}/{len(jobs)}] {tag}", flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        line = (r.stdout or "").strip().splitlines()
        print("   ", line[-1] if line else (r.stderr or "")[-200:], flush=True)
        if r.returncode == 0:
            ok += 1
            out = LAB / "out" / f"{tag}__LAB_{tag}_00001.png"
            if out.exists():
                print("    dedup", dedup_views(out), flush=True)
    print(f"done {ok}/{len(jobs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
