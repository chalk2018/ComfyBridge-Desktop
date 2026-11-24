"""业务逻辑层：忙闲判定、任务生命周期、进度、归档、管理台、对外接口。

状态判定全部在这里，前端只呈现 + 触发（需求 §0.2）。
普通生成忙了只提示不排队；只有「加入队列生成」会让任务堆到后面。
"""

from __future__ import annotations

import copy
import base64
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
from .promptlib import SheetError, library as sheet_library
from .state import now_ms, state
from . import audiolib, videolib
from .workflows import WorkflowError, flatten_params, pick_workflow, registry

# 故事板 0 张参考图时给 LoadImage 的占位图文件名（固定名，上传目录里只存一份）
BLANK_IMAGE_NAME = "_board_blank_ref.png"

# ComfyUI 各种输出节点把成品塞在不同的键下：SaveImage 是 images、SaveAudio 是 audio、
# 老式 SaveVideo 是 gifs。视频类现在也走 images（带 metadata.kind=videos）。
OUTPUT_KEYS = ("images", "audio", "gifs", "videos", "video")
MEDIA_SUFFIX = {
    ".mp4": "video", ".webm": "video", ".mov": "video", ".mkv": "video",
    ".mp3": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio", ".m4a": "audio",
}

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
        try:
            sheet_lib = sheet_library.view()
        except SheetError:
            # 提示词库坏了不能连累整个创作台：给空库，页面上按「库读不到」提示
            sheet_lib = {}
        try:
            from . import imagestylelib
            image_style_lib = imagestylelib.library.view()
        except Exception:  # noqa: BLE001 - 通用模板库坏了也要让创作台可用
            from . import imagestylelib
            image_style_lib = {"line_styles": [], "styles": []}
        try:
            from . import combos
            sheet_combos = combos.view()
        except Exception:  # noqa: BLE001 - 组合同样不能拖垮页面
            sheet_combos = []
        with state.lock:
            from . import extsheets as ext
            from . import boardlib as board
            ext_libs, ext_combos = {}, {}
            for _kind in (ext.SCENE, ext.PROP):
                try:
                    ext_libs[_kind] = ext.library_view(_kind)
                except Exception:  # noqa: BLE001
                    ext_libs[_kind] = {}
                try:
                    ext_combos[_kind] = ext.view(_kind)
                except Exception:  # noqa: BLE001
                    ext_combos[_kind] = []
            try:
                board_lib, board_combos = board.library_view(), board.view()
            except Exception:  # noqa: BLE001
                board_lib, board_combos = {}, []
            return {
                "service": {**config.state_service(), "maxQueue": config.service.maxQueue},
                "capabilities": registry.state_capabilities(),
                "jobs": copy.deepcopy(state.jobs),
                "wfLib": registry.state_wf_lib(),
                "ui": copy.deepcopy(state.ui),
                "schema": registry.schema,
                "sheetLib": sheet_lib,
                "imageStyleLib": image_style_lib,
                "sheetCombos": sheet_combos,
                "extLibs": ext_libs,
                "extCombos": ext_combos,
                "boardLib": board_lib,
                "boardCombos": board_combos,
                # 视频 / 音频的模式与档位元数据：创作台、画布、管理台共用一份口径
                "video": videolib.state_payload(),
                "audio": audiolib.state_payload(),
                "health": {"online": state.comfy_online, "comfy": config.service.comfy,
                           "queue": copy.deepcopy(state.comfy_queue)},
                "seeded": True,
            }

    def update_ui(self, patch: dict[str, Any]) -> None:
        """保存 UI 草稿（描述、参考图、档位、当前能力），刷新/切页不丢。"""
        with state.lock:
            for key in ("capId", "route", "adminPage", "galleryFilter", "wizardSeen",
                        "drafts", "editingWfIndex", "gOpen", "draft",
                        "railMin", "drawerMin", "promptDock", "promptDockPos"):
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
        media = "image"
        try:
            media = registry.bridge(wf_name).get("media", "image")
        except Exception:  # noqa: BLE001 - 工作流还没登记时按图片处理
            pass
        return {
            "id": state.next_id("run"), "seq": state.next_seq(), "mode": mode,
            "wf": wf_name, "grade": grade or "默认", "seed": None, "media": media,
            "state": "queued", "pct": 0, "step": 0, "total": 8,
            "t0": now_ms(), "t1": None, "file": "", "files": [],
            "prompt_id": "", "err": None, "archive_failed": None,
            "size": "", "node": "",
        }

    def _resolve_refs(self, ref_ids: list[str]) -> list[dict[str, Any]]:
        return [r for rid in ref_ids if (r := state.refs.get(rid))]

    def resolve_refs(self, ref_ids: list[str]) -> list[dict[str, Any]]:
        """对外版：接口层要按素材类型点数（图/视频/音频各几张）时用它。"""
        return self._resolve_refs(ref_ids)

    def video_opt(self, job: dict[str, Any], grade: str) -> dict[str, Any]:
        """这一档 → 真正提交的出片参数（视频四模式共用）。

        比例/分辨率/步数只由档位表决定：页面早已不在画面上单独选这三项
        （web/media.js 的 videoForm 注释与提交体都只从档位对象取 ratio/res/steps），
        档位名 grade 是唯一真源。旧实现把任务首次保存的 ratio/res/steps 作为显式
        参数传进去、压过新档位，导致「加入队列生成」换档时新档位被旧值盖掉，
        ComfyUI 命中执行缓存瞬间返回旧片。现一律按档位名解析，队列换档才会真正
        换参数；时长 duration/length 不属于档位，仍从任务读取。
        """
        v = job.get("video") or {}
        opt = videolib.opts(str(v.get("mode") or "ref2v"), grade)
        # 需求6：创作台时长输入框（4~15 秒，精度 0.01）为准，换算成 H3 的帧数。
        # H3 约 24fps；老草稿没有 duration 时沿用 length / 默认 124 帧。
        dur = v.get("duration")
        if dur is not None:
            width, height = int(opt.get("width") or 1344), int(opt.get("height") or 768)
            # 时长→帧数：124 帧≈5.17 秒 ⇒ 24fps，钳到 H3 合理范围
            frames = int(round(float(dur) * 24))
            opt["length"] = max(24, min(360, frames))
            opt["duration"] = float(dur)
        else:
            opt["length"] = int(v.get("length") or 124)
        return opt

    def audio_opt(self, job: dict[str, Any], grade: str) -> dict[str, Any]:
        a = job.get("audio") or {}
        return audiolib.opts(str(a.get("mode") or "music"), grade)

    def ensure_blank_image(self) -> str:
        """故事板 0 张参考图时给 LoadImage 的纯白占位图，返回 ComfyUI 里的文件名。

        为什么要它：参考生图工作流里有一个 LoadImage 节点，节点上写的默认图片名
        在 ComfyUI 输入目录里并不存在。一格描述都不带参考图时，这个失效文件名会
        被原样提交，ComfyUI 以 prompt_outputs_failed_validation 拒收整条任务。
        占位图只填空位，提示词里不会引用它（compose 已声明参考图仅作素材线索）。
        """
        uploads = config.resolve(config.service.uploadDir)
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / BLANK_IMAGE_NAME
        if not dest.is_file():
            try:
                from PIL import Image
                Image.new("RGB", (1536, 864), "white").save(dest)
            except Exception:  # noqa: BLE001 - 没有 PIL 也要给出一张合法 PNG
                dest.write_bytes(base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
                    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        try:
            return self.comfy.upload_image(dest, BLANK_IMAGE_NAME)
        except ComfyError:
            return BLANK_IMAGE_NAME

    def _ensure_job_refs(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        """按任务记录把参考图补齐，返回可直接交给 build_prompt 的完整 ref 列表。

        ref id 只活在内存里的 RefStore：服务重启、或参考图缓存清理之后，老任务
        job["refs"] 里那串 id 就查不到了。以前「加入队列生成」会带着空 refs
        去发「参考生图」工作流（LoadImage 还指向一个失效的文件名，ComfyUI 直接
        拒收），「再来一张」更会把 refs=[] 写进新卡，参考图永久丢失。这里按
        refsInfo 的落盘副本重新登记，并把新 id 写回任务；图片内容不变，所以
        同种子队列生成出来的仍然是同一张图。
        """
        ids = list(job.get("refs") or [])
        refs = self._resolve_refs(ids)
        if refs and len(refs) == len(ids):
            return refs
        if not ids or not (job.get("refsInfo") or []):
            return refs
        ok, _reason, restored = self.restore_refs(job["id"])
        if ok and restored:
            with state.lock:
                job["refs"] = [r["id"] for r in restored]
            state.persist()
            return self._resolve_refs(job["refs"])
        return refs

    def submit_normal(
        self, cap_id: str, prompt: str, grade: str, ref_ids: list[str],
        wf: str | None = None, sheet: dict[str, Any] | None = None,
        brief: str = "", params: dict[str, Any] | None = None,
        tag: str | None = None, archive_folder: str | None = None,
        video: dict[str, Any] | None = None, audio: dict[str, Any] | None = None,
        image: dict[str, Any] | None = None, raw_prompt: str | None = None,
    ) -> tuple[bool, str, str]:
        """发送 / 再来一张 / 重试：新开一张卡，忙了只提示。返回 (ok, job_id, reason)。

        wf 为前端显式选择的子工作流（如「文生图」/「参考生图」）；若给定且属于该能力，
        优先用它，否则回退到按参考图数量自动挑（pick_workflow）。这样前端既能做显式模式切换，
        也能在没传 wf 时保持旧行为。

        sheet / brief 只有角色设定图会用：sheet 记结构化输入（版式/画法/参考方式/角色行），
        便于「基于上一条」把整台表单带回来；brief 是给对话卡片的一句摘要，
        因为设定图的正文有 800 字左右，铺在卡片上会把整屏撑满。

        params / tag：角色设定图「组合档位」的参数覆盖与缓存键。给了就用它，
        不再按档位名去 config 里查——组合的档位是算出来的，不在 grades 表里。
        """
        cap = config.capability(cap_id)
        if not cap or not cap.get("enabled") or not cap.get("workflows"):
            return False, "", "这个能力还没配置工作流"
        if not prompt.strip():
            return False, "", "写点描述再发送"
        refs = self._resolve_refs(ref_ids)
        max_refs = int(cap.get("maxRefs", 3) or 3)
        # 视频 / 音频能力的 maxRefs 只约束图片张数：视频 6 图 + 1 视频 + 2 音频
        images_only = [r for r in refs if r.get("kind", "image") == "image"]
        if len(images_only) > max_refs:
            return False, "", f"这个能力最多 {max_refs} 张参考图"
        if cap.get("requiredRef") and not refs:
            return False, "", "这个能力需要参考素材"
        # 显式工作流优先：仅当它确实属于这个能力时才采用，避免伪造/越权选择
        # 道具设定图的图生图：用户图只当「造型参考」。若让它占住 LoadImage 位，
        # 模型会把它当「要编辑的原图」整张照抄（场景里的窗棂、垫着的桌子全跟着进来）。
        # 前面垫一张纯白图把 LoadImage 位顶掉，用户图就落到「参考」位。
        if cap_id == "sheet.prop" and refs and str((sheet or {}).get("mode") or "") == "i2i":
            try:
                blank = self.ensure_blank_image()
                refs = [{"id": "__blank__", "comfyName": blank, "path": "", "name": "blank.png"}] + refs
            except ComfyError:
                pass
        wf_name: str | None = None
        if wf and wf in (cap.get("workflows") or []):
            wf_name = wf
            why = "前端显式选择"
        else:
            wf_name, why = pick_workflow(cap, len(refs))
        if not wf_name:
            return False, "", "这种情况还没有配置对应的工作流"
        wf = config.workflow(wf_name) or {}
        if video or audio:
            # 视频 / 音频的档位不在 workflows.<name>.grades 里，而是按模式存在
            # videoGrades / audioGrades：同一套模型换模式就该换一组档位。
            mode = str((video or audio or {}).get("mode") or "")
            names = [g["name"] for g in (
                videolib.grades(mode) if video else audiolib.grades(mode))]
            if grade not in names:
                grade = names[0]
            tag = tag or grade
        elif params is None:
            # 图片生成：档位来自 config 的 grades 表，名字不合法就退回第一档
            grades = [g["name"] for g in wf.get("grades", [])] or ["默认"]
            if grade not in grades:
                grade = grades[0]
            tag = tag or grade
        else:
            # 角色设定图：档位属于某个组合，名字由 combos 校验过，这里不再改
            grade = grade or "默认"
            tag = tag or grade
        ok, reason = self.check_normal(cap_id)
        if not ok:
            return False, "", reason
        job = {
            "id": state.next_id("job"), "cap": cap_id, "capName": cap["name"],
            # 画布是「另一个前端」：能力名仍按真实能力显示，但成品落到它自己的菜单目录
            "archiveFolder": archive_folder or "",
            "capSnapshot": {"id": cap_id, "name": cap["name"]},
            "wf": wf_name, "why": why, "prompt": prompt.strip(),
            "refs": [r["id"] for r in refs], "t0": now_ms(), "seed": None, "runs": [],
            # 记下参考图的本地副本：ref id 只活在内存里（重启就没了），
            # 「基于上一条」要能把图片原样带回来，只能靠这些落盘路径重新登记
            "refsInfo": [
                {"path": r.get("path", ""), "name": r.get("name", "")} for r in refs
            ],
        }
        if sheet:
            job["sheet"] = copy.deepcopy(sheet)
            job["brief"] = brief or ""
        if video:
            job["video"] = copy.deepcopy(video)
        if audio:
            job["audio"] = copy.deepcopy(audio)
        if image is not None:
            # 图片生成的画法/线稿/风格整条存下来，「基于上一条」才能把选项带回来。
            job["image"] = copy.deepcopy(image)
            # prompt 字段是 compose 后的成片提示词（带线稿前缀/尾缀），回填会二次拼装；
            # rawPrompt 是用户原话，refine 优先用它。老的 job 没这个字段，前端会回退到 prompt。
            if raw_prompt is not None:
                job["rawPrompt"] = raw_prompt
        if params is not None:
            # 参数跟着任务走：换档重跑、再来一张都要拿回同一份覆盖，不能靠名字重查
            job["params"] = copy.deepcopy(params)
            job["gradeTag"] = tag
        run = self._new_run(job, "normal", wf_name, grade)
        run["params"] = copy.deepcopy(params) if params is not None else None
        run["gradeTag"] = tag
        job["runs"].append(run)
        with state.lock:
            state.jobs.append(job)
        state.persist()
        return True, job["id"], ""

    def submit_queue(self, job_id: str, grade: str) -> tuple[bool, str]:
        """加入队列生成：接到同一张卡后面，配方原样复用、只换档位。

        设定图必须按「这张卡所属的组合」解析档位：同一组合的档位只是参数覆盖，
        提示词/参考图/种子/工作流都不变，出来的才是同一张图的高清版；
        跨组合拿档位会被拒绝，而不是静默当成母版跑。
        """
        job = state.job(job_id)
        if not job:
            return False, "找不到这张卡片"
        base = job["runs"][0] if job["runs"] else None
        if not base or base["state"] != "done":
            return False, "等第一张出来再往这批里加"
        ok, reason = self.check_queue(job["cap"])
        if not ok:
            return False, reason
        # 老卡的 ref id 可能已经失效：先按落盘副本补齐，别等到执行时才发一个
        # 没有参考图的「参考生图」工作流被 ComfyUI 拒收。
        if job.get("refs") and not self._ensure_job_refs(job):
            return False, "这张卡的参考图已经被清理，请重新上传后再加入队列"
        params: dict[str, Any] | None = None
        tag: str | None = None
        cid = str((job.get("sheet") or {}).get("combo") or "")
        if cid:
            if job.get("cap") in ("sheet.scene", "sheet.prop"):
                from . import extsheets as ext
                kind = ext.SCENE if job.get("cap") == "sheet.scene" else ext.PROP
                if not ext.find_grade(kind, cid, grade):
                    return False, f"「{ext.meta(kind, cid)['name']}」没有档位「{grade}」"
                params = ext.effective_params(kind, cid, grade)
                tag = ext.tag(kind, cid, grade)
            elif job.get("cap") == "board.story":
                from . import boardlib as board
                if not board.find_grade(cid, grade):
                    return False, f"「{board.meta(cid)['name']}」没有档位「{grade}」"
                params = board.effective_params(cid, grade)
                tag = board.tag(cid, grade)
            else:
                from . import combos
                if not combos.find_grade(cid, grade):
                    return False, f"「{combos.meta(cid)['name']}」没有档位「{grade}」"
                params = combos.effective_params(cid, grade)
                tag = combos.tag(cid, grade)
        else:
            # 视频 / 音频的档位按模式存在 videoGrades / audioGrades 里
            vmode = str((job.get("video") or {}).get("mode") or "")
            amode = str((job.get("audio") or {}).get("mode") or "")
            if vmode or amode:
                names = [g["name"] for g in (
                    videolib.grades(vmode) if vmode else audiolib.grades(amode))]
                if grade not in names:
                    grade = names[0]
                tag = grade
            else:
                wf = config.workflow(job["wf"]) or {}
                grades = [g["name"] for g in wf.get("grades", [])] or ["默认"]
                if grade not in grades:
                    grade = grades[0]
                tag = grade
        run = self._new_run(job, "queue", base["wf"], grade)
        run["params"] = params
        run["gradeTag"] = tag
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
        # 先按 refsInfo 把可能失效的参考图重新登记，再把新 id 传给 submit_normal；
        # 否则新卡会记成 refs=[]，参考图从此找不回来。
        refs = self._ensure_job_refs(job)
        return self.submit_normal(
            job["cap"], job["prompt"], run["grade"], [r["id"] for r in refs],
            wf=job.get("wf"), sheet=job.get("sheet"), brief=job.get("brief", ""),
            # 组合档位的参数不按名字重查（名字只在那一个组合里有意义），原样透传
            params=job.get("params"), tag=job.get("gradeTag"),
            # 「再来一张」也要留在画布目录里，不能因为重跑又回到「图片生成」
            archive_folder=job.get("archiveFolder") or None,
            video=job.get("video"), audio=job.get("audio"),
            image=job.get("image"), raw_prompt=job.get("rawPrompt"),
        )

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
                refs = self._ensure_job_refs(job)
                # 普通生成：等价于 ComfyUI 前端 control_after_generate=randomize；
                # 队列生成沿用首发回读到的真实种子（X42）。
                seed = job.get("seed") if run["mode"] == "queue" and job.get("seed") else secrets.randbelow(2**62)
                prefix = f"ComfyBridge_{job['id']}_{run['seq']}"
                params, tag = run.get("params"), run.get("gradeTag")
                bridge = registry.bridge(run["wf"])
                media = bridge.get("media", "image")
                applied: dict[str, Any] = {}
                if media == "video":
                    opt = self.video_opt(job, run["grade"])
                    # 视频工作流在 config 里只有「默认」一档：真正的档位（比例/分辨率/步数）
                    # 是 videolib 按模式算出来的，不落在这条工作流的 grades 表里
                    api = registry.load(run["wf"], "默认")
                    prompt = videolib.build(api, bridge.get("video_ref") or {},
                                            text=job["prompt"], opt=opt, refs=refs,
                                            seed=seed, prefix=prefix)
                    applied = {"mode": opt["modeName"], "size": opt["size"],
                               "steps": opt["steps"], "lora": opt["lora"] or "不挂 LoRA"}
                    size_label = f"{opt['size']} · {opt['steps']} 步"
                elif media == "audio":
                    opt = self.audio_opt(job, run["grade"])
                    api = registry.load(run["wf"], "默认")
                    audio_ref = next((r for r in refs if r.get("kind") == "audio"), None)
                    prompt = audiolib.build(api, bridge, text=job["prompt"],
                                            lyrics=str((job.get("audio") or {}).get("lyrics") or ""),
                                            duration=opt.get("duration"), seed=seed,
                                            prefix=prefix, ref=audio_ref)
                    for node_id, fields in (opt.get("params") or {}).items():
                        node = prompt.get(node_id)
                        if node:
                            node["inputs"].update(fields)
                            applied[node_id] = dict(fields)
                    size_label = (f"{int(opt['duration'])} 秒" if opt.get("duration") else "语音")
                else:
                    prompt = registry.build_prompt(
                        run["wf"], run["grade"], job["prompt"], refs, seed, prefix,
                        params=params, tag=tag,
                        placeholder=None if refs or job.get("cap") != "board.story" else self.ensure_blank_image())
                    # 记下这一档真正提交到 ComfyUI 的参数，页面上可核对「改了有没有生效」
                    eff = registry.grade_effective(
                        run["wf"], run["grade"], params=params, tag=tag)
                    applied, size_label = eff["applied"], eff["size"]["label"]
                prompt_id = self.comfy.submit(prompt)
                with state.lock:
                    run["prompt_id"] = prompt_id
                    run["seed"] = seed
                    run["applied"] = applied
                    run["sizeExpected"] = size_label
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
            payload = outputs.get(node_id, {}) or {}
            for key in OUTPUT_KEYS:
                for item in payload.get(key) or []:
                    filename = item.get("filename")
                    if not filename:
                        continue
                    meta = item.get("metadata") or {}
                    info = {"filename": filename, "subfolder": item.get("subfolder", "") or "",
                            "type": item.get("type", "output") or "output"}
                    info["width"] = meta.get("width")
                    info["height"] = meta.get("height")
                    # 视频 / 音频按扩展名与 metadata.kind 一起判，别把 mp4 当图片渲染
                    kind = str(meta.get("kind") or "")
                    if kind not in ("video", "audio"):
                        kind = MEDIA_SUFFIX.get(Path(filename).suffix.lower(), "image")
                    info["kind"] = kind
                    for extra in ("duration", "fps", "frame_count"):
                        if meta.get(extra) is not None:
                            info[extra] = meta[extra]
                    self._archive(job, run, info)
                    # 宫格分镜线稿：夕阳/暖光格在 cfg=3.5 下仍会渗色（Qwen 对暖色先验太强），
                    # 提示词与 CFG 都压不住，最后一线：线稿工作流的成品落盘后确定性转灰度。
                    # 只影响 wf 名带「线稿」的任务，彩色与其它能力不受影响。
                    if info.get("path") and "线稿" in str(run.get("wf") or ""):
                        try:
                            from PIL import Image, ImageOps
                            p = Path(info["path"])
                            with Image.open(p) as im:
                                ImageOps.grayscale(im).convert("RGB").save(p)
                            info["desaturated"] = True
                        except Exception as exc:  # noqa: BLE001 - 后处理失败不挡出图
                            info["desaturated"] = f"failed: {exc}"
                    # 音频/视频节点不写 duration：归档完自己探一次，卡片上才有「N 秒」
                    if info.get("duration") is None and info.get("kind") in ("audio", "video") and info.get("path"):
                        try:
                            from .mediaprobe import duration as probe_duration
                            got = probe_duration(info["path"])
                            if got:
                                info["duration"] = got
                        except Exception:  # noqa: BLE001 - 探测失败不能影响收尾
                            pass
                    # 道具多视图：模型会把同一视角复制几份凑满画布，出图后做一次视觉去重
                    if (job.get("cap") == "sheet.prop"
                            and str((job.get("sheet") or {}).get("layout") or "") == "MV"
                            and info.get("path")):
                        try:
                            from .extsheets import view_source
                            from .viewdedup import dedup_views
                            # 只有系统自动凑的三个视角才允许把同一个圆形态合掉；
                            # 用户自己点了「正面、背面」，一个都不许动。
                            src = view_source(str((job.get("sheet") or {}).get("text") or ""))
                            info["dedup"] = dedup_views(Path(info["path"]), auto=(src == "auto"))
                        except Exception as exc:  # noqa: BLE001
                            info["dedup"] = {"ok": False, "reason": str(exc)}
                    files.append(info)
        if not files:
            # 工作流"跑完"但一个成品都没产出：ComfyUI 那句
            # "Failed to validate prompt / Output will be ignored / Prompt executed in 0.0X seconds"
            # 就是这种情形（工作流里的模型名或节点在本机 ComfyUI 里对不上）。
            # 以前这里照样写 done，页面显示"成功但没图"，比直接报错更难查。
            self._finish_error(job, run, err(
                "NO_OUTPUT", "ComfyUI 跑完了，但一张图都没产出",
                "多半是工作流里的模型/节点在本机 ComfyUI 里对不上；看 ComfyUI 控制台日志确认",
                short="没有产出图片", retriable=True))
            return
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
            if files and files[0].get("duration"):
                # 视频 / 音频都记一条实际时长：卡片上显示「5.2 秒」比「成功」有用
                run["duration"] = round(float(files[0]["duration"]), 2)
                if files[0].get("kind") == "audio":
                    run["size"] = f"{run['duration']:.1f} 秒"
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
        dst_dir = Path(config.service.archiveDir).resolve() / (job.get("archiveFolder") or job["capName"])
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
        if job.get("sheet"):
            # 设定图多记一笔结构：以后按版式/画法统计哪一类出得最多
            sheet = job["sheet"]
            record["sheet"] = {"layout": sheet.get("layoutName"), "ink": sheet.get("ink"),
                               "ref": sheet.get("ref"), "style": sheet.get("style")}
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
            # 同一张图已经登记过就直接复用：不然每点一次「基于上一条」就多复制一份，
            # 上传目录会被自己的副本塞满（实测点 19 次就多出 19 个文件）。
            existing = next(
                (r for r in state.refs.as_list()
                 if str(r.get("path") or "") and
                 Path(str(r["path"])).resolve() == src.resolve()),
                None,
            )
            if existing:
                out.append({"id": existing["id"], "kind": "image",
                            "name": existing.get("name") or src.name})
                continue
            ref_id = state.next_id("ref")
            # 源图本来就在上传目录里（绝大多数情况）就直接复用，不再复制一份：
            # 缓存清理现在只摘内存登记、保留磁盘文件，每恢复一次都复制会把上传目录撑满。
            try:
                in_uploads = src.resolve().is_relative_to(uploads.resolve())
            except (OSError, ValueError):
                in_uploads = False
            if in_uploads:
                dest, filename = src, src.name
            else:
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

        保护范围（少一条就会误删用户还要用的图）：
        1. 调用方给的保护项（当前草稿里的参考图）；
        2. 任何能力草稿里引用的 ref id；
        3. 未从会话删除的任务引用的 ref id，以及它们 `refsInfo` 里记的磁盘路径——
           ref id 只活在内存里，服务重启或「基于上一条」重新登记后会换 id，
           只有路径是跨重启可靠的锚。以前只按 id 保护，正是「图被当孤儿删掉」的根因。
        """
        prot = [r if isinstance(r, str) else str((r or {}).get("id") or "") for r in (protect or [])]
        draft_ids = set(i for i in prot if i)
        protected = set(draft_ids)
        protected_paths: set[str] = set()
        with state.lock:
            for draft in (state.ui.get("drafts") or {}).values():
                # 草稿里的 refs 可能是 id 字符串（创作台），也可能是 {id:...} 对象
                # （画布的连线/素材记录）；统一抽成 id 再进 set，不然 dict 直接当 key
                # 会炸「unhashable type: 'dict'」，上传直接 500。
                ids = [r if isinstance(r, str) else str((r or {}).get("id") or "")
                       for r in ((draft or {}).get("refs") or [])]
                draft_ids.update(i for i in ids if i)
                protected.update(i for i in ids if i)
            for job in state.jobs:
                if job.get("sessionRemoved"):
                    continue
                protected.update(job.get("refs") or [])
                for info in job.get("refsInfo") or []:
                    path_text = str(info.get("path") or "")
                    if path_text:
                        protected_paths.add(path_text.replace("/", "\\").lower())
        items = state.refs.as_list()
        removed = 0
        for ref in items:
            if len(state.refs.as_list()) < keep:
                break
            if ref.get("id") in protected:
                continue
            ref_path = str(ref.get("path") or "")
            if ref_path and ref_path.replace("/", "\\").lower() in protected_paths:
                continue
            outcome = self._remove_file(Path(ref_path)) if ref_path else "deleted"
            if outcome == "blocked":
                continue          # 文件动不了就先留着登记，下次再试；绝不因此让上传 500
            state.refs.remove(ref["id"])
            removed += 1

        # 连续创作很久时，几乎每张登记都被历史任务引用，上面一趟可能一张都删不掉，
        # 于是第 31 张参考图直接被 400 挡住。其实每条任务的 refsInfo 都记着落盘
        # 路径，参考图随时能重新登记（见 _ensure_job_refs / restore_refs），所以
        # 这里只摘掉内存登记、保留磁盘文件，保证上传不会被旧任务卡死；当前正在
        # 编辑的草稿参考图绝不动。
        if len(state.refs.as_list()) >= keep:
            for ref in state.refs.as_list():
                if len(state.refs.as_list()) < keep:
                    break
                if ref.get("id") in draft_ids:
                    continue
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
        """看菜单：只列「已启用且配了工作流」的能力（当前是图片生成 + 角色设定图）。"""
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
