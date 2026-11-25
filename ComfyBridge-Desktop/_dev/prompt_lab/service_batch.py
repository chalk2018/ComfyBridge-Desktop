"""道具设定图：走正式服务链路（http://127.0.0.1:8190）批量出图。

与 prop_batch.py 的区别：那个只调 compose + ComfyUI，绕过了服务里的
「白图占位」「出图后视觉去重」。这里全程打 HTTP 接口，等价于用户在前端点一次。

清单格式：
    [{"tag": "...", "text": "...", "ref": "参考图路径（可选）", "style": "写实摄影",
      "layout": "MV", "mode": "i2i"}]

用法：
    .venv/Scripts/python.exe _dev/prompt_lab/service_batch.py _dev/prompt_lab/plan_svc.json
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
ROOT = Path(__file__).resolve().parents[2]


def _json(url: str, payload: dict | None = None, method: str | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def upload(path: Path) -> str:
    boundary = "----wb" + uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        BASE + "/api/uploads", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read().decode("utf-8"))
    return (out.get("data") or out)["id"]


def state() -> dict:
    return _json(BASE + "/api/state")


def find_job(s: dict, job_id: str) -> dict | None:
    for job in s.get("jobs") or []:
        if job.get("id") == job_id:
            return job
    return None


def wait(job_id: str, timeout: float = 900.0) -> dict:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        job = find_job(state(), job_id)
        if job:
            runs = job.get("runs") or []
            if runs:
                run = runs[-1]
                cur = f"{run.get('state')} {run.get('pct')}"
                if cur != last:
                    print(f"      {cur}", flush=True)
                    last = cur
                if run.get("state") in ("done", "error", "failed", "canceled"):
                    return run
        time.sleep(4)
    return {"state": "timeout"}


def run_one(item: dict) -> dict:
    cap = "sheet.prop"
    mode = item.get("mode") or ("i2i" if item.get("ref") else "t2i")
    refs = []
    if item.get("ref"):
        refs = [upload(Path(item["ref"]))]
    ext = {
        "kind": "prop", "mode": mode, "layout": item.get("layout", "MV"),
        "text": item["text"], "ink": item.get("ink", "color"),
        "nochar": "yes", "ref": "lock" if mode == "i2i" else "none",
        "refSlots": {}, "style": item.get("style", "写实摄影"),
        "styleCustom": False, "styleText": "", "grade": item.get("grade", ""),
    }
    resp = _json(BASE + "/api/tasks",
                 {"capId": cap, "ext": ext, "refs": refs, "grade": item.get("grade", "")})
    if not resp.get("ok", True):
        return {"ok": False, "reason": resp.get("reason") or resp}
    job_id = (resp.get("data") or resp).get("job_id")
    print(f"    job {job_id}", flush=True)
    run = wait(job_id)
    files = run.get("files") or []
    return {"ok": run.get("state") == "done", "run": run.get("id"),
            "state": run.get("state"), "err": run.get("err"),
            "path": (files[0].get("path") if files else ""),
            "dedup": (files[0].get("dedup") if files else None),
            "prompt_file": ""}


def main() -> int:
    plan = Path(sys.argv[1])
    items = json.loads(plan.read_text(encoding="utf-8"))
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    results = []
    for i, it in enumerate(items, 1):
        tag = it["tag"]
        if only and only not in tag:
            continue
        print(f"[{i}/{len(items)}] {tag}  {it.get('ref', '(文生图)')}", flush=True)
        rec = {"tag": tag, **{k: it.get(k, "") for k in ("text", "ref", "mode", "layout")}}
        try:
            rec.update(run_one(it))
        except Exception as exc:  # noqa: BLE001
            rec.update({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
        print(f"    -> {rec.get('ok')} {rec.get('path') or rec.get('err') or rec.get('reason')}"
              f"  dedup={json.dumps(rec.get('dedup'), ensure_ascii=False) if rec.get('dedup') else '-'}",
              flush=True)
        results.append(rec)
        out = ROOT / "_dev" / "prompt_lab" / "out" / f"svc_{plan.stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done {sum(1 for r in results if r.get('ok'))}/{len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
