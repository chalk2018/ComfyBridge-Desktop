"""ComfyUI 客户端：连通性、队列、上传、提交、进度、历史、取文件。

只对接已经启动的本机 ComfyUI，不启动/停止它，也不扫描模型目录。
进度走一条常驻 WebSocket；断线自动重连，期间降级为历史轮询，任务不会丢。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable

import httpx
import websocket

from .config import config

logger = logging.getLogger("comfybridge.comfy")


class ComfyError(Exception):
    """ComfyUI 调用失败，带结构化错误码供页面/AI 转达人话。"""

    def __init__(self, code: str, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.raw = raw


class ComfyClient:
    """同步 HTTP + 常驻 WS 客户端。"""

    def __init__(self, on_message: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._on_message = on_message
        self._client = httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))
        self._ws_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._current_prompt_id = ""
        self.client_id = "comfybridge"

    @property
    def base(self) -> str:
        return config.service.comfy

    def _url(self, path: str) -> str:
        return self.base + path

    def health(self) -> tuple[bool, dict[str, Any]]:
        """顶栏那盏灯：只查连不连得上，顺带拿版本信息。"""
        try:
            resp = self._client.get(self._url("/system_stats"), timeout=3.0)
            resp.raise_for_status()
            return True, resp.json()
        except Exception as exc:  # noqa: BLE001
            return False, {"error": str(exc)}

    def queue_state(self) -> dict[str, Any]:
        """读取 ComfyUI 自己的执行队列（running + pending），用于忙闲判定。"""
        try:
            resp = self._client.get(self._url("/prompt"), timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            running = data.get("queue_running", []) or []
            pending = data.get("queue_pending", []) or []
            ids = []
            for item in [*running, *pending]:
                # 队列项形如 [number, prompt_id, prompt, extra, outputs]
                if isinstance(item, (list, tuple)) and len(item) > 1:
                    ids.append(str(item[1]))
            return {
                "running": len(running), "pending": len(pending),
                "ids": ids, "ok": True,
            }
        except Exception as exc:  # noqa: BLE001
            return {"running": 0, "pending": 0, "ids": [], "ok": False, "error": str(exc)}

    def queue_contains(self, prompt_id: str) -> bool:
        """判断一条任务是否还在 ComfyUI 队列/执行中（重启恢复用）。"""
        return prompt_id in self.queue_state().get("ids", [])

    def upload_image(self, path: Path, filename: str) -> str:
        """把参考图交给 ComfyUI（写入它自己的 input 目录），返回可用的文件名。"""
        try:
            with path.open("rb") as handle:
                resp = self._client.post(
                    self._url("/upload/image"),
                    files={"image": (filename, handle, "image/png")},
                    data={"type": "input", "overwrite": "true"},
                    timeout=60.0,
                )
            resp.raise_for_status()
            data = resp.json()
            name = data.get("name") or filename
            subfolder = data.get("subfolder") or ""
            return f"{subfolder}/{name}" if subfolder else name
        except Exception as exc:  # noqa: BLE001
            raise ComfyError("UPLOAD_FAILED", "参考图没能交给 ComfyUI", str(exc)) from exc

    def submit(self, prompt: dict[str, Any], prompt_id_hint: str = "") -> str:
        """提交工作流，返回 prompt_id；被 ComfyUI 拒绝时带原始报错。"""
        self._current_prompt_id = prompt_id_hint
        try:
            resp = self._client.post(
                self._url("/prompt"),
                json={"prompt": prompt, "client_id": self.client_id},
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise ComfyError("COMFY_OFFLINE", "ComfyUI 没连上，先启动它", str(exc)) from exc
        if resp.status_code != 200:
            raw = resp.text[:2000]
            raise ComfyError("WORKFLOW_REJECTED", "这个工作流发不出去，ComfyUI 没接收", raw)
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyError("WORKFLOW_REJECTED", "ComfyUI 没有返回任务编号", json.dumps(data, ensure_ascii=False))
        self._current_prompt_id = prompt_id
        return prompt_id

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        """取单条历史；未完成时返回 None。"""
        try:
            resp = self._client.get(self._url(f"/history/{prompt_id}"), timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            return data.get(prompt_id)
        except Exception:  # noqa: BLE001
            return None

    def delete(self, prompt_id: str) -> None:
        """从 ComfyUI 队列里撤掉一条任务。"""
        try:
            self._client.post(self._url("/prompt"), json={"delete": [prompt_id]}, timeout=10.0)
        except Exception:  # noqa: BLE001
            logger.debug("delete prompt failed", exc_info=True)

    def interrupt(self) -> None:
        """停止 ComfyUI 当前正在跑的任务。"""
        try:
            self._client.post(self._url("/interrupt"), timeout=10.0)
        except Exception:  # noqa: BLE001
            logger.debug("interrupt failed", exc_info=True)

    def view(self, filename: str, subfolder: str = "", kind: str = "output") -> bytes | None:
        """按 ComfyUI 给定的文件名取回成品字节。"""
        try:
            resp = self._client.get(
                self._url("/view"),
                params={"filename": filename, "subfolder": subfolder, "type": kind},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.content
        except Exception:  # noqa: BLE001
            return None

    def start_ws(self) -> None:
        """启动常驻 WebSocket 线程，接收进度/执行/错误消息。"""
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._stop.clear()
        self._ws_thread = threading.Thread(target=self._ws_loop, name="comfy-ws", daemon=True)
        self._ws_thread.start()

    def _ws_loop(self) -> None:
        """断线自动重连；WS 不可用不影响任务完成判定（还有 history 轮询）。"""
        while not self._stop.is_set():
            url = self.base.replace("http://", "ws://").replace("https://", "wss://")
            url += f"/ws?clientId={self.client_id}"
            app = websocket.WebSocketApp(
                url,
                on_message=self._handle_ws_message,
                on_error=lambda _ws, err: logger.debug("ws error: %s", err),
                on_close=lambda _ws, *_args: logger.debug("ws closed"),
            )
            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:  # noqa: BLE001
                logger.debug("ws run_forever failed", exc_info=True)
            self._stop.wait(2.0)

    def _handle_ws_message(self, _ws: Any, raw: str) -> None:
        """解析 ComfyUI 消息并回调业务层。"""
        try:
            message = json.loads(raw)
        except Exception:  # noqa: BLE001
            return
        data = message.get("data") or {}
        if not data.get("prompt_id") and self._current_prompt_id:
            data["prompt_id"] = self._current_prompt_id
        if self._on_message:
            try:
                self._on_message({"type": message.get("type", ""), "data": data})
            except Exception:  # noqa: BLE001
                logger.debug("ws callback failed", exc_info=True)

    def stop(self) -> None:
        """关闭客户端（应用退出时）。"""
        self._stop.set()
        self._client.close()


def parse_history_error(entry: dict[str, Any]) -> dict[str, Any]:
    """把 ComfyUI 的执行错误翻译成「哪个节点 + 一句人话 + 原文」。"""
    status = entry.get("status", {}) or {}
    for name, payload in status.get("messages", []) or []:
        if name != "execution_error":
            continue
        node_type = payload.get("node_type", "某个步骤")
        message = str(payload.get("exception_message", "")).strip()
        short = message.splitlines()[0][:180] if message else "执行没跑完"
        return {
            "code": "EXEC_FAILED",
            "msg": f"在「{node_type}」这一步失败：{short}",
            "raw": message[:4000],
            "short": short[:60],
            "retriable": True,
        }
    return {"code": "EXEC_FAILED", "msg": "这次没跑成", "raw": json.dumps(status, ensure_ascii=False)[:2000],
            "short": "没跑成", "retriable": True}


def read_seed_from_history(entry: dict[str, Any]) -> int | None:
    """从提交给 ComfyUI 的 prompt 里回读真实种子（不猜、不预设）。"""
    prompt = entry.get("prompt")
    if isinstance(prompt, list) and len(prompt) > 2 and isinstance(prompt[2], dict):
        api = prompt[2]
    elif isinstance(prompt, dict):
        api = prompt
    else:
        return None
    for spec in api.values():
        if not isinstance(spec, dict):
            continue
        inputs = spec.get("inputs", {}) or {}
        for key in ("noise_seed", "seed"):
            value = inputs.get(key)
            if isinstance(value, int) and value >= 0:
                return value
    return None
