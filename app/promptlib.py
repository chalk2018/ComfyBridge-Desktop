"""角色设定图提示词库：把「通用块 + 版式 + 参考图声明 + 两行变量」拼成整条提示词。

为什么单独一层：
- 设定图的提示词有 600 字以上、由固定段落拼成，散在前端拼字符串会两头漂移，
  改一处要改两处。这里做成唯一的拼装入口，创作台预览、提交、管理台编辑都走它。
- 提示词库放在 `prompts/character_sheet.json`（数据，不是代码）：通用块 U1~U5
  改一次，所有版式同时生效——这正是原始提示词库文档里「一字不改整段搬」的用法。

结构约定（文件里不要改键名）：
    blocks      通用块，键名在 layout.order 里被引用（U1/U1G/U2/U2G/U3/U3G/U4/U5/R1/R2）
    line_styles 线稿风格句（W1 针管笔 / W2 工业细线 / W3 美式细勾线 / W4 铅笔）
    layouts     版式：head（开头一句）+ cells（格位描述，可含 {u2}/{u2g}）+ order（拼装顺序）
    styles      风格预设（画种 + 材质笔触 + 色调 + 光线）
    presets     15 条已实测组合（A/B/C/D1-3/E1-3/L1-3/M1-3），供管理台速查
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

from .config import ROOT

LIB_PATH = ROOT / "prompts" / "character_sheet.json"

# 拼装顺序里允许出现的两个特殊槽位，不是通用块的键名
SLOT_REF = "ref"
SLOT_VARS = "vars"

MAX_CHARACTER = 1200
MAX_STYLE = 400


class SheetError(Exception):
    """提示词库读不到 / 参数不合法；页面上直接显示这句话。"""


def _clean(text: str) -> str:
    """压掉多余空行：拼装出来的段落之间只留一个空行。"""
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


class SheetLibrary:
    """提示词库的读、校验、拼装、保存（线程安全 + 按 mtime 缓存）。"""

    def __init__(self, path: Path = LIB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        self._mtime: int | None = None

    # ---------------------------------------------------------------- 读
    def load(self) -> dict[str, Any]:
        """读库文件；文件变了自动重读（手改 json 也生效）。"""
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
            except OSError as exc:
                raise SheetError(f"找不到提示词库：{self.path.name}") from exc
            if self._cache is not None and self._mtime == mtime:
                return copy.deepcopy(self._cache)
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SheetError(f"提示词库 {self.path.name} 第 {exc.lineno} 行 JSON 有问题：{exc.msg}") from exc
            self._validate(raw)
            self._cache, self._mtime = raw, mtime
            return copy.deepcopy(raw)

    def _validate(self, raw: Any) -> None:
        """保存前/读取时都校验：宁可报错，也不要拼出半截提示词。"""
        if not isinstance(raw, dict):
            raise SheetError("提示词库内容不是一个完整的结构")
        for key in ("blocks", "layouts", "styles", "line_styles"):
            if key not in raw:
                raise SheetError(f"提示词库缺少「{key}」")
        if not isinstance(raw["blocks"], dict) or not raw["blocks"]:
            raise SheetError("通用块 blocks 不能为空")
        for name, block in raw["blocks"].items():
            text = block.get("text") if isinstance(block, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise SheetError(f"通用块「{name}」的正文是空的")
        if not isinstance(raw["layouts"], list) or not raw["layouts"]:
            raise SheetError("至少要有一个版式")
        known = set(raw["blocks"]) | {SLOT_REF, SLOT_VARS}
        for layout in raw["layouts"]:
            lid = layout.get("id") or "?"
            for key in ("id", "name", "order"):
                if not layout.get(key):
                    raise SheetError(f"版式「{lid}」缺少 {key}")
            if not isinstance(layout.get("order"), list):
                raise SheetError(f"版式「{lid}」的拼装顺序不是一个列表")
            for slot in layout["order"]:
                if slot not in known and slot != "cells":
                    raise SheetError(f"版式「{lid}」引用了不存在的段落「{slot}」")
            if "cells" in layout["order"] and not str(layout.get("cells") or "").strip():
                raise SheetError(f"版式「{lid}」的格位描述是空的")
        if not isinstance(raw["styles"], list) or not raw["styles"]:
            raise SheetError("风格预设不能为空")
        if not isinstance(raw["line_styles"], list) or not raw["line_styles"]:
            raise SheetError("线稿风格句不能为空")

    # ---------------------------------------------------------------- 辅助
    def layout(self, layout_id: str) -> dict[str, Any]:
        """按 id 找版式；找不到抛 SheetError（不静默兜底，免得错版式出图）。"""
        for layout in self.load()["layouts"]:
            if layout["id"] == layout_id:
                return layout
        raise SheetError(f"没有这个版式：{layout_id}")

    def layout_by_name(self, name: str) -> dict[str, Any] | None:
        """档位名就是版式名（创作台的版式按钮 = 工作流档位），按名字反查。"""
        for layout in self.load()["layouts"]:
            if layout["name"] == name:
                return layout
        return None

    def style_text(self, style: str) -> str:
        """风格：命中预设就用预设句，否则当作用户自写的风格句。"""
        text = str(style or "").strip()
        if not text:
            raise SheetError("先选一个画风，或自己写一句风格")
        for preset in self.load()["styles"]:
            if preset["name"] == text:
                return preset["text"]
        return text

    def line_style(self, line_style: str | None) -> dict[str, Any]:
        """线稿风格句；不传就用第一条（针管笔）。"""
        items = self.load()["line_styles"]
        if not line_style:
            return items[0]
        for item in items:
            if item["id"] == line_style or item["name"] == line_style:
                return item
        raise SheetError(f"没有这个线稿风格：{line_style}")

    # ---------------------------------------------------------------- 拼装
    def compose(
        self,
        layout_id: str,
        character: str,
        style: str,
        ink: str = "color",
        ref: str = "none",
        line_style: str | None = None,
    ) -> str:
        """拼出一条可直接提交的提示词。

        Args:
            layout_id: 版式 id（A 四视图横排 / B 四宫格 / C 单张全身）
            character: 角色行正文（年龄身份 + 五官 + 发型发色 + 服装分件 + 标志物）
            style: 风格预设名，或一句自写风格
            ink: color 彩色 / line 线稿（线稿时风格行整行替换成线稿句）
            ref: none 无参考 / lock 锁定同角色（R1 插在角色行前）/ style 只借画风（R2 提到最前）
            line_style: 线稿风格句 id（W1~W4），仅 ink=line 时生效

        Returns:
            拼好的提示词文本。

        Raises:
            SheetError: 版式/字数/参数不合法时。
        """
        lib = self.load()
        layout = self.layout(layout_id)
        character = str(character or "").strip()
        if not character:
            raise SheetError("先写角色描述：年龄身份 + 五官 + 发型 + 服装 + 标志物")
        if len(character) > MAX_CHARACTER:
            raise SheetError(f"角色描述太长了（{len(character)} 字），控制在 {MAX_CHARACTER} 字以内")
        if ink not in ("color", "line"):
            raise SheetError("画法只能是彩色或线稿")
        if ref not in ("none", "lock", "style"):
            raise SheetError("参考方式只能是 无参考图 / 锁定同角色 / 只借画风")

        blocks = lib["blocks"]
        # 线稿：风格行整行换成线稿句；彩色：用风格预设 / 自写句
        if ink == "line":
            style_line = self.line_style(line_style)["text"]
        else:
            style_line = self.style_text(style)
            if len(style_line) > MAX_STYLE:
                raise SheetError("风格描述太长了，一两句就够")

        # 参考图声明：lock 只影响「风格行尾巴」，style 还要提到最前面
        suffix = ""
        if ref == "lock":
            suffix = ("，整体线条风格与图 1 保持一致，画风与图 1 保持一致"
                      if ink == "line" else "，画风与图 1 保持一致")
        variables = f"角色：{character}\n风格：{style_line}{suffix}"

        cells = str(layout.get("cells") or "")
        cells = cells.replace("{u2}", blocks["U2"]["text"]).replace("{u2g}", blocks["U2G"]["text"])

        pieces: list[str] = []
        # R2 必须放整条提示词最前面，放末尾会被角色描述盖过去（实测）
        if ref == "style":
            pieces.append(blocks["R2"]["text"])
        head = str(layout.get("head") or "").strip()
        if head:
            pieces.append(head)
        for slot in layout["order"]:
            if slot == "cells":
                pieces.append(cells)
            elif slot == SLOT_VARS:
                pieces.append(variables)
            elif slot == SLOT_REF:
                if ref == "lock":
                    pieces.append(blocks["R1"]["text"])
            else:
                pieces.append(blocks[slot]["text"])
        return _clean("\n\n".join(p for p in pieces if str(p).strip()))

    def brief(
        self,
        layout_id: str,
        character: str,
        ink: str = "color",
        ref: str = "none",
        style: str = "",
        width: int = 42,
    ) -> str:
        """卡片的短标题：「四视图横排 · 彩色 · 写实摄影 · 赤发剑士…」。

        为什么要单独一条：设定图正文 800 字左右，直接铺在对话卡片上会把整屏撑满，
        卡片只留一句能认出「哪一张」的摘要，完整提示词点开再看。
        """
        layout = self.layout(layout_id)
        ink_name = "线稿" if ink == "line" else "彩色"
        ref_name = {"lock": "锁同角色", "style": "借画风"}.get(ref, "")
        head = str(character or "").strip().replace("\n", " ")
        if len(head) > width:
            head = head[:width] + "…"
        bits = [layout["name"], ink_name]
        if ref_name:
            bits.append(ref_name)
        if style and style not in bits:
            bits.append(str(style))
        return " · ".join(bits + ([head] if head else []))

    # ---------------------------------------------------------------- 视图
    def view(self) -> dict[str, Any]:
        """给管理台/创作台的库快照（不含任何写盘路径）。"""
        lib = self.load()
        return {
            "version": lib.get("version", 1),
            "name": lib.get("name", ""),
            "note": lib.get("note", ""),
            "params": lib.get("params", {}),
            "blocks": lib["blocks"],
            "line_styles": lib["line_styles"],
            "layouts": [{k: v for k, v in layout.items()} for layout in lib["layouts"]],
            "ref_modes": lib.get("ref_modes", []),
            "ink_modes": lib.get("ink_modes", []),
            "styles": lib["styles"],
            "variable_hints": lib.get("variable_hints", {}),
            "presets": lib.get("presets", []),
            "file": str(self.path.name),
        }

    def save(self, raw: dict[str, Any]) -> None:
        """整库保存（管理台编辑通用块/风格后用）：先校验，再原子写回。"""
        with self._lock:
            current = self.load()
            merged = copy.deepcopy(current)
            for key in ("blocks", "line_styles", "styles"):
                if key in raw:
                    merged[key] = raw[key]
            if "layouts" in raw:
                merged["layouts"] = raw["layouts"]
            self._validate(merged)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._cache = None
            self._mtime = None


library = SheetLibrary()
