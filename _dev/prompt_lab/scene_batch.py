"""场景设定图：走正式服务链路（http://127.0.0.1:8190）批量出图。

清单格式：
    [{"tag": "...", "text": "...", "layout": "P", "mode": "i2i", "sub": "",
      "slot": "ref",              # ref / pano / bird / sketch，缺省 ref
      "ref": "参考图路径",         # 可选；expand 必须
      "extra": {"bird": "另一张图的路径"},   # 可选，多槽位
      "style": "写实摄影", "ink": "color", "nochar": "yes"}]

用法：
    .venv/Scripts/python.exe _dev/prompt_lab/scene_batch.py <plan.json> [tag过滤]
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


def _json(url: str, payload: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=90) as r:
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


def wait(job_id: str, timeout: float = 900.0) -> dict:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        job = next((j for j in (state().get("jobs") or []) if j.get("id") == job_id), None)
        if job and job.get("runs"):
            run = job["runs"][-1]
            cur = f"{run.get('state')} {run.get('pct')}"
            if cur != last:
                print(f"      {cur}", flush=True)
                last = cur
            if run.get("state") in ("done", "error", "failed", "canceled"):
                return run
        time.sleep(4)
    return {"state": "timeout"}


def run_one(item: dict) -> dict:
    mode = item.get("mode") or "t2i"
    slot = item.get("slot") or "ref"
    refs: list[str] = []
    ref_slots: dict[str, str] = {}
    if item.get("ref"):
        rid = upload(Path(item["ref"]))
        refs.append(rid)
        ref_slots[slot] = rid
    for name, p in (item.get("extra") or {}).items():
        rid = upload(Path(p))
        refs.append(rid)
        ref_slots[name] = rid
    ext = {
        "kind": "scene", "mode": mode, "layout": item["layout"],
        "sub": item.get("sub", ""), "text": item.get("text", ""),
        "ink": item.get("ink", "color"), "nochar": item.get("nochar", "yes"),
        "ref": "none", "refSlots": ref_slots,
        "style": item.get("style", "写实摄影"), "styleCustom": False,
        "styleText": "", "lineStyle": "", "grade": item.get("grade", ""),
    }
    resp = _json(BASE + "/api/tasks", {"capId": "sheet.scene", "ext": ext,
                                       "refs": refs, "grade": item.get("grade", "")})
    if not resp.get("ok", True):
        return {"ok": False, "reason": resp.get("reason") or resp}
    job_id = (resp.get("data") or resp).get("job_id")
    print(f"    job {job_id}", flush=True)
    run = wait(job_id)
    files = run.get("files") or []
    return {"ok": run.get("state") == "done", "run": run.get("id"),
            "state": run.get("state"), "err": run.get("err"),
            "path": (files[0].get("path") if files else "")}


def main() -> int:
    plan = Path(sys.argv[1])
    items = json.loads(plan.read_text(encoding="utf-8"))
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    out = ROOT / "_dev" / "prompt_lab" / "out" / f"scene_{plan.stem}.json"
    results = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
    done = {r["tag"] for r in results if r.get("ok")}
    for i, it in enumerate(items, 1):
        tag = it["tag"]
        if only and only not in tag:
            continue
        if tag in done:
            print(f"[{i}/{len(items)}] {tag} 跳过（已完成）", flush=True)
            continue
        print(f"[{i}/{len(items)}] {tag}  {it.get('layout')}/{it.get('mode')} {it.get('ref', '')}", flush=True)
        rec = {"tag": tag, **{k: it.get(k, "") for k in
                              ("text", "ref", "mode", "layout", "slot", "sub")}}
        try:
            rec.update(run_one(it))
        except Exception as exc:  # noqa: BLE001
            rec.update({"ok": False, "reason": f"{type(exc).__name__}: {exc}"})
        print(f"    -> {rec.get('ok')} {rec.get('path') or rec.get('err') or rec.get('reason')}",
              flush=True)
        results = [r for r in results if r["tag"] != tag] + [rec]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done {sum(1 for r in results if r.get('ok'))}/{len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
