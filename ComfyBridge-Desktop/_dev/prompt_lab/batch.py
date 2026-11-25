"""批量调提示词：读一个 JSON 清单，逐条出图。清单格式见 jobs_*.json。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "_dev" / "prompt_lab" / "gen.py"
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", help="jobs json 路径（相对 _dev/prompt_lab 或绝对）")
    args = ap.parse_args()
    path = Path(args.jobs)
    if not path.is_absolute():
        path = ROOT / "_dev" / "prompt_lab" / path
    jobs = json.loads(path.read_text(encoding="utf-8"))
    for job in jobs:
        pfile = ROOT / "_dev" / "prompt_lab" / job.get("prompt_file", f"_{job['tag']}.txt")
        pfile.write_text(job["prompt"], encoding="utf-8")
        cmd = [str(PY), str(GEN), "--wf", job["wf"], "--prompt-file", str(pfile), "--tag", job["tag"]]
        for key, flag in (("aspect", "--aspect"), ("megapixels", "--megapixels"),
                          ("multiple", "--multiple"), ("seed", "--seed"), ("timeout", "--timeout")):
            if key in job:
                cmd += [flag, str(job[key])]
        if job.get("refs"):
            cmd += ["--refs"] + job["refs"]
        print("=" * 70, flush=True)
        print("RUN", job["tag"], flush=True)
        r = subprocess.run(cmd, cwd=str(ROOT))
        if r.returncode != 0:
            print("FAILED", job["tag"], "rc=", r.returncode, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
