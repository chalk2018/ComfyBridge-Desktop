"""服务端唯一状态：任务、UI 草稿、上传引用、ComfyUI 健康度。

需求 §0.2：状态只有一份，在服务端；页面刷新/新开窗口都重新拉取。
这里用内存 dict + 单份 JSON 文件持久化（不引入数据库，需求 X9）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import ROOT, config

STATE_FILE = ROOT / "data" / "tasks" / "state.json"
REF_FILE = ROOT / "data" / "refs.json"
_REF_NAME = re.compile(r"^(ref_\d+)(\.[A-Za-z0-9]+)$")
_VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


def _kind_of(path: Path) -> str:
    """按扩展名判素材类型（扫描补索引时用，上传路径另有 MIME 判断）。"""
    ext = path.suffix.lower()
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _AUDIO_EXT:
        return "audio"
    return "image"


def now_ms() -> int:
    """返回毫秒时间戳（前端原型的时间字段单位）。"""
    return int(time.time() * 1000)


class RefStore:
    """上传的参考素材：本地副本在应用目录内，同时记录 ComfyUI 里的文件名。

    索引落盘到 data/refs.json。以前只放内存，服务一重启 ref_xxx 全部 404，
    画布上的资产节点整片破图（文件其实还在 uploads 里）；索引丢了也能按文件名重建。
    """

    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path = Path(path) if path else REF_FILE
        self.items: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        items = raw.get("items") if isinstance(raw, dict) else None
        out: dict[str, dict[str, Any]] = {}
        for ref_id, info in (items or {}).items():
            if not isinstance(info, dict):
                continue
            p = Path(str(info.get("path") or ""))
            if p.is_file():                 # 文件被手动删掉的条目直接丢掉，别一直 404
                out[str(ref_id)] = info
        return out

    def _save(self) -> None:
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"items": self.items}, ensure_ascii=False, indent=1),
                               encoding="utf-8")
                tmp.replace(self._path)
            except OSError:
                pass

    def add(self, ref_id: str, info: dict[str, Any]) -> None:
        with self._lock:
            self.items[ref_id] = info
            self._save()

    def get(self, ref_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self.items.get(ref_id)

    def remove(self, ref_id: str) -> dict[str, Any] | None:
        with self._lock:
            gone = self.items.pop(ref_id, None)
            if gone:
                self._save()
            return gone

    def as_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.items.values())

    def scan_dir(self, folder: Path) -> int:
        """按 uploads 里的 ref_xxx.ext 补回索引；返回补齐条数。

        上传文件名就是 ref id，所以即便 refs.json 没了（或从旧版本升级上来），
        也能把画布上的旧素材重新变成可访问的 URL。
        """
        added = 0
        try:
            files = list(folder.iterdir())
        except OSError:
            return 0
        with self._lock:
            for f in files:
                m = _REF_NAME.match(f.name)
                if not m or not f.is_file():
                    continue
                ref_id = m.group(1)
                if ref_id in self.items:
                    continue
                self.items[ref_id] = {
                    "id": ref_id, "kind": _kind_of(f), "name": f.name,
                    "filename": f.name, "path": str(f), "comfyName": f.name,
                }
                added += 1
            if added:
                self._save()
        return added


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
            # 1.06 提示词模板库：浮动小窗开关与位置（只在创作台显示）
            "promptDock": False,
            "promptDockPos": None,
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
