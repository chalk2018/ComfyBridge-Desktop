"""图片生成通用模板库（1.05 需求2）。

和角色设定图提示词库严格分开：这里只管「线稿风格句 + 风格预设」两部分，
措辞是主体无关的通用表述（人物 / 动物 / 场景 / 物件都能用），不锁纯白底、
不锁角色一致性。图片生成的画法 / 风格下拉从这里读，管理台以模态框编辑。
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from .config import ROOT

LIB_PATH = ROOT / "prompts" / "image_style.json"


class ImageStyleError(Exception):
    pass


class ImageStyleLibrary:
    def __init__(self, path: Path = LIB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        self._mtime: int | None = None

    def _validate(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            raise ImageStyleError("模板库内容不是一个完整结构")
        if not isinstance(raw.get("line_styles"), list) or not raw["line_styles"]:
            raise ImageStyleError("线稿风格句不能为空")
        if not isinstance(raw.get("styles"), list) or not raw["styles"]:
            raise ImageStyleError("风格预设不能为空")
        for s in raw["line_styles"]:
            if not s.get("id") or not s.get("name") or not str(s.get("text") or "").strip():
                raise ImageStyleError("有一条线稿风格句缺字段或正文为空")
        for s in raw["styles"]:
            if not s.get("name") or not str(s.get("text") or "").strip():
                raise ImageStyleError("有一条风格预设缺字段或正文为空")

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
            except OSError as exc:
                raise ImageStyleError(f"找不到模板库：{self.path.name}") from exc
            if self._cache is not None and self._mtime == mtime:
                return copy.deepcopy(self._cache)
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ImageStyleError(f"模板库第 {exc.lineno} 行 JSON 有问题：{exc.msg}") from exc
            self._validate(raw)
            self._cache, self._mtime = raw, mtime
            return copy.deepcopy(raw)

    def view(self) -> dict[str, Any]:
        return self.load()

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        self._validate(body)
        self.path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        with self._lock:
            self._cache = None
            self._mtime = None
        return self.load()

    def line_style(self, sid: str | None) -> dict[str, Any] | None:
        if not sid:
            return None
        return next((s for s in self.load()["line_styles"] if s["id"] == sid), None)

    def style_text(self, name: str | None) -> str:
        if not name:
            return ""
        return next((s["text"] for s in self.load()["styles"] if s["name"] == name), "")


library = ImageStyleLibrary()
