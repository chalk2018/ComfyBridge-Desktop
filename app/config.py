"""单配置文件管理：启动时读一次，管理台保存后写回磁盘并立即在内存生效。

注意（部署红线）：成果目录只允许落在 ComfyUI 的 output 目录内——应用只在这个
范围里移动本次生成的成品，不碰用户别处的文件。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


class ServiceSettings(BaseModel):
    """服务设置（唯一当前版本，不做多版本对比）。"""

    comfy: str = "http://127.0.0.1:8188"
    port: int = Field(default=8190, ge=1, le=65535)
    bindLan: bool = False
    comfyOutputDir: str
    archiveDir: str
    wfDir: str = "./workflows"
    copyDir: str = "./workflows/ComfyBridge"
    uploadDir: str = "./data/uploads"
    taskDir: str = "./data/tasks"
    uploadMaxMB: int = Field(default=32, ge=1, le=512)
    maxQueue: int = Field(default=20, ge=1, le=200)
    keepDays: int = Field(default=7, ge=1)
    maxRecords: int = Field(default=500, ge=1)

    @field_validator("comfy")
    @classmethod
    def _check_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("ComfyUI 地址要以 http:// 或 https:// 开头")
        return value.rstrip("/")


class ConfigError(Exception):
    """配置损坏或越界时抛出，页面上会明确指出问题。"""


class ConfigManager:
    """线程安全地持有配置；raw 是配置文件的完整内容。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.raw: dict[str, Any] = {}
        self.service = ServiceSettings(
            comfy="http://127.0.0.1:8188",
            comfyOutputDir=str(ROOT / "data"),
            archiveDir=str(ROOT / "data"),
        )

    def resolve(self, value: str) -> Path:
        """把配置里的相对路径解析成以项目根目录为基准的绝对路径。"""
        path = Path(value)
        return (path if path.is_absolute() else ROOT / path).resolve()

    def load(self) -> None:
        """读取 config.yaml；文件损坏时抛 ConfigError（不自动替换用户配置）。"""
        with self._lock:
            try:
                raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                # 需求 04 章：配置被手改坏时要明确指出「第几行、哪个字段有问题」
                mark = getattr(exc, "problem_mark", None)
                where = f"第 {mark.line + 1} 行" if mark is not None else "未知位置"
                detail = getattr(exc, "problem", None) or str(exc)
                raise ConfigError(f"config.yaml {where} 有问题：{detail}") from exc
            except Exception as exc:  # noqa: BLE001 - 明确转达配置错误
                raise ConfigError(f"读取 config.yaml 失败：{exc}") from exc
            if not isinstance(raw, dict) or not all(k in raw for k in ("service", "capabilities", "workflows")):
                raise ConfigError("config.yaml 缺少 service / capabilities / workflows 之一")
            try:
                service = ServiceSettings(**raw["service"])
            except Exception as exc:  # noqa: BLE001
                raise ConfigError(f"config.yaml 的 service 段有问题：{exc}") from exc
            self._validate_paths(service)
            self.raw = raw
            self.service = service
            self.ensure_dirs()

    def _validate_paths(self, service: ServiceSettings) -> None:
        """成果目录必须在 ComfyUI output 内；其余写入目录必须在项目目录内。"""
        archive = Path(service.archiveDir).resolve()
        if ".." in service.archiveDir or "models" in service.archiveDir.lower():
            raise ConfigError("成果目录路径不合法")
        # 需求 §2.4：成果目录可以指定成任意有写权限的目录（默认仍在 ComfyUI output 下）。
        # 这里只校验权限、不写探针文件，避免在指定目录里留下任何多余文件。
        if archive.exists():
            if not archive.is_dir():
                raise ConfigError("成果目录不是一个文件夹")
            if not os.access(archive, os.W_OK):
                raise ConfigError(f"成果目录没有写权限：{archive}")
        for key in ("wfDir", "copyDir", "uploadDir", "taskDir"):
            target = self.resolve(getattr(service, key))
            if not target.is_relative_to(ROOT):
                raise ConfigError(f"{key} 必须位于应用目录内")

    def ensure_dirs(self) -> None:
        """创建应用需要的目录（只在本项目目录和成果目录内）。"""
        for key in ("wfDir", "copyDir", "uploadDir", "taskDir"):
            self.resolve(getattr(self.service, key)).mkdir(parents=True, exist_ok=True)
        self.resolve(self.service.archiveDir).mkdir(parents=True, exist_ok=True)

    def save_service(self, patch: dict[str, Any]) -> None:
        """管理台保存服务设置：校验 → 写回 → 内存立即生效。"""
        with self._lock:
            merged = dict(self.raw["service"])
            for key, value in patch.items():
                if key in ServiceSettings.model_fields:
                    merged[key] = value
            candidate = ServiceSettings(**merged)
            self._validate_paths(candidate)
            self.raw["service"] = candidate.model_dump()
            CONFIG_PATH.write_text(
                yaml.safe_dump(self.raw, allow_unicode=True, sort_keys=False, width=120),
                encoding="utf-8",
            )
            self.service = candidate
            self.ensure_dirs()

    def replace(self, raw: dict[str, Any]) -> None:
        """用一份完整配置替换当前配置（能力启停、复制、删除、档位改动后写回）。"""
        with self._lock:
            ServiceSettings(**raw["service"])
            self._validate_paths(ServiceSettings(**raw["service"]))
            CONFIG_PATH.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=120),
                encoding="utf-8",
            )
            self.raw = raw
            self.service = ServiceSettings(**raw["service"])
            self.ensure_dirs()

    def capability(self, cap_id: str) -> dict[str, Any] | None:
        """按 id 找能力定义。"""
        with self._lock:
            return next((c for c in self.raw.get("capabilities", []) if c.get("id") == cap_id), None)

    def workflow(self, name: str) -> dict[str, Any] | None:
        """按名字找工作流母版定义。"""
        with self._lock:
            wf = self.raw.get("workflows", {}).get(name)
            return dict(wf) if wf else None

    def state_service(self) -> dict[str, Any]:
        """给页面的服务设置字段（不含任何口令/内部字段）。"""
        return self.service.model_dump()


config = ConfigManager()
