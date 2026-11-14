"""服务端唯一状态：任务、UI 草稿、上传引用、ComfyUI 健康度。

需求 §0.2：状态只有一份，在服务端；页面刷新/新开窗口都重新拉取。
这里用内存 dict + 单份 JSON 文件持久化（不引入数据库，需求 X9）。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .config import ROOT, config

STATE_FILE = ROOT / "data" / "tasks" / "state.json"


def now_ms() -> int:
    """返回毫秒时间戳（前端原型的时间字段单位）。"""
    return int(time.time() * 1000)


class RefStore:
    """上传的参考素材：本地副本在应用目录内，同时记录 ComfyUI 里的文件名。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.items: dict[str, dict[str, Any]] = {}

    def add(self, ref_id: str, info: dict[str, Any]) -> None:
        with self._lock:
            self.items[ref_id] = info

    def get(self, ref_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self.items.get(ref_id)

    def remove(self, ref_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self.items.pop(ref_id, None)

    def as_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.items.values())


class AppState:
    """全局状态容器；所有改动都在锁内，避免后台执行线程与请求线程打架。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jobs: list[dict[str, Any]] = []
        self.ui: dict[str, Any] = {
            "capId": "image.generate",
            "route": "studio",
            "adminPage": "caps",
            "galleryFilter": "全部",
            "wizardSeen": False,
            "drafts": {},           # capId -> {prompt, refs:[ref_id], grade}
            "editingWfIndex": 0,
            "gOpen": 0,
            "draft": {"name": "", "values": {}},
        }
        self.refs = RefStore()
        self.comfy_online = False
        self.comfy_queue: dict[str, Any] = {"running": 0, "pending": 0}
        self.progress: dict[str, dict[str, Any]] = {}   # prompt_id -> 进度消息
        self.run_counter = 0
        self.last_error = ""
        self.started_at = now_ms()

    def next_seq(self) -> int:
        """生成全局递增的任务序号（队列按它排序）。"""
        with self.lock:
            self.run_counter += 1
            return self.run_counter

    def next_id(self, prefix: str) -> str:
        """生成短 id（不暴露给最终用户界面，只用于 DOM / 内部关联）。"""
        with self.lock:
            self.run_counter += 1
            return f"{prefix}_{self.run_counter:04d}"

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((j for j in self.jobs if j["id"] == job_id), None)

    def run(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """按 run id 找 (job, run)。"""
        with self.lock:
            for job in self.jobs:
                for run in job.get("runs", []):
                    if run["id"] == run_id:
                        return job, run
        return None

    def all_runs(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        with self.lock:
            return [(j, r) for j in self.jobs for r in j.get("runs", [])]

    def active_runs(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """仍在排队或执行中的任务。"""
        return [(j, r) for j, r in self.all_runs() if r["state"] in ("queued", "running")]

    def persist(self) -> None:
        """把任务与 UI 草稿写入单份 JSON（原子替换，避免重启读到半截文件）。"""
        with self.lock:
            payload = {"jobs": self.jobs, "ui": self.ui, "run_counter": self.run_counter}
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(STATE_FILE)

    def restore(self) -> None:
        """重启后恢复状态：正在跑的任务稍后按 prompt_id 向 ComfyUI 复核。"""
        if not STATE_FILE.exists():
            return
        try:
            payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 坏记录不应阻止启动
            return
        with self.lock:
            self.jobs = payload.get("jobs", []) or []
            self.ui.update(payload.get("ui", {}) or {})
            self.run_counter = int(payload.get("run_counter", 0) or 0)
            for job in self.jobs:
                for run in job.get("runs", []):
                    # 重启后不再直接相信"running"，交给 ComfyUI 队列/历史复核
                    if run.get("state") == "running":
                        run["state"] = "recovering"
                    elif run.get("state") == "queued" and not run.get("prompt_id"):
                        run["state"] = "queued"


state = AppState()
