"""ComfyBridge 端到端自测：真实调用本机 ComfyUI 出图，逐项核对需求功能。

运行：
    .venv\\Scripts\\python.exe tests\\smoke_test.py
前置：服务已在 127.0.0.1:8190 运行、ComfyUI 在 8188 且队列空闲。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8190"
ROOT = Path(__file__).resolve().parents[1]
CLIENT = httpx.Client(base_url=BASE, timeout=30.0)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail[:220]}" if detail else ""), flush=True)


def get(path: str) -> Any:
    return CLIENT.get(path).json()


def post(path: str, body: dict[str, Any] | None = None) -> Any:
    return CLIENT.post(path, json=body or {}).json()


def find_job(job_id: str) -> dict[str, Any] | None:
    return next((j for j in get("/api/state")["jobs"] if j["id"] == job_id), None)


def wait_runs(job_id: str, count: int, timeout: int = 240) -> dict[str, Any] | None:
    """等到这张卡里前 count 个 run 都结束（成功/失败/取消）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = find_job(job_id)
        if job and len(job["runs"]) >= count:
            runs = job["runs"][:count]
            if all(r["state"] in ("done", "failed", "canceled") for r in runs):
                return job
        time.sleep(0.8)
    return None


def main() -> int:
    """依次验证页面、状态、生成、队列、上传、归档、管理台、对外接口。"""
    # 1. 页面与状态
    page = CLIENT.get("/")
    check("页面/首页", page.status_code == 200 and "ComfyBridge" in page.text)
    app_js = CLIENT.get("/static/app.js")
    check("前端脚本", app_js.status_code == 200 and len(app_js.content) > 10000)
    state = get("/api/state")
    check("状态接口字段", all(k in state for k in ("service", "capabilities", "jobs", "wfLib", "ui", "health")))
    check("17 个能力 / 图片生成可用",
          len(state["capabilities"]) == 17 and len(state["capabilities"][0]["workflows"]) == 2)
    check("档位表 2 个母版 tab", len(state["wfLib"]) == 2)
    check("顶栏健康灯", state["health"]["online"] is True, str(state["health"]["queue"]))

    # 2. 忙闲判定：ComfyUI 队列必须空闲才能开始
    q = get("/api/v1/health")
    if q["queue"]["running"] or q["queue"]["pending"]:
        check("ComfyUI 队列空闲", False, "ComfyUI 里还有别的任务，测试可能被拦")
    else:
        check("ComfyUI 队列空闲", True)

    # 3. 普通生成（文生图）
    created = post("/api/tasks", {"capId": "image.generate", "prompt": "一只橘猫坐在窗台上，午后阳光，写实摄影",
                                  "grade": "默认", "refs": []})
    check("普通生成提交", created.get("ok") is True, str(created))
    job_id = created.get("data", {}).get("job_id", "")
    if not job_id:
        return report()
    job = wait_runs(job_id, 1)
    check("文生图完成", bool(job and job["runs"][0]["state"] == "done"),
          json.dumps(job["runs"][0], ensure_ascii=False)[:400] if job else "超时")
    if not job or job["runs"][0]["state"] != "done":
        return report()
    run1 = job["runs"][0]
    check("工作流选中文生图", run1["wf"] == "文生图", run1["wf"])
    check("回读到真实种子", isinstance(run1["seed"], int) and run1["seed"] >= 0, str(run1["seed"]))
    check("成品文件已归档", bool(run1["files"]) and Path(run1["files"][0]["path"]).exists(),
          str(run1["files"][0]["path"]) if run1["files"] else "无文件")
    check("归档落在成果目录", "ComfyBridgeOutput" in (run1["files"][0]["path"] if run1["files"] else ""))
    image = CLIENT.get(f"/api/media?run={run1['id']}&i=0")
    check("成品可预览", image.status_code == 200 and len(image.content) > 10000, str(len(image.content)))
    download = CLIENT.get(f"/api/download?run={run1['id']}&i=0")
    check("成品可下载", download.status_code == 200 and len(download.content) > 10000)

    # 4. 加入队列生成：沿用种子、接到同一张卡
    queue_res = post(f"/api/tasks/{job_id}/queue", {"grade": "精细（16:9）"})
    check("队列生成准入", queue_res.get("ok") is True, str(queue_res))
    if queue_res.get("ok"):
        job2 = wait_runs(job_id, 2)
        check("队列出片", bool(job2 and job2["runs"][1]["state"] == "done"),
              json.dumps(job2["runs"][1], ensure_ascii=False)[:300] if job2 else "超时")
        if job2:
            r2 = job2["runs"][1]
            check("同一批沿用同一种子", r2["seed"] == run1["seed"], f"{run1['seed']} vs {r2['seed']}")
            check("队列档位生效", r2["grade"] == "精细（16:9）")
            check("同一张卡里两个成品", len(job2["runs"]) == 2)

    # 5. 忙时普通生成必须被拦（提交一个任务后立刻再发）
    second = post("/api/tasks", {"capId": "image.generate", "prompt": "再画一张", "grade": "默认", "refs": []})
    # 此刻如果已有任务在跑会被拦；空闲时允许，两种情况都验证判定存在
    if second.get("ok"):
        blocked = post("/api/tasks", {"capId": "image.generate", "prompt": "连点发送", "grade": "默认", "refs": []})
        check("连点发送被拦（不排队）", blocked.get("ok") is False and "还没跑完" in blocked.get("reason", ""),
              str(blocked))
        # 清掉这个测试任务，避免影响后续
        jid2 = second["data"]["job_id"]
        st = get("/api/state")
        j2 = next((j for j in st["jobs"] if j["id"] == jid2), None)
        if j2:
            post(f"/api/runs/{j2['runs'][0]['id']}/cancel", {})
        wait_runs(jid2, 1, 60)
    else:
        check("忙时普通生成被拦", "还没跑完" in second.get("reason", ""), str(second))

    # 6. 参考生图：上传参考图（用刚出的成品）→ 自动挑参考生图工作流
    img_bytes = CLIENT.get(f"/api/media?run={run1['id']}&i=0").content
    uploaded = CLIENT.post("/api/uploads", files={"file": ("ref.png", img_bytes, "image/png")}).json()
    ref_id = (uploaded.get("data") or {}).get("id", "")
    check("参考图上传", bool(ref_id), str(uploaded))
    if ref_id:
        # 等队列彻底空，避免普通生成被拦
        deadline = time.time() + 90
        while time.time() < deadline:
            st = get("/api/state")
            if not any(r["state"] in ("queued", "running") for j in st["jobs"] for r in j["runs"]):
                break
            time.sleep(0.8)
        edit = post("/api/tasks", {"capId": "image.generate", "prompt": "把画面改成月光下的夜景，保持构图",
                                   "grade": "默认", "refs": [ref_id]})
        check("参考生图提交", edit.get("ok") is True, str(edit))
        if edit.get("ok"):
            jid3 = edit["data"]["job_id"]
            job3 = wait_runs(jid3, 1, 240)
            check("参考生图完成", bool(job3 and job3["runs"][0]["state"] == "done"),
                  json.dumps(job3["runs"][0], ensure_ascii=False)[:400] if job3 else "超时")
            if job3:
                check("自动挑中参考生图工作流", job3["runs"][0]["wf"] == "参考生图", job3["runs"][0]["wf"])
                check("参考生图成品可预览",
                      CLIENT.get(f"/api/media?run={job3['runs'][0]['id']}&i=0").status_code == 200)

    # 7. 管理台四页相关接口
    diag = get("/api/diag")
    check("自检只有两项", len(diag.get("data", [])) == 2, str(diag.get("data")))
    nodes = get("/api/workflows/%E6%96%87%E7%94%9F%E5%9B%BE/nodes")
    check("工作流节点清单", len(nodes.get("data", {}).get("nodes", [])) >= 10)
    added = post("/api/workflows/%E6%96%87%E7%94%9F%E5%9B%BE/grades",
                 {"name": "自检档", "note": "临时", "params": {"459_456": {"width": 896, "height": 896}}})
    check("添加档位（生成拷贝）", added.get("ok") is True, str(added))
    state_after = get("/api/state")
    has_grade = any(g["name"] == "自检档" for g in state_after["capabilities"][0]["workflows"][0]["grades"])
    copy_path = ROOT / "workflows" / "ComfyBridge" / "文生图__自检档.json"
    check("档位出现在创作台 + 拷贝文件存在", has_grade and copy_path.exists())
    deleted = CLIENT.delete("/api/workflows/%E6%96%87%E7%94%9F%E5%9B%BE/grades/%E8%87%AA%E6%A3%80%E6%A1%A3").json()
    check("删除档位", deleted.get("ok") is True and not copy_path.exists(), str(deleted))
    check("默认档不可删", CLIENT.delete("/api/workflows/%E6%96%87%E7%94%9F%E5%9B%BE/grades/%E9%BB%98%E8%AE%A4").json().get("ok") is False)
    reloaded = post("/api/config/reload", {})
    check("重读配置", reloaded.get("ok") is True, str(reloaded))
    check("服务设置保存", post("/api/config", {"patch": {"keepDays": 7}}).get("ok") is True)
    check("打开目录白名单", post("/api/open-folder", {"path": "C:/Windows"}).get("ok") is False)

    # 8. 对外接口（斜线技能用）
    menu = get("/api/v1/capabilities")
    check("看菜单只列可用能力", len(menu["capabilities"]) == 1 and menu["capabilities"][0]["id"] == "image.generate")
    precheck = post("/api/v1/precheck", {"capability": "image.generate", "prompt": "测试"})
    check("预检接口", "ready" in precheck)
    tasks = get("/api/v1/tasks")
    check("任务列表接口", len(tasks["tasks"]) >= 1)
    skill = CLIENT.get("/api/v1/skill")
    check("技能文本", skill.status_code == 200 and "四步" in skill.text)
    status = get(f"/api/v1/orders/{job_id}")
    check("取货接口", status.get("state") == "done" and len(status.get("files", [])) >= 2, str(status)[:260])

    # 9. 历史批量下载
    sel = [{"run": run1["id"], "index": 0}]
    zip_resp = CLIENT.post("/api/download/batch", json={"items": sel})
    check("批量下载打包", zip_resp.status_code == 200 and zip_resp.content[:2] == b"PK", str(len(zip_resp.content)))
    return report()


def report() -> int:
    """打印汇总，返回退出码。"""
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n===== {passed}/{len(RESULTS)} 项通过 =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail[:300]}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
