"""1.03 宫格分镜故事板：提示词拼装、组合与档位。

与 1.02 图片生成 / 角色设定图、1.03 场景 / 道具设定图完全独立：
- 只读自己的 prompts/board_story.json，不改任何既有提示词库或代码路径；
- 线稿句在 board_story.json 里自带（分镜措辞），不依赖角色设定图库；
- 组合 = 版式 × 画法，档位只改参数，提示词一个字都不变。

组合 id：`G4.color` / `G9.line`；档位组（group）：版式 id（G4/G6/G9），
档位存 config.yaml 的 `boardGrades` 段。
"""
from __future__ import annotations

import copy
import json
import math
import re
import threading
from pathlib import Path
from typing import Any

from .config import ROOT, config
from .workflows import ASPECT_MAP

CAP = "board.story"
LIB_PATH = ROOT / "prompts" / "board_story.json"
MASTER = "默认"
MASTER_ALIASES = (MASTER, "母版")
MAX_CELL = 1200
_SIGMAS_RE = re.compile(r"^\s*\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)+\s*$")
_REF_RE = re.compile(r"图\s*([1-9])")


class BoardError(Exception):
    pass


def _clean(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


class _Lib:
    """按 mtime 缓存的提示词库读取器。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: dict[str, Any] | None = None
        self._mtime: int | None = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime_ns
            except OSError as exc:
                raise BoardError(f"找不到提示词库：{self.path.name}") from exc
            if self._cache is not None and self._mtime == mtime:
                return copy.deepcopy(self._cache)
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise BoardError(f"提示词库 {self.path.name} 第 {exc.lineno} 行有问题：{exc.msg}") from exc
            self._cache, self._mtime = raw, mtime
            return copy.deepcopy(raw)

    def save(self, raw: dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._cache, self._mtime = None, None


_LIB = _Lib(LIB_PATH)


def library_view() -> dict[str, Any]:
    d = _LIB.load()
    return {
        "file": LIB_PATH.name,
        "name": d.get("name", ""),
        "note": d.get("note", ""),
        "layouts": d.get("layouts", []),
        "ink_modes": d.get("ink_modes", []),
        "blocks": d.get("blocks", {}),
        "line_styles": d.get("line_styles", []),
        "variable_hints": d.get("variable_hints", {}),
    }


def layout_of(layout_id: str) -> dict[str, Any]:
    d = _LIB.load()
    lay = next((x for x in d.get("layouts", []) if str(x.get("id")) == str(layout_id)), None)
    if not lay:
        raise BoardError(f"没有这个宫格版式：{layout_id}")
    return lay


def cell_position(layout_id: str, i: int) -> str:
    lay = layout_of(layout_id)
    cols, n = int(lay.get("cols", 2)), int(lay.get("cells", 4))
    if i < 0 or i >= n:
        raise BoardError(f"没有第 {i+1} 格")
    if layout_id == "G4":
        return ("左上", "右上", "左下", "右下")[i]
    r, c = divmod(i, cols)
    return f"第 {r+1} 行第 {c+1} 列"


def line_style_text(line_style: str | None) -> str:
    items = _LIB.load().get("line_styles", [])
    if not line_style:
        return items[0]["text"] if items else ""
    for item in items:
        if item.get("id") == line_style or item.get("name") == line_style:
            return str(item.get("text", ""))
    raise BoardError(f"没有这个线稿风格：{line_style}")


def compose(layout_id: str, cells: list[str], ink: str = "color",
            line_style: str | None = None, refs: int = 0) -> str:
    """把逐格提示词拼成一条可直接提交给 image_edit 的正文。"""
    d = _LIB.load()
    b = d.get("blocks", {})
    lay = layout_of(layout_id)
    n = int(lay.get("cells", 4))
    ink = ink if ink in ("color", "line") else "color"
    refs = max(0, int(refs or 0))
    clean_cells = [str(x or "").strip()[:MAX_CELL] for x in (cells or [])[:n]]
    clean_cells += [""] * (n - len(clean_cells))
    cn = d.get("cn", list("一二三四五六七八九"))
    blocks: list[str] = []

    if ink == "line":
        blocks.append(str(b.get("line_prefix", "")))
    blocks.append(str(lay.get("head", "")))

    lines: list[str] = []
    for i, text in enumerate(clean_cells):
        head = f"第 {cn[i]} 格（{cell_position(layout_id, i)}）："
        body = text or str(b.get("blank_cell", ""))
        if ink == "line" and (text or "").strip():
            # 全局的线稿约束压不住个别格子的内容诱导（夕阳/暖阳光格实测被涂成彩色），
            # 必须在每一格行尾再钉一遍黑白线稿。
            body += "（本格严格黑白线稿，只许黑线与白纸，这一格也不许出现任何颜色）"
        lines.append(head + body)
    blocks.append("【逐格画面】\n" + "\n".join(lines))

    if refs == 0:
        blocks.append(str(b.get("no_ref_lock", "")))
    else:
        mentions: list[str] = []
        for i, text in enumerate(clean_cells):
            for num in _REF_RE.findall(text):
                mentions.append(str(b.get("ref_mention", "")).format(cell=cn[i], n=num))
        blocks.append("【参考图使用】" + ("\n".join(dict.fromkeys(mentions)) if mentions
                                       else str(b.get("ref_no_mention", ""))))
        blocks.append(str(b.get("ref_common", "")))
        # 三视图/六视图这类参考图本身是多格拼图，模型会顺手把格子数照搬过来，
        # 明明要 2×2 却画出 6 格。这一句专门压住它。
        blocks.append(
            "【多视图参考图】参考图若是三视图、六视图或多格拼图，只从中取人物长相、发型、服饰与配色，"
            f"绝对不要照搬它的格子数量、排布或分割线；整张故事板必须严格是 {n} 格，多一格少一格都算错。")

    if ink == "line":
        blocks.append("【画法】" + line_style_text(line_style) + str(b.get("line_tail", "")))
    else:
        blocks.append("【画法】" + str(b.get("color_tail", "")))
    tail = str(b.get("tail", ""))
    blocks.append(tail)
    # 格数放在最末：图像模型对结尾的约束最敏感，参考图本身是多格拼图时尤其要压一遍
    rows, cols = int(lay.get("rows", 2)), int(lay.get("cols", 2))
    blocks.append(
        f"【格数硬约束】整张图必须正好 {n} 个格子，按 {rows} 行 × {cols} 列排，不多不少；"
        f"第 {n + 1} 格不存在，任何情况下都不要再添一格。")
    if ink == "line":
        # 线稿检查句要独占最末：夕阳、晚霞、金色彩虹这类格子最容易把模型带回彩色，
        # 放在倒数第二会被格数约束盖过去（实测 G4 第 4 格夕阳被涂成彩色）。
        blocks.append("【最终检查】" + str(b.get("line_check", "")))
    return _clean("\n\n".join(blocks))


def brief(layout_id: str, cells: list[str]) -> str:
    lay = layout_of(layout_id)
    filled = [str(x or "").strip() for x in (cells or []) if str(x or "").strip()]
    first = filled[0][:40] if filled else "空故事板"
    return f"{lay.get('name', layout_id)} · {len(filled)}/{lay.get('cells', 0)} 格 · {first}"


# ------------------------------------------------------------------ 组合 / 档位
def all_combos() -> list[dict[str, Any]]:
    d = _LIB.load()
    out: list[dict[str, Any]] = []
    for lay in d.get("layouts", []):
        lid = str(lay["id"])
        for ink in ("color", "line"):
            wf = str(lay.get("wf") or "")
            if ink == "line" and lay.get("wfLine"):
                # 线稿组合走专用工作流（VAEDecode 后接 ImageDesaturate 去色兜底）：
                # 夕阳/暖光格在 cfg=3.5 下仍会渗色，纯提示词压不住，只能输出侧确定性去彩。
                wf = str(lay["wfLine"])
            out.append({
                "id": f"{lid}.{ink}", "name": f"{lay.get('name', lid)} · {'彩色' if ink == 'color' else '线稿'}",
                "layout": lid, "layoutName": lay.get("name", lid), "ink": ink,
                # mode 用画法：管理台档位表就是「画法 × 版式」两段切，
                # 和设定图那套组件（按 mode + layout 选组合）能直接复用。
                "mode": ink, "ref": "none", "nochar": "yes",
                "group": lid, "wf": wf, "ratio": lay.get("ratio", ""),
                "size": lay.get("size", ""), "cells": lay.get("cells", 0),
            })
    return out


def meta(cid: str) -> dict[str, Any]:
    info = next((c for c in all_combos() if c["id"] == cid), None)
    if not info:
        raise BoardError(f"没有这个组合：{cid}")
    return info


def parse(cid: str) -> dict[str, Any] | None:
    return next((c for c in all_combos() if c["id"] == cid), None)


def _fields(wf_name: str) -> list[dict[str, Any]]:
    wf = config.workflow(wf_name) or {}
    return [dict(f) for f in (wf.get("fields") or []) if f.get("node") and f.get("field")]


def layout_params(cid: str) -> dict[str, Any]:
    """版式写死的参数：16:9 通用节点 + 6 格专用宽高节点，都从组合的 fields 推导。"""
    info = meta(cid)
    lay = layout_of(info["layout"])
    params: dict[str, Any] = {}
    for f in _fields(info["wf"]):
        field = str(f["field"])
        if field not in ("aspect_ratio", "megapixels", "multiple", "width", "height"):
            continue
        key = {"aspect_ratio": "aspect", "width": "width", "height": "height"}.get(field, field)
        value = lay.get(key)
        if value not in (None, ""):
            params.setdefault(str(f["node"]), {})[field] = value
    return params


def approx_size(cid: str, params: dict[str, Any]) -> str:
    # 6 格是固定宽高（EmptyLatentImage），优先按它显示，不能拿 502 的 16:9 估算
    for fields in (params or {}).values():
        if isinstance(fields, dict) and fields.get("width") and fields.get("height"):
            return f"{int(fields['width'])} × {int(fields['height'])}"
    info = meta(cid)
    lay = layout_of(info["layout"])
    got: dict[str, Any] = {}
    for fields in (params or {}).values():
        if isinstance(fields, dict) and "aspect_ratio" in fields:
            got = fields
            break
    aspect = str(got.get("aspect_ratio", lay.get("aspect", "16:9 (Widescreen)")))
    mp = float(got.get("megapixels", lay.get("megapixels", 1)) or 1)
    mult = int(got.get("multiple", lay.get("multiple", 16)) or 16) or 8
    wr, hr = ASPECT_MAP.get(aspect, (16, 9))
    scale = math.sqrt(mp * 1024 * 1024 / (wr * hr))
    return f"{round(wr * scale / mult) * mult} × {round(hr * scale / mult) * mult}"


def _custom_grades(cid: str) -> list[dict[str, Any]]:
    info = meta(cid)
    table = (config.raw.get("boardGrades") or {}).get(info["group"]) or {}
    return [copy.deepcopy(g) for g in table if isinstance(g, dict) and g.get("name")]


def master_grade(cid: str) -> dict[str, Any]:
    info = meta(cid)
    params = layout_params(cid)
    return {"name": MASTER, "locked": True, "params": params,
            "note": f"{info['layoutName']} 完整配方，只读", "size": approx_size(cid, params)}


def grades(cid: str) -> list[dict[str, Any]]:
    out = [master_grade(cid)]
    for g in _custom_grades(cid):
        params = effective_params(cid, str(g["name"]))
        out.append({"name": str(g["name"]), "locked": False,
                    "params": copy.deepcopy(g.get("params") or {}),
                    "note": str(g.get("note") or ""), "size": approx_size(cid, params)})
    return out


def find_grade(cid: str, name: str) -> dict[str, Any] | None:
    return next((g for g in grades(cid) if g["name"] == name), None)


def effective_params(cid: str, name: str) -> dict[str, Any]:
    merged = copy.deepcopy(layout_params(cid))
    if name in MASTER_ALIASES:
        return merged
    g = next((x for x in _custom_grades(cid) if x["name"] == name), None)
    if not g:
        raise BoardError(f"这个组合没有档位「{name}」")
    for nid, fields in (g.get("params") or {}).items():
        if isinstance(fields, dict):
            merged.setdefault(str(nid), {}).update(fields)
    return merged


def tag(cid: str, name: str) -> str:
    return f"board_{meta(cid)['group']}__{name}"


def view_one(cid: str) -> dict[str, Any]:
    info = meta(cid)
    return {**info, "fields": _fields(info["wf"]), "grades": grades(cid)}


def view() -> list[dict[str, Any]]:
    return [view_one(c["id"]) for c in all_combos()]


def save_grades(cid: str, rows: list[dict[str, Any]]) -> None:
    info = meta(cid)
    clean: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("name") or "").strip():
            continue
        name = str(row["name"]).strip()
        if name in MASTER_ALIASES:
            raise BoardError("「默认」是只读档，不能覆盖")
        clean.append({"name": name, "note": str(row.get("note") or ""),
                      "params": _check_params(row.get("params") or {})})
    raw = copy.deepcopy(config.raw)
    raw.setdefault("boardGrades", {})[info["group"]] = clean
    config.replace(raw)


def _check_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for nid, fields in (params or {}).items():
        if not isinstance(fields, dict):
            continue
        row: dict[str, Any] = {}
        for field, value in fields.items():
            if field == "aspect_ratio":
                if str(value) not in ASPECT_MAP:
                    raise BoardError(f"宽高比只能是工作流支持的选项，收到「{value}」")
                row[field] = str(value)
            elif field in ("width", "height", "multiple"):
                try:
                    n = int(float(value))
                except (TypeError, ValueError):
                    raise BoardError(f"{field} 要填整数") from None
                if field == "multiple" and not 8 <= n <= 128:
                    raise BoardError("对齐倍数建议在 8 ~ 128 之间")
                if field in ("width", "height") and not 256 <= n <= 4096:
                    raise BoardError(f"{field} 建议在 256 ~ 4096 之间")
                row[field] = n
            elif field == "megapixels":
                try:
                    n = float(value)
                except (TypeError, ValueError):
                    raise BoardError("百万像素要填数字") from None
                if not 0.05 <= n <= 8:
                    raise BoardError("百万像素建议在 0.05 ~ 8 之间")
                row[field] = n
            elif field == "cfg":
                try:
                    n = float(value)
                except (TypeError, ValueError):
                    raise BoardError("CFG 要填数字") from None
                if not 0 <= n <= 30:
                    raise BoardError("CFG 建议在 0 ~ 30 之间")
                row[field] = n
            elif field == "sigmas":
                if not _SIGMAS_RE.match(str(value or "")):
                    raise BoardError("sigma 要写成用逗号隔开的数字")
                row[field] = str(value)
        if row:
            out[str(nid)] = row
    return out
