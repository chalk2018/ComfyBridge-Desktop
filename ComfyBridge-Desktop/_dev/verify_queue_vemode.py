"""端到端验证：参考模式提交 + 队列切档后模式/模型同步切换。"""
from __future__ import annotations

import json
import time

import httpx

BASE = "http://127.0.0.1:8190"
CLIENT = httpx.Client(base_url=BASE, timeout=60.0)


def get(path: str):
    return CLIENT.get(path).json()


def post(path: str, body: dict | None = None):
    return CLIENT.post(path, json=body or {}).json()


def wait_job(jid: str, runs: int, timeout: float = 420.0):
    end = time.time() + timeout
    while time.time() < end:
        st = get("/api/state")
        job = next((j for j in st.get("jobs", []) if j["id"] == jid), None)
        if job and len(job.get("runs", [])) >= runs:
            states = [r.get("state") for r in job["runs"][:runs]]
            if all(s in ("done", "error", "cancelled") for s in states):
                return job
        time.sleep(2)
    return None


def main() -> int:
    body = {
        "capId": "video.ext",
        "prompt": "一只橘猫在窗边看雨，慢镜头",
        "grade": "8采480P",
        "refs": [],
        "videoExt": {
            "func": "multiRefFast", "ratio": "16:9", "mode": "ref2v", "duration": 5.0,
        },
    }
    r = post("/api/tasks", body)
    print("submit:", json.dumps(r, ensure_ascii=False))
    assert r.get("ok"), r
    jid = r["data"]["job_id"]

    job = wait_job(jid, 1)
    assert job, "首发任务超时"
    run1 = job["runs"][0]
    print("run1:", run1.get("state"), run1.get("grade"), json.dumps(run1.get("applied"), ensure_ascii=False))
    assert run1.get("state") == "done", run1.get("error")
    assert job["videoExt"]["mode"] == "ref2v"
    applied1 = run1.get("applied") or {}
    assert applied1.get("参考模式") == "参考生成", applied1

    # 队列切档：模式/提示词/种子/参考都沿用，只换档位对应的工作流与模型
    q = post(f"/api/tasks/{jid}/queue", {"grade": "20采480P"})
    print("queue:", json.dumps(q, ensure_ascii=False))
    assert q.get("ok"), q
    job = wait_job(jid, 2)
    assert job, "队列任务超时"
    run2 = job["runs"][1]
    print("run2:", run2.get("state"), run2.get("grade"), json.dumps(run2.get("applied"), ensure_ascii=False))
    assert run2.get("state") == "done", run2.get("error")
    assert run2.get("grade") == "20采480P", run2
    applied2 = run2.get("applied") or {}
    assert applied2.get("参考模式") == "参考生成", applied2
    assert run2.get("seed") == run1.get("seed"), "队列没沿用种子"

    print("QUEUE MODE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
