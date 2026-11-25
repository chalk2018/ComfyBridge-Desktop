"""公开版能力开关。

仓库里带 `public_release.json` 时，文件里列出的能力在页面上置灰、接口拒绝受理；
本地开发目录没有这个文件 → 全部能力照常可用，本地代码一行都不用改。

文件格式（放在项目根目录，与 config.yaml 同级）：

    {"disabled": {"canvas.video": "公开版暂未开放", "audio.generate": "公开版暂未开放"}}
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT

FILE = ROOT / "public_release.json"
DEFAULT_REASON = "公开版暂未开放"

_disabled: dict[str, str] = {}
_loaded = False


def load() -> dict[str, str]:
    """读一次开关文件；文件不存在或损坏都按「全开放」处理，不影响启动。"""
    global _disabled, _loaded
    _loaded = True
    _disabled = {}
    try:
        if FILE.is_file():
            # utf-8-sig：记事本 / PowerShell 写出来的文件常带 BOM，用 utf-8 会解析失败
            raw = json.loads(FILE.read_text(encoding="utf-8-sig"))
            for cap_id, reason in (raw.get("disabled") or {}).items():
                _disabled[str(cap_id)] = str(reason or DEFAULT_REASON)
    except Exception:  # noqa: BLE001 - 开关文件坏了不该挡住启动
        _disabled = {}
    return dict(_disabled)


def disabled() -> dict[str, str]:
    if not _loaded:
        load()
    return dict(_disabled)


def is_disabled(cap_id: str) -> bool:
    return str(cap_id) in disabled()


def reason(cap_id: str) -> str:
    return disabled().get(str(cap_id), DEFAULT_REASON)


def name_of(cap_id: str) -> str:
    """拒收时给人看的名字：优先用 config.yaml 里的能力名。"""
    try:
        from .config import config

        cap = config.capability(str(cap_id)) or {}
        return cap.get("name") or str(cap_id)
    except Exception:  # noqa: BLE001
        return str(cap_id)
