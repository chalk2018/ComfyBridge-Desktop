"""业务逻辑层：忙闲判定、任务生命周期、进度、归档、管理台、对外接口。

状态判定全部在这里，前端只呈现 + 触发（需求 §0.2）。
普通生成忙了只提示不排队；只有「加入队列生成」会让任务堆到后面。
"""

from __future__ import annotations

import copy
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import yaml

from .comfy import ComfyClient, ComfyError, parse_history_error, read_seed_from_history
from .config import CONFIG_PATH, ROOT, ConfigError, config
from .state import now_ms, state
from .workflows import WorkflowError, flatten_params, pick_workflow, registry

logger = logging.getLogger("comfybridge.services")

RECORD_FILE = ROOT / "data" / "tasks" / "records.jsonl"


def err(code: str, message: str, raw: str = "", short: str = "", retriable: bool = True) -> dict[str, Any]:
    """统一错误结构：页面显示人话，AI 拿结构化 code，原文要展开才看。"""
    return {"code": code, "msg": message, "raw": raw, "short": short or message[:50],
            "retriable": retriable}


def _focus_explorer_window(title_hint: str, timeout: float = 2.0) -> bool:
    """把标题匹配的资源管理器窗口还原并置顶（Windows；失败返回 False）。

    为什么要这么麻烦：`explorer /select,文件` 打开后窗口常常还在浏览器后面，
    用户以为按钮没反应。SetForegroundWindow 本身受限，得先 AttachThreadInput
    把当前线程和目标窗口的输入队列挂一起才有效。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    hint = (title_hint or "").lower()

    def find_window() -> int:
        found: list[tuple[int, str]] = []

        def collect(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            cls = ctypes.create_unicode_buffer(80)
            user32.GetClassNameW(hwnd, cls, 80)
            if cls.value == "CabinetWClass":           # 资源管理器文件夹窗口
                found.append((int(hwnd), buf.value))
            return True

        user32.EnumWindows(callback_type(collect), 0)
        if not found:
            return 0
        exact = [h for h, title in found if hint and title.strip().lower() == hint]
        if exact:
            return exact[-1]
        fuzzy = [h for h, title in found if hint and hint in title.lower()]
        return (fuzzy or [found[-1][0]])[-1]

    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window()
        if hwnd:
            fg = user32.GetForegroundWindow()
            cur_tid = kernel32.GetCurrentThreadId()
            target_tid = user32.GetWindowThreadProcessId(hwnd, None)
            fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            try:
                if target_tid:
                    user32.AttachThreadInput(cur_tid, target_tid, True)
                if fg_tid and fg_tid != target_tid:
                    user32.AttachThreadInput(cur_tid, fg_tid, True)
                user32.ShowWindow(hwnd, 9)             # SW_RESTORE
                brought = bool(user32.SetForegroundWindow(hwnd))
                user32.BringWindowToTop(hwnd)
            finally:
                try:
                    if target_tid:
                        user32.AttachThreadInput(cur_tid, target_tid, False)
                    if fg_tid and fg_tid != target_tid:
                        user32.AttachThreadInput(cur_tid, fg_tid, False)
                except Exception:  # noqa: BLE001
                    pass
            if not brought:
                try:
                    user32.SwitchToThisWindow(hwnd, True)
                except Exception:  # noqa: BLE001
                    pass
            return True
        time.sleep(0.15)
    return False


class TaskService:
    """任务管家 + 配置管理 + 对外接口的服务端实现。"""

    def __init__(self) -> None:
        self.comfy = ComfyClient(on_message=self.on_ws_message)
        self._offline_since: float | None = None
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ 启动
    def start(self) -> None:
        """启动健康检查、WS、执行循环，并按 ComfyUI 队列/历史复核未完成的任务。"""
        config.ensure_dirs()
        registry.ensure_copies()
        state.restore()
        self.cleanup_records()
        self.refresh_health()
        self._recover()
        self.comfy.start_ws()
        for target, name in ((self._health_loop, "health"), (self._execution_loop, "executor")):
            thread = threading.Thread(target=target, name=f"comfybridge-{name}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def _health_loop(self) -> None:
        """顶栏灯与断连兜底：断线 1 分钟内在跑的任务标失败。"""
        while True:
            self.refresh_health()
            if not state.comfy_online:
                if self._offline_since is None:
                    self._offline_since = time.time()
                elif time.time() - self._offline_since > 60:
                    with state.lock:
                        for _job, run in state.all_runs():
                            if run["state"] == "running":
                                run["state"] = "failed"
                                run["t1"] = now_ms()
                                run["err"] = err("COMFY_OFFLINE", "ComfyUI 连接中断，这次没跑完", "连接中断")
                    state.persist()
            else:
                self._offline_since = None
            time.sleep(2.0)

    def refresh_health(self) -> None:
        """刷新 ComfyUI 连通性与它自己的队列状态。"""
        online, _info = self.comfy.health()
        queue = self.comfy.queue_state() if online else {"running": 0, "pending": 0, "ok": False}
        with state.lock:
            state.comfy_online = online
            state.comfy_queue = queue

    def _recover(self) -> None:
        """重启后拿 prompt_id 去 ComfyUI 问：跑完的补取成品，还在跑的继续盯。"""
        for job, run in state.all_runs():
            if run.get("state") != "recovering":
                continue
            prompt_id = run.get("prompt_id")
            if not prompt_id:
                run["state"] = "failed"
                run["err"] = err("EXEC_FAILED", "服务重启时找不到这条任务", "缺少任务编号")
                continue
            entry = self.comfy.history(prompt_id)
            if entry and (entry.get("status") or {}).get("completed"):
                self._finish_success(job, run, entry)
            elif entry and (entry.get("status") or {}).get("status_str") == "error":
                self._finish_error(job, run, parse_history_error(entry))
            else:
                run["state"] = "queued"
                run["resume_only"] = True
        state.persist()

    # ------------------------------------------------------------------ 状态
    def state_payload(self) -> dict[str, Any]:
        """/api/state：页面每次刷新/新开窗口都从这里重新拉全量状态。"""
        with state.lock:
            return {
                "service": {**config.state_service(), "maxQueue": config.service.maxQueue},
                "capabilities": registry.state_capabilities(),
                "jobs": copy.deepcopy(state.jobs),
                "wfLib": registry.state_wf_lib(),
                "ui": copy.deepcopy(state.ui),
                "schema": registry.schema,
                "health": {"online": state.comfy_online, "comfy": config.service.comfy,
                           "queue": copy.deepcopy(state.comfy_queue)},
                "seeded": True,
            }

    def update_ui(self, patch: dict[str, Any]) -> None:
        """保存 UI 草稿（描述、参考图、档位、当前能力），刷新/切页不丢。"""
        with state.lock:
            for key in ("capId", "route", "adminPage", "galleryFilter", "wizardSeen",
                        "drafts", "editingWfIndex", "gOpen", "draft"):
                if key in patch:
                    state.ui[key] = patch[key]
        state.persist()

    def _busy_text(self, exclude: dict[str, Any] | None = None) -> str | None:
        """当前在跑的是谁、跑到几成（被拦下的提交必须说清，X40）。"""
        for job, run in state.all_runs():
            if run is exclude:
                continue
            if run["state"] == "running":
                return f"「{job['capName']} · {run.get('grade') or '默认'}」（{int(run.get('pct', 0))}%）"
            if run["state"] == "queued":
                # 刚创建、还没交给 ComfyUI 的任务也算在跑：连点发送不该变成排队
                return f"「{job['capName']} · {run.get('grade') or '默认'}」（刚提交还没开始）"
        return None

    def check_normal(self, cap_id: str) -> tuple[bool, str]:
        """普通生成准入：ComfyUI 队列必须完全空，否则只提示、不提交、不排队。"""
        if not state.comfy_online:
            return False, "ComfyUI 没连上，请先启动 ComfyUI"
        self.refresh_health()
        if not state.comfy_online:
            return False, "ComfyUI 没连上，请先启动 ComfyUI"
        busy = self._busy_text()
        if busy:
            return False, f"{busy} 还没跑完。等这张出来再点；这次不提交，也不排队。"
        queue = state.comfy_queue
        if queue.get("running") or queue.get("pending"):
            return False, "ComfyUI 正在执行别的任务，等它结束再发送；这次不提交，也不排队。"
        return True, ""

    def check_queue(self, cap_id: str) -> tuple[bool, str]:
        """队列生成准入：无普通任务没跑完、能力匹配、队列未满。"""
        if not state.comfy_online:
            return False, "ComfyUI 没连上，请先启动 ComfyUI"
        all_active = state.active_runs()
        for job, run in all_active:
            if run["mode"] != "queue":
                return False, f"{self._busy_text() or '刚才那张'} 还在跑。它出来以后再点「加入队列生成」。"
            if job["cap"] != cap_id:
                return False, f"队列里跑的是「{job['capName']}」，等它结束才能加图片生成的任务。"
        queued_count = len([1 for _j, r in all_active if r["mode"] == "queue"])
        if queued_count >= config.service.maxQueue:
            return False, f"已经排了 {queued_count} 个，等前面出来几个再加。"
        queue = state.comfy_queue
        known = {r.get("prompt_id") for _j, r in all_active if r.get("prompt_id")}
        if any(pid not in known for pid in queue.get("ids", [])):
            return False, "ComfyUI 正在执行别的任务，等它结束再加队列。"
        return True, ""

    # ------------------------------------------------------------------ 提交
    def _new_run(self, job: dict[str, Any], mode: str, wf_name: str, grade: str) -> dict[str, Any]:
        return {
            "id": state.next_id("run"), "seq": state.next_seq(), "mode": mode,
            "wf": wf_name, "grade": grade or "默认", "seed": None, "media": "image",
            "state": "queued", "pct": 0, "step": 0, "total": 8,
            "t0": now_ms(), "t1": None, "file": "", "files": [],
            "prompt_id": "", "err": None, "archive_failed": None,
            "size": "", "node": "",
        }

    def _resolve_refs(self, ref_ids: list[str]) -> list[dict[str, Any]]:
        return [r for rid in ref_ids if (r := state.refs.get(rid))]

    def submit_normal(
        self, cap_id: str, prompt: str, grade: str, ref_ids: list[str],
        wf: str | None = None,
    ) -> tuple[bool, str, str]:
        """发送 / 再来一张 / 重试：新开一张卡，忙了只提示。返回 (ok, job_id, reason)。

        wf 为前端显式选择的子工作流（如「文生图」/「参考生图」）；若给定且属于该能力，
        优先用它，否则回退到按参考图数量自动挑（pick_workflow）。这样前端既能做显式模式切换，
        也能在没传 wf 时保持旧行为。
        """
        cap = config.capability(cap_id)
        if not cap or not cap.get("enabled") or not cap.get("workflows"):
            return False, "", "这个能力还没配置工作流"
        if not prompt.strip():
            return False, "", "写点描述再发送"
        refs = self._resolve_refs(ref_ids)
        max_refs = int(cap.get("maxRefs", 3) or 3)
        if len(refs) > max_refs:
            return False, "", f"这个能力最多 {max_refs} 张参考图"
        if cap.get("requiredRef") and not refs:
            return False, "", "这个能力需要参考图"
        # 显式工作流优先：仅当它确实属于这个能力时才采用，避免伪造/越权选择
        wf_name: str | None = None
        if wf and wf in (cap.get("workflows") or []):
            wf_name = wf
            why = "前端显式选择"
        else:
            wf_name, why = pick_workflow(cap, len(refs))
        if not wf_name:
            return False, "", "这种情况还没有配置对应的工作流"
        wf = config.workflow(wf_name) or {}
        grades = [g["name"] for g in wf.get("grades", [])] or ["默认"]
        if grade not in grades:
            grade = grades[0]
        ok, reason = self.check_normal(cap_id)
        if not ok:
            return False, "", reason
        job = {
            "id": state.next_id("job"), "cap": cap_id, "capName": cap["name"],
            "capSnapshot": {"id": cap_id, "name": cap["name"]},
            "wf": wf_name, "why": why, "prompt": prompt.strip(),
            "refs": [r["id"] for r in refs], "t0": now_ms(), "seed": None, "runs": [],
            # 记下参考图的本地副本：ref id 只活在内存里（重启就没了），
            # 「基于上一条」要能把图片原样带回来，只能靠这些落盘路径重新登记
            "refsInfo": [
                {"path": r.get("path", ""), "name": r.get("name", "")} for r in refs
            ],
        }
        job["runs"].append(self._new_run(job, "normal", wf_name, grade))
        with state.lock:
            state.jobs.append(job)
        state.persist()
        return True, job["id"], ""

    def submit_queue(self, job_id: str, grade: str) -> tuple[bool, str]:
        """加入队列生成：接到同一张卡后面，沿用这一批的种子。"""
        job = state.job(job_id)
        if not job:
            return False, "找不到这张卡片"
        base = job["runs"][0] if job["runs"] else None
        if not base or base["state"] != "done":
            return False, "等第一张出来再往这批里加"
        ok, reason = self.check_queue(job["cap"])
        if not ok:
            return False, reason
        wf = config.workflow(job["wf"]) or {}
        grades = [g["name"] for g in wf.get("grades", [])] or ["默认"]
        if grade not in grades:
            grade = grades[0]
        run = self._new_run(job, "queue", base["wf"], grade)
        with state.lock:
            job["runs"].append(run)
        state.persist()
        return True, ""

    def repeat(self, job_id: str, retry: bool = False) -> tuple[bool, str, str]:
        """再来一张 / 重试一次：都是普通生成，参数不变、新开一张卡。"""
        job = state.job(job_id)
        if not job or not job["runs"]:
            return False, "", "找不到这张卡片"
        run = job["runs"][0]
        return self.submit_normal(job["cap"], job["prompt"], run["grade"], job["refs"])

    def cancel(self, run_id: str) -> bool:
        """取消排队中/生成中的任务，并通知 ComfyUI 真正停下。"""
        found = state.run(run_id)
        if not found:
            return False
        _job, run = found
        with state.lock:
            run["state"] = "canceled"
            run["t1"] = now_ms()
        pid = run.get("prompt_id")
        if pid:
            self.comfy.delete(pid)
            if run.get("state") == "running":
                self.comfy.interrupt()
        state.persist()
        return True

    # ------------------------------------------------------------------ 执行
    def _execution_loop(self) -> None:
        """按 seq 顺序一次跑一个；队列任务也在这里一个个接上。"""
        while True:
            candidates = [(j, r) for j, r in state.all_runs() if r["state"] == "queued"]
            if not candidates:
                time.sleep(0.35)
                continue
            job, run = min(candidates, key=lambda pair: pair[1]["seq"])
            try:
                self._execute(job, run)
            except Exception as exc:  # noqa: BLE001 - 单条任务出错不能拖垮服务
                logger.exception("task failed")
                with state.lock:
                    run["state"] = "failed"
                    run["t1"] = now_ms()
                    run["err"] = err("EXEC_FAILED", "这次没跑成", str(exc))
                state.persist()

    def _execute(self, job: dict[str, Any], run: dict[str, Any]) -> None:
        """提交一次工作流并盯到出片/失败/取消。"""
        with state.lock:
            run["state"] = "running"
            run["t0"] = now_ms()
            run["pct"] = max(1, run.get("pct", 0) or 0)
        state.persist()
        try:
            resume_only = bool(run.get("resume_only"))
            if not resume_only:
                refs = self._resolve_refs(job.get("refs", []))
                # 普通生成：等价于 ComfyUI 前端 control_after_generate=randomize；
                # 队列生成沿用首发回读到的真实种子（X42）。
                seed = job.get("seed") if run["mode"] == "queue" and job.get("seed") else secrets.randbelow(2**62)
                prefix = f"ComfyBridge_{job['id']}_{run['seq']}"
                prompt = registry.build_prompt(run["wf"], run["grade"], job["prompt"], refs, seed, prefix)
                # 记下这一档真正提交到 ComfyUI 的参数，页面上可核对「改了有没有生效」
                applied = registry.grade_effective(run["wf"], run["grade"])
                prompt_id = self.comfy.submit(prompt)
                with state.lock:
                    run["prompt_id"] = prompt_id
                    run["seed"] = seed
                    run["applied"] = applied["applied"]
                    run["sizeExpected"] = applied["size"]["label"]
                state.persist()
            else:
                with state.lock:
                    run.pop("resume_only", None)
            prompt_id = run["prompt_id"]
            while True:
                time.sleep(0.8)
                with state.lock:
                    if run["state"] == "canceled":
                        return
                entry = self.comfy.history(prompt_id)
                if entry:
                    status = entry.get("status") or {}
                    if status.get("completed") or status.get("status_str") == "success":
                        self._finish_success(job, run, entry)
                        return
                    if status.get("status_str") == "error":
                        self._finish_error(job, run, parse_history_error(entry))
                        return
                if resume_only and not self.comfy.queue_contains(prompt_id):
                    self._finish_error(job, run, err("EXEC_FAILED", "服务重启后 ComfyUI 里找不到这条任务", "队列/历史均无记录"))
                    return
        except ComfyError as exc:
            self._finish_error(job, run, err(exc.code, exc.message, exc.raw))
        except WorkflowError as exc:
            self._finish_error(job, run, err("BAD_GRADE", "这个工作流现在发不出去", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self._finish_error(job, run, err("EXEC_FAILED", "这次没跑成", str(exc)))

    def on_ws_message(self, message: dict[str, Any]) -> None:
        """把 ComfyUI 的进度消息翻译成第几步 / 百分比。"""
        kind, data = message.get("type", ""), message.get("data", {})
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            return
        with state.lock:
            state.progress[prompt_id] = {"type": kind, "data": data}
            for _job, run in state.all_runs():
                if run.get("prompt_id") != prompt_id or run["state"] != "running":
                    continue
                if kind == "progress":
                    value, maximum = data.get("value"), data.get("max")
                    if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
                        run["step"] = int(value)
                        run["total"] = int(maximum)
                        run["pct"] = min(99, round(value / maximum * 100))
                elif kind == "executing":
                    run["node"] = str(data.get("node") or "")
                elif kind == "execution_cached":
                    run["pct"] = max(run.get("pct", 0), 5)
                elif kind == "execution_error":
                    run["error_hint"] = str(data.get("exception_message", ""))[:200]

    def _finish_success(self, job: dict[str, Any], run: dict[str, Any], entry: dict[str, Any]) -> None:
        """取成品 → 回读种子 → 归档 → 写任务记录。"""
        outputs = entry.get("outputs", {}) or {}
        bridge = registry.bridge(run["wf"])
        files: list[dict[str, Any]] = []
        for node_id in bridge.get("output_nodes", []):
            for item in (outputs.get(node_id, {}) or {}).get("images", []) or []:
                filename = item.get("filename")
                if not filename:
                    continue
                info = {"filename": filename, "subfolder": item.get("subfolder", "") or "",
                        "type": item.get("type", "output") or "output"}
                info["width"] = (item.get("metadata") or {}).get("width")
                info["height"] = (item.get("metadata") or {}).get("height")
                self._archive(job, run, info)
                files.append(info)
        seed = read_seed_from_history(entry) or run.get("seed") or job.get("seed")
        with state.lock:
            run["state"] = "done"
            run["pct"] = 100
            run["t1"] = now_ms()
            run["files"] = files
            run["file"] = files[0]["filename"] if files else ""
            run["seed"] = seed
            if files and files[0].get("width") and files[0].get("height"):
                run["size"] = f"{files[0]['width']}×{files[0]['height']}"
            if not job.get("seed"):
                job["seed"] = seed
            for sibling in job["runs"]:
                sibling["seed"] = seed
        state.persist()
        self._write_record(job, run)

    def _archive(self, job: dict[str, Any], run: dict[str, Any], info: dict[str, Any]) -> None:
        """只把本次生成的文件移动到 <成果目录>/<能力名>/ 下（X11/X12）。

        归档失败不算任务失败：成品照常可看可下载，卡片上加一行黄标说明。
        """
        src = Path(config.service.comfyOutputDir).resolve() / info["subfolder"] / info["filename"]
        dst_dir = Path(config.service.archiveDir).resolve() / job["capName"]
        info["path"] = str(src)
        info["archived_path"] = ""
        try:
            if not src.exists():
                raise FileNotFoundError(f"原文件不在 output 目录：{src}")
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / info["filename"]
            if dst.exists():
                stem, suffix = Path(info["filename"]).stem, Path(info["filename"]).suffix
                dst = dst_dir / f"{stem}_{run['seq']}{suffix}"
                info["filename"] = dst.name
            shutil.move(str(src), str(dst))
            info["path"] = str(dst)
            info["archived_path"] = str(dst)
        except Exception as exc:  # noqa: BLE001
            run["archive_failed"] = {"reason": str(exc), "original": str(src)}
            info["archive_failed"] = str(exc)

    def _finish_error(self, job: dict[str, Any], run: dict[str, Any], error: dict[str, Any]) -> None:
        """失败卡片：一行人话 + 原始报错（展开才看）+ 重试一次。"""
        with state.lock:
            run["state"] = "failed"
            run["t1"] = now_ms()
            run["pct"] = 0
            run["err"] = error
        state.persist()
        self._write_record(job, run)

    def _write_record(self, job: dict[str, Any], run: dict[str, Any]) -> None:
        """任务记录字段齐：时间、能力、档位、工作流、prompt_id、结果、耗时、落盘路径。"""
        record = {
            "time": now_ms(), "capability": job.get("capSnapshot", {}),
            "grade": run.get("grade"), "workflow": run.get("wf"),
            "prompt_id": run.get("prompt_id"), "result": run["state"],
            "duration_ms": (run.get("t1") or now_ms()) - (run.get("t0") or now_ms()),
            "seed": run.get("seed"), "paths": [f.get("path") for f in run.get("files", [])],
            "error": (run.get("err") or {}).get("msg"),
        }
        try:
            RECORD_FILE.parent.mkdir(parents=True, exist_ok=True)
            with RECORD_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            logger.debug("write record failed", exc_info=True)

    # ------------------------------------------------------------------ 文件
    def file_for_run(self, run_id: str, index: int = 0) -> dict[str, Any] | None:
        """取文件只允许访问"这次任务产出的那几个文件"（目录穿越防护）。"""
        found = state.run(run_id)
        if not found:
            return None
        _job, run = found
        files = run.get("files", []) or []
        if index < 0 or index >= len(files):
            return None
        info = files[index]
        if info.get("deleted"):
            return None
        path = Path(info.get("path", ""))
        if not path.exists() or not path.is_file():
            return None
        return info

    def build_zip(self, selections: list[tuple[str, int]]) -> bytes:
        """历史页批量下载：把选中的成品打成一个 zip（内存里，不落盘）。"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for run_id, index in selections:
                info = self.file_for_run(run_id, index)
                if info:
                    path = Path(info["path"])
                    archive.write(path, arcname=path.name)
        return buffer.getvalue()

    def open_folder(self, target: str, select: bool = True) -> tuple[bool, str, dict[str, Any]]:
        """在本机资源管理器里打开成果（文件就选中它），并把窗口拉到最前面。

        为什么不用 os.startfile：它只会打开目录，且窗口可能还盖在浏览器后面，
        用户点了「打开所在目录」却看不见任何反应。这里用 explorer /select, 把文件选中，
        再用 Win32 API 把窗口还原 + 置顶（尽力而为，失败也照常返回成功）。
        """
        root = Path(config.service.archiveDir).resolve()
        path = Path(target).resolve()
        in_archive = path.is_relative_to(root)
        in_output = path.is_relative_to(Path(config.service.comfyOutputDir).resolve())
        if not (in_archive or in_output) or not path.exists():
            return False, "只能打开成果目录里的文件", {}
        folder = path if path.is_dir() else path.parent
        try:
            if sys.platform == "win32":
                if path.is_dir():
                    subprocess.Popen(["explorer", str(path)])
                else:
                    # /select 让资源管理器打开父目录并选中这个文件
                    subprocess.Popen(f'explorer /select,"{path}"')
                focused = _focus_explorer_window(folder.name)
                return True, "", {"focused": focused, "folder": str(folder)}
            os.startfile(str(folder))  # type: ignore[attr-defined]
            return True, "", {"focused": False, "folder": str(folder)}
        except Exception as exc:  # noqa: BLE001
            return False, str(exc), {}

    # ------------------------------------------------------------------ 删除
    def _is_generated_path(self, path: Path) -> bool:
        """只允许删除本应用产出的文件（成果目录 / ComfyUI output 内），防目录穿越。"""
        resolved = path.resolve()
        roots = [Path(config.service.archiveDir).resolve(),
                 Path(config.service.comfyOutputDir).resolve()]
        return any(resolved.is_relative_to(root) for root in roots)

    def _trash_dir(self) -> Path:
        return config.resolve(config.service.uploadDir).parent / "trash"

    def _remove_file(self, path: Path) -> str:
        """删文件；删不掉就挪进本地回收站。返回 'deleted' / 'trashed' / 'blocked'。

        为什么不用一句 unlink 了事：文件可能被杀毒/看图软件占着，也可能被安全策略拦下，
        这时**绝不能把整个批量删除变成 500**——用户的其它选择就白删了。
        挪进 data/trash 既能保住「批量删除成功」的语义，也还能手动捞回来。
        """
        try:
            path.unlink()
            return "deleted"
        except FileNotFoundError:
            return "deleted"
        except BaseException:  # noqa: BLE001 - 占用/权限/策略拦截都走回收站
            pass
        try:
            trash = self._trash_dir()
            trash.mkdir(parents=True, exist_ok=True)
            dest = trash / f"{int(time.time() * 1000)}_{path.name}"
            shutil.move(str(path), str(dest))
            return "trashed"
        except BaseException:  # noqa: BLE001
            return "blocked"

    def delete_history(self, items: list[dict[str, Any]], delete_files: bool = True) -> tuple[bool, str, dict[str, Any]]:
        """历史页删除：删掉选中成品的本地文件，并从历史与对话卡片里移除。

        只认 state 里登记过的 run/file，路径还必须在成果目录或 ComfyUI output 内。
        返回 (ok, reason, {files, runs})。
        """
        removed_files = 0
        trashed = 0
        blocked = 0
        hidden_runs: set[str] = set()
        with state.lock:
            for item in items:
                run_id = str(item.get("run", ""))
                try:
                    index = int(item.get("index", 0) or 0)
                except (TypeError, ValueError):
                    continue
                found = state.run(run_id)
                if not found:
                    continue
                _job, run = found
                files = run.get("files", []) or []
                if index < 0 or index >= len(files):
                    continue
                info = files[index]
                if info.get("deleted"):
                    continue
                path = Path(str(info.get("path", "")))
                if delete_files and path.exists() and self._is_generated_path(path):
                    outcome = self._remove_file(path)
                    if outcome == "deleted":
                        removed_files += 1
                    elif outcome == "trashed":
                        removed_files += 1
                        trashed += 1
                    else:
                        blocked += 1       # 删不掉也照样从历史里摘掉，不留破图
                info["deleted"] = True
                # 一张成品都没了，这张卡就不该继续显示破图
                if all(f.get("deleted") for f in files):
                    run["hidden"] = True
                    hidden_runs.add(run_id)
            # 一张成品都不剩的卡片不该留在主页面当空壳；但文件已删，历史页也不会再列
            for job in state.jobs:
                runs = job.get("runs", []) or []
                if runs and all(r.get("hidden") for r in runs):
                    job["sessionRemoved"] = True
            # 会话里已删掉、成品也删光的任务，整条清掉
            state.jobs[:] = [
                job for job in state.jobs
                if not (job.get("sessionRemoved") and all(
                    r.get("hidden") or not (r.get("files") or []) for r in job.get("runs", [])
                ))
            ]
        state.persist()
        return True, "", {"files": removed_files, "runs": len(hidden_runs),
                          "trashed": trashed, "blocked": blocked}

    def delete_session_job(self, job_id: str) -> tuple[bool, str]:
        """会话任务删除：只移走右栏与主页面的这条对话记录，成品文件一律保留。

        成品文件只能从历史记录页删除（需求口径）。历史页不过滤这个标记，
        所以移走对话后仍能在历史里管理/删除这些文件。
        """
        job = state.job(job_id)
        if not job:
            return False, "找不到这条任务"
        if any(r.get("state") in ("queued", "running", "recovering") for r in job.get("runs", [])):
            return False, "这条还在跑，等它结束再删"
        with state.lock:
            job["sessionRemoved"] = True
        state.persist()
        return True, ""

    def delete_session_jobs(self, job_ids: list[str]) -> tuple[bool, str, int]:
        """批量删除会话任务：逐条调用 delete_session_job，跳过正在跑的。返回 (ok, reason, 成功数)。"""
        ok_count = 0
        skipped = 0
        for job_id in job_ids:
            job = state.job(job_id)
            if not job:
                skipped += 1
                continue
            if any(r.get("state") in ("queued", "running", "recovering") for r in job.get("runs", [])):
                skipped += 1
                continue
            with state.lock:
                job["sessionRemoved"] = True
            ok_count += 1
        state.persist()
        reason = "" if skipped == 0 else f"有 {skipped} 条正在跑被跳过"
        return True, reason, ok_count

    # ------------------------------------------------------------------ 参考图复用
    def restore_refs(self, job_id: str) -> tuple[bool, str, list[dict[str, Any]]]:
        """把某条任务的参考图重新登记一遍（「基于上一条」用）。返回 (ok, reason, refs)。

        为什么不用旧 ref id：RefStore 只在内存里，服务重启就空了；而参考图必须
        能原样带回来（用户在意的是「图片有没有引过来」），所以按任务记录里的
        本地副本重新落一份 + 重新交给 ComfyUI，拿一组新的 ref id 更可靠。
        """
        job = state.job(job_id)
        if not job:
            return False, "找不到这条任务", []
        uploads = config.resolve(config.service.uploadDir)
        uploads.mkdir(parents=True, exist_ok=True)
        out: list[dict[str, Any]] = []
        for info in job.get("refsInfo") or []:
            src = Path(str(info.get("path") or ""))
            if not src.is_file():
                continue            # 文件被手动清掉了，跳过（前端会提示少了几张）
            ref_id = state.next_id("ref")
            filename = f"{ref_id}{src.suffix.lower() or '.png'}"
            dest = uploads / filename
            try:
                shutil.copyfile(src, dest)
            except OSError:
                continue
            try:
                comfy_name = self.comfy.upload_image(dest, filename)
            except ComfyError:
                comfy_name = filename       # ComfyUI 没开也先把图挂上，等它起来再说
            ref = {"id": ref_id, "kind": "image", "name": info.get("name") or filename,
                   "filename": filename, "path": str(dest), "comfyName": comfy_name}
            state.refs.add(ref_id, ref)
            out.append({"id": ref_id, "kind": "image", "name": ref["name"]})
        if not out and job.get("refs"):
            return False, "这条任务的参考图已经被清理，只能带文字描述", []
        return True, "", out

    def prune_refs(self, protect: list[str], keep: int) -> int:
        """按需清理参考图缓存，返回清掉的数量。

        为什么要清：参考图不能一移除就从输入框里删文件（要留给「基于上一条」），
        可上传目录也不能无限涨。策略是「正在编辑的 + 最近任务的」保留，其余先清。
        """
        protected = set(protect)
        with state.lock:
            for job in state.jobs:
                protected.update(job.get("refs") or [])
        items = state.refs.as_list()
        removed = 0
        for ref in items:
            if len(state.refs.as_list()) < keep:
                break
            if ref.get("id") in protected:
                continue
            path = Path(str(ref.get("path") or ""))
            outcome = self._remove_file(path) if str(ref.get("path") or "") else "deleted"
            if outcome == "blocked":
                continue          # 文件动不了就先留着登记，下次再试；绝不因此让上传 500
            state.refs.remove(ref["id"])
            removed += 1
        return removed

    # ------------------------------------------------------------------ 管理台
    def storage_info(self) -> dict[str, Any]:
        """管理台「当前占用」：各目录文件数与大小（需求 04 章上传目录塞满）。"""
        dirs = {
            "upload": config.resolve(config.service.uploadDir),
            "tasks": config.resolve(config.service.taskDir),
            "copies": config.resolve(config.service.copyDir),
            "archive": Path(config.service.archiveDir).resolve(),
        }
        out: dict[str, Any] = {}
        for key, path in dirs.items():
            count, size = 0, 0
            if path.exists() and path.is_dir():
                for item in path.rglob("*"):
                    if not item.is_file():
                        continue
                    count += 1
                    try:
                        size += item.stat().st_size
                    except OSError:
                        pass
            out[key] = {"path": str(path), "files": count, "bytes": size,
                        "text": f"{count} 个文件 · {size / 1024 / 1024:.1f} MB"}
        return out

    def cleanup_records(self) -> dict[str, Any]:
        """按 keepDays / maxRecords 清理任务记录；只清记录，不删成品（需求 §2.4）。"""
        if not RECORD_FILE.exists():
            return {"removed": 0, "kept": 0}
        cutoff = now_ms() - config.service.keepDays * 86400000
        rows: list[dict[str, Any]] = []
        original = 0
        for line in RECORD_FILE.read_text(encoding="utf-8").splitlines():
            original += 1
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if row.get("time", 0) >= cutoff:
                rows.append(row)
        rows = rows[-config.service.maxRecords:]
        RECORD_FILE.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
        )
        return {"removed": max(0, original - len(rows)), "kept": len(rows)}

    def config_raw(self) -> str:
        """返回 config.yaml 原文，供页面在配置损坏时直接修改。"""
        return CONFIG_PATH.read_text(encoding="utf-8")

    def save_config_raw(self, text: str) -> tuple[bool, str]:
        """页面直接改配置：先校验语法再写回，不自动替换用户内容。"""
        try:
            parsed = yaml.safe_load(text)
            if not isinstance(parsed, dict):
                return False, "配置内容不是一个完整的配置表"
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f"第 {mark.line + 1} 行" if mark is not None else "未知位置"
            return False, f"{where} 有问题：{getattr(exc, 'problem', None) or exc}"
        CONFIG_PATH.write_text(text, encoding="utf-8")
        accepted, reason = self.reload_config()
        if not accepted:
            return False, reason
        state.last_error = ""
        return True, ""

    def save_config(self, patch: dict[str, Any]) -> tuple[bool, str]:
        """服务设置保存：写回 config.yaml 并立即在内存生效。"""
        try:
            config.save_service(patch)
            self.refresh_health()
            self.cleanup_records()
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def reload_config(self) -> tuple[bool, str]:
        """重读配置（配置坏掉时明确指出问题，不自动替换）。"""
        try:
            config.load()
            registry.invalidate()
            registry._bridges.clear()
            # 强制按新配置重建所有档位拷贝：以前只补「不存在的」，
            # 手改 config.yaml 后旧参数会残留，导致页面显示改了、出图没变。
            registry.rebuild_all_copies()
            self.refresh_health()
            self.cleanup_records()
            state.last_error = ""
            return True, ""
        except Exception as exc:  # noqa: BLE001
            state.last_error = str(exc)
            return False, str(exc)

    def capability_action(self, action: str, cap_id: str) -> tuple[bool, str]:
        """能力启停 / 复制 / 删除（都写回配置文件）。"""
        raw = copy.deepcopy(config.raw)
        caps = raw.get("capabilities", [])
        index = next((i for i, c in enumerate(caps) if c["id"] == cap_id), -1)
        if index < 0:
            return False, "找不到这个能力"
        cap = caps[index]
        if action == "toggle":
            if not cap.get("enabled") and not cap.get("workflows"):
                return False, "这个能力还没配工作流，开不了"
            cap["enabled"] = not cap.get("enabled", False)
        elif action == "copy":
            clone = copy.deepcopy(cap)
            clone["id"] = f"{cap_id}.copy{secrets.randbelow(90) + 10}"
            clone["name"] = f"{cap['name']}（副本）"
            caps.append(clone)
        elif action == "delete":
            caps.pop(index)
        else:
            return False, "不支持的操作"
        try:
            config.replace(raw)
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def update_workflow(self, wf_name: str, patch: dict[str, Any]) -> tuple[bool, str]:
        """勾节点 / 改名 / 改说明，写回配置。"""
        raw = copy.deepcopy(config.raw)
        wf = (raw.get("workflows") or {}).get(wf_name)
        if not wf:
            return False, "找不到这个工作流"
        for key in ("fields", "status"):
            if key in patch:
                wf[key] = patch[key]
        renamed_from = ""
        if "gradePatch" in patch:
            gp = patch["gradePatch"]
            # name 只用于定位，newName 才是新名字；否则改名会让定位失败
            grade = next((g for g in wf.get("grades", []) if g["name"] == gp.get("name")), None)
            if not grade or grade.get("locked"):
                return False, "默认档不能改"
            old_name = grade["name"]
            new_name = str(gp.get("newName") or old_name).strip()
            if not new_name:
                return False, "档位名不能为空"
            if new_name != old_name and any(g["name"] == new_name for g in wf.get("grades", [])):
                return False, "这个名字已经有一档了"
            for key in ("note", "params"):
                if key in gp:
                    grade[key] = gp[key]
            grade["name"] = new_name
            if new_name != old_name:
                renamed_from = old_name
        try:
            config.replace(raw)
            if renamed_from:
                old_copy = registry.grade_copy_path(wf_name, renamed_from)
                if old_copy.exists():
                    old_copy.unlink()
                registry.invalidate(old_copy)
            for grade in wf.get("grades", []):
                if not grade.get("locked"):
                    registry.rebuild_copy(wf_name, grade)
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def add_grade(self, wf_name: str, name: str, params: dict[str, Any], note: str = "") -> tuple[bool, str]:
        """添加一档 = 复制一份工作流，把这档的值写进拷贝（X34 / X35）。"""
        raw = copy.deepcopy(config.raw)
        wf = (raw.get("workflows") or {}).get(wf_name)
        if not wf:
            return False, "找不到这个工作流"
        name = (name or "").strip()
        if not name:
            return False, "先给这一档起个名字"
        if any(g["name"] == name for g in wf.get("grades", [])):
            return False, "这个名字已经有一档了，换一个"
        wf.setdefault("grades", []).append({"name": name, "note": note, "params": params or {}})
        try:
            config.replace(raw)
            registry.rebuild_copy(wf_name, wf["grades"][-1])
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def delete_grade(self, wf_name: str, name: str) -> tuple[bool, str]:
        """删掉一档，连它的工作流拷贝一起作废；默认档删不掉。"""
        raw = copy.deepcopy(config.raw)
        wf = (raw.get("workflows") or {}).get(wf_name)
        if not wf:
            return False, "找不到这个工作流"
        grade = next((g for g in wf.get("grades", []) if g["name"] == name), None)
        if not grade or grade.get("locked"):
            return False, "默认档删不掉"
        wf["grades"] = [g for g in wf["grades"] if g["name"] != name]
        try:
            config.replace(raw)
            path = registry.grade_copy_path(wf_name, name)
            if path.exists():
                path.unlink()
            registry.invalidate(path)
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def reorder_grades(self, wf_name: str, order: list[str]) -> tuple[bool, str]:
        """按给定顺序重排非默认档；默认档（locked）固定在首位。

        创作台的档位按钮顺序直接来自这里的配置顺序，所以拖拽后创作台跟着变。
        只动顺序，不动任何档位的参数/拷贝文件。
        """
        raw = copy.deepcopy(config.raw)
        wf = (raw.get("workflows") or {}).get(wf_name)
        if not wf:
            return False, "找不到这个工作流"
        grades = wf.get("grades") or []
        locked = [g for g in grades if g.get("locked")]
        movable = {g["name"]: g for g in grades if not g.get("locked")}
        seen: set[str] = set()
        new_movable: list[dict[str, Any]] = []
        for name in order or []:
            if name in movable and name not in seen:
                new_movable.append(movable[name])
                seen.add(name)
        for g in grades:                                   # order 里漏掉的补在后面
            if not g.get("locked") and g["name"] not in seen:
                new_movable.append(g)
        wf["grades"] = locked + new_movable
        try:
            config.replace(raw)
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def diag(self) -> list[dict[str, Any]]:
        """自检只有两项：连不连得上、成果目录写不写得进去。"""
        online, _info = self.comfy.health()
        rows = [{"ok": online, "name": "ComfyUI 连接" if online else "ComfyUI 未连接",
                 "detail": (config.service.comfy + " · 队列空闲" if online else "请先启动 ComfyUI")}]
        try:
            target = Path(config.service.archiveDir).resolve()
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            rows.append({"ok": True, "name": "成果目录可写", "detail": str(target)})
        except Exception as exc:  # noqa: BLE001
            rows.append({"ok": False, "name": "成果目录写不进去", "detail": str(exc)})
        return rows

    # ------------------------------------------------------------------ 对外
    def ai_capabilities(self) -> dict[str, Any]:
        """看菜单：只列可用能力；1.01 只有 image.generate 一条。"""
        caps = []
        for cap in config.raw.get("capabilities", []):
            if not cap.get("enabled") or not cap.get("workflows"):
                continue
            grades = []
            for name in cap["workflows"]:
                wf = config.workflow(name) or {}
                grades.extend(g["name"] for g in wf.get("grades", []))
            caps.append({
                "id": cap["id"], "name": cap["name"], "description": cap.get("desc", ""),
                "grades": sorted(set(grades)), "max_reference_images": cap.get("maxRefs", 0),
                "typical_seconds": 20,
            })
        return {"capabilities": caps}

    def ai_order(self, body: dict[str, Any]) -> dict[str, Any]:
        """下单：永远对应普通生成，忙时直接回 busy，服务端不替 AI 排队。"""
        cap_id = body.get("capability") or "image.generate"
        ok, job_id, reason = self.submit_normal(
            cap_id, str(body.get("prompt", "")), str(body.get("grade", "默认")),
            list(body.get("references", []) or []),
        )
        return {"accepted": ok, "job_id": job_id, "reason": reason, "poll_after": 3}

    def ai_status(self, job_id: str) -> dict[str, Any]:
        """取货：状态 / 进度 / 成品地址 / 失败原因。"""
        job = state.job(job_id)
        if not job:
            return {"state": "not_found", "message": "没有这个任务编号"}
        runs = job["runs"]
        run = runs[-1]
        result = {
            "job_id": job_id, "state": run["state"], "progress": run.get("pct", 0),
            "capability": job["cap"], "grade": run.get("grade"), "seed": job.get("seed"),
            "prompt_id": run.get("prompt_id"),
        }
        if run["state"] == "done":
            result["files"] = [
                {"index": i, "url": f"/api/v1/files/{r['id']}/{i}", "filename": f["filename"]}
                for r in runs for i, f in enumerate(r.get("files", []))
            ]
        if run.get("err"):
            result["error"] = run["err"]
        return result


service = TaskService()
