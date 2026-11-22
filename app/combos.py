"""角色设定图：组合（母版）与档位。

概念边界（这条线决定「档位」到底能改什么）：

- **组合** = 子功能（文生图/图生图）× 版式 × 画法 × 参考方式 的一次确定取值。
  它对应一条完整配方：工作流文件 + 通用块 + 版式句 + 线稿句 + 参考声明 + 版式画幅。
  这条配方就是该组合的**母版**，也就是默认档。
- **档位** = 母版之上的参数覆盖：画幅、百万像素、对齐倍数、CFG、sigma 序列。
  档位**不许**改提示词、版式、参考方式，也不许换工作流——同一组合切「快速/精细」
  出来的必须是同一张图的不同精度版本，否则「加入队列生成」就失去意义。

为什么不在 config 里给每个组合各写一条 workflow 定义：
18 个组合只是「同一份工作流 + 不同参数 + 不同提示词段落」，重复 18 遍配置
除了制造漂移风险没有任何好处。组合由版式/画法/参考方式在运行时推导，
config 的 `sheetGrades` 只存用户自建的档位（母版永远是算出来的）。
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from .config import config
from .promptlib import SheetError, library as sheet_library
from .workflows import ASPECT_MAP

# 内置档的名字：界面上和「图片生成」保持一致，就叫「默认」。
# 代码里仍把它当「母版」理解——这一档就是组合的完整配方，参数由版式算出来、不可改名不可删。
MASTER = "默认"
# 历史叫法：早期版本写的是「母版」，现在只用来拦住用户自建同名档位（不许占这个名字）
MASTER_ALIASES: tuple[str, ...] = (MASTER, "母版")
INKS: tuple[str, ...] = ("color", "line")
REFS: tuple[str, ...] = ("none", "lock", "style")
WF_T2I = "设定图-文生图"
WF_I2I = "设定图-参考生图"          # 历史工作流名：老任务与旧拷贝仍可能引用，保留常量
WF_LOCK = "设定图-锁定同角色"
WF_STYLE = "设定图-只借画风"
MAX_NAME = 12

_SIGMAS_RE = re.compile(r"^\s*\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)+\s*$")


def wf_for_ref(ref: str) -> str:
    """参考方式 → 独立子工作流：文生图 / 锁定同角色 / 只借画风各走各的母版。"""
    return {"none": WF_T2I, "lock": WF_LOCK, "style": WF_STYLE}.get(ref, WF_T2I)


def group_of(cid: str) -> str:
    """档位组 key：`版式.子功能`（如 `A.t2i` / `A.lock` / `A.style`）。

    1.07 起锁定同角色与只借画风是两条独立工作流，档位与工作流拷贝也必须分开；
    画法（color/line）仍只改提示词，所以同一版式同一参考方式下两种画法共享档位。
    """
    parsed = parse(cid)
    if not parsed:
        return str(cid)
    layout, _ink, ref = parsed
    mode = "t2i" if ref == "none" else ref
    return f"{layout}.{mode}"


def combo_id(layout: str, ink: str, ref: str) -> str:
    """组合 id：`C.color.lock` 这种，稳定、可读、可直接当缓存键。"""
    return f"{layout}.{ink}.{ref}"


def parse(cid: str) -> tuple[str, str, str] | None:
    """拆组合 id；不合法返回 None（不要抛异常，调用方要能试探）。"""
    parts = str(cid or "").split(".")
    if len(parts) != 3:
        return None
    layout, ink, ref = parts
    if ink not in INKS or ref not in REFS:
        return None
    return layout, ink, ref


def _layouts() -> dict[str, dict[str, Any]]:
    try:
        return {str(item["id"]): item for item in sheet_library.load().get("layouts", [])}
    except SheetError:
        return {}


def _ref_name(ref: str) -> str:
    try:
        modes = {str(m["id"]): str(m["name"]) for m in sheet_library.load().get("ref_modes", [])}
    except SheetError:
        modes = {}
    return modes.get(ref) or {"none": "无参考图", "lock": "锁定同角色", "style": "只借画风"}[ref]


def _ink_name(ink: str) -> str:
    try:
        modes = {str(m["id"]): str(m["name"]) for m in sheet_library.load().get("ink_modes", [])}
    except SheetError:
        modes = {}
    return modes.get(ink) or ("彩色" if ink == "color" else "线稿")


def meta(cid: str) -> dict[str, Any]:
    """组合的展示信息，附带它的工作流与母版画幅。

    Raises:
        SheetError: 组合 id 不合法，或版式已经不在提示词库里。
    """
    parsed = parse(cid)
    if not parsed:
        raise SheetError(f"没有这个组合：{cid}")
    layout_id, ink, ref = parsed
    layout = _layouts().get(layout_id)
    if not layout:
        raise SheetError(f"提示词库里没有版式「{layout_id}」")
    if wf_for_ref(ref) not in (config.capability("sheet.character") or {}).get("workflows", []):
        raise SheetError("这个能力没配对应的子工作流，先回管理台补上")
    return {
        "id": cid, "layout": layout_id, "layoutName": str(layout.get("name", layout_id)),
        "ratio": str(layout.get("ratio", "")), "size": str(layout.get("size", "")),
        "ink": ink, "inkName": _ink_name(ink), "ref": ref, "refName": _ref_name(ref),
        "wf": wf_for_ref(ref),
        "mode": ref,                       # t2i / lock / style：管理台按它拆成三套
        "group": {"none": "文生图", "lock": "图生图-锁定同角色", "style": "图生图-只借画风"}[ref],
        "name": f"{layout.get('name', layout_id)} · {_ink_name(ink)} · {_ref_name(ref)}",
    }


def all_combos() -> list[dict[str, Any]]:
    """18 个组合：文生图 3 版式 × 2 画法 = 6，图生图 3 × 2 × 2 参考 = 12。"""
    out: list[dict[str, Any]] = []
    for layout_id in _layouts():
        for ink in INKS:
            for ref in REFS:
                try:
                    out.append(meta(combo_id(layout_id, ink, ref)))
                except SheetError:
                    continue
    return out


# ------------------------------------------------------------------ 参数
def _tunable(wf_name: str) -> list[dict[str, Any]]:
    """这一档允许改哪些节点字段：由 config 里该工作流的 `fields` 决定。"""
    wf = config.workflow(wf_name) or {}
    return [dict(item) for item in (wf.get("fields") or []) if item.get("node") and item.get("field")]


def _size_field(wf_name: str) -> dict[str, Any] | None:
    """画幅参数的节点（ResolutionSelector），母版的画幅就是往这里写。"""
    fields = _tunable(wf_name)
    for name in ("aspect_ratio", "width"):
        found = next((f for f in fields if f["field"] == name), None)
        if found:
            return found
    return None


def layout_params(cid: str) -> dict[str, Any]:
    """母版参数：把版式的画幅写进工作流的尺寸节点。

    为什么母版也要有参数而不是「原样提交母版文件」：版式决定了 16:9 / 9:16，
    而母版文件里是 1:1。不写进去就会出现「选了四宫格却出方的」。
    """
    info = meta(cid)
    raw = _layouts().get(info["layout"]) or {}
    node = _size_field(info["wf"])
    if not node:
        return {}
    node_id = str(node["node"])
    fields: dict[str, Any] = {"aspect_ratio": raw.get("aspect")}
    # megapixels / multiple 只有在该工作流也把它们列进 fields 时才写，
    # 否则等于偷偷改了用户没勾的参数
    names = {f["field"] for f in _tunable(info["wf"]) if str(f["node"]) == node_id}
    if "megapixels" in names:
        fields["megapixels"] = raw.get("megapixels")
    if "multiple" in names:
        fields["multiple"] = raw.get("multiple")
    return {node_id: {k: v for k, v in fields.items() if v not in (None, "")}}


def _custom_grades(cid: str) -> list[dict[str, Any]]:
    # 档位按「版式.子功能」6 组共享：同组里换画法/参考只改提示词，档位参数完全一样。
    key = group_of(cid)
    table = config.raw.get("sheetGrades") or {}
    raw = table.get(key)
    if raw is None and key.endswith((".lock", ".style")):
        # 1.07 兼容：旧版 lock/style 共用 A.i2i 这组档位，新组还没单独保存过时
        # 继续沿用旧档位，确保拆分后每个档位的参数能力与拆分前完全一致。
        raw = table.get(key.rsplit(".", 1)[0] + ".i2i")
    if raw is None and key != cid:
        # 兼容更早的配置：18 段 key（如 A.color.none）里的自建档位迁到组 key。
        # 迁移只在内存里做，写回发生在用户下一次保存档位时（不悄悄改盘上的文件）。
        for old_cid in (f"{key.split('.')[0]}.{ink}.{ref}" for ink in INKS for ref in REFS):
            rows = table.get(old_cid)
            if rows:
                raw = rows
                break
    return [copy.deepcopy(g) for g in (raw or []) if isinstance(g, dict) and g.get("name")]


def approx_size(cid: str, params: dict[str, Any]) -> str:
    """按参数估算出图尺寸（不读工作流：状态轮询每秒跑一次，别每次都解析 JSON）。"""
    info = meta(cid)
    layout = _layouts().get(info["layout"]) or {}
    aspect = str(layout.get("aspect", ""))
    megapixels = float(layout.get("megapixels", 1) or 1)
    multiple = int(layout.get("multiple", 16) or 16)
    node = _size_field(info["wf"])
    if node:
        got = (params.get(str(node["node"])) or {})
        aspect = str(got.get("aspect_ratio", aspect))
        megapixels = float(got.get("megapixels", megapixels) or megapixels)
        multiple = int(got.get("multiple", multiple) or multiple) or 8
    wr, hr = ASPECT_MAP.get(aspect, (1, 1))
    scale = math.sqrt(megapixels * 1024 * 1024 / (wr * hr))
    width = round(wr * scale / multiple) * multiple
    height = round(hr * scale / multiple) * multiple
    return f"{width} × {height}"


def _flatten(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fields in (params or {}).values():
        if isinstance(fields, dict):
            out.update(fields)
    return out


def master_grade(cid: str) -> dict[str, Any]:
    """母版（默认档）：参数由版式算出来，界面上不可改名、不可删除。"""
    info = meta(cid)
    params = layout_params(cid)
    return {
        "name": MASTER, "locked": True, "params": params,
        "note": f"{info['layoutName']} {info['ratio']}，这一档就是组合的完整配方",
        "size": approx_size(cid, params),
    }


def grades(cid: str) -> list[dict[str, Any]]:
    """该组合的全部档位：母版永远在第一位，后面是用户自建的。"""
    out = [master_grade(cid)]
    for grade in _custom_grades(cid):
        params = effective_params(cid, grade["name"])
        out.append({
            "name": str(grade["name"]), "locked": False,
            "params": copy.deepcopy(grade.get("params") or {}),
            "note": str(grade.get("note") or ""),
            "size": approx_size(cid, params),
        })
    return out


def find_grade(cid: str, name: str) -> dict[str, Any] | None:
    return next((g for g in grades(cid) if g["name"] == name), None)


def effective_params(cid: str, name: str) -> dict[str, Any]:
    """母版参数 + 档位覆盖 = 这一档真正要提交的参数（按节点、按字段合并）。"""
    merged = copy.deepcopy(layout_params(cid))
    if name == MASTER:
        return merged
    grade = next((g for g in _custom_grades(cid) if g["name"] == name), None)
    if not grade:
        raise SheetError(f"这个组合没有档位「{name}」")
    for node_id, fields in (grade.get("params") or {}).items():
        if not isinstance(fields, dict):
            continue
        merged.setdefault(str(node_id), {}).update(fields)
    return merged


def tag(cid: str, name: str) -> str:
    """工作流拷贝/缓存用的标签：档位组 + 档位。

    同组的画法/参考共用同一份拷贝（参数节点完全一样），不同组/不同档位互不串味。
    """
    return f"{group_of(cid)}__{name}"


# ------------------------------------------------------------------ 校验与保存
def _check_value(field: str, value: Any) -> Any:
    """逐字段校验并归一化；宁可报错也不静默存一个提交时不生效的值。"""
    if field == "aspect_ratio":
        text = str(value)
        if text not in ASPECT_MAP:
            raise SheetError(f"宽高比只能是工作流支持的选项，收到「{text}」")
        return text
    if field == "megapixels":
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise SheetError("百万像素要填数字") from None
        if not 0.05 <= number <= 8:
            raise SheetError("百万像素建议在 0.05 ~ 8 之间")
        return number
    if field == "multiple":
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            raise SheetError("对齐倍数要填整数") from None
        if not 4 <= number <= 128:
            raise SheetError("对齐倍数建议在 4 ~ 128 之间")
        return number
    if field == "cfg":
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise SheetError("CFG 要填数字")
        if not 0 <= number <= 30:
            raise SheetError("CFG 建议在 0 ~ 30 之间")
        return number
    if field == "sigmas":
        text = str(value or "").strip()
        if not _SIGMAS_RE.match(text):
            raise SheetError("sigma 序列要写成一串数字，逗号分隔，例如 1.0, 0.7, 0.3, 0")
        if len(text.split(",")) < 2:
            raise SheetError("sigma 序列至少两段")
        return text
    return value


def normalize_grades(cid: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验一组自建档位，返回可写进 config 的干净结构。

    `params` 两种写法都收：按节点写（`{"502": {"megapixels": 2}}`，跟 config 一致）
    或按字段名平铺（`{"megapixels": 2}`，前端只认识字段名，不知道节点 id）。
    平铺写法在这里映射成节点写法，落盘永远是同一种结构。

    Raises:
        SheetError: 名字为空/重复/叫「默认」，或参数节点、字段、取值不合法。
    """
    info = meta(cid)
    tunable = _tunable(info["wf"])
    allowed: dict[str, set[str]] = {}
    field_node: dict[str, str] = {}
    for item in tunable:
        node_id, field = str(item["node"]), str(item["field"])
        allowed.setdefault(node_id, set()).add(field)
        field_node.setdefault(field, node_id)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            raise SheetError("档位名不能为空")
        if len(name) > MAX_NAME:
            raise SheetError(f"档位名太长了（{len(name)} 字），{MAX_NAME} 字以内")
        if name in MASTER_ALIASES:
            raise SheetError(f"「{MASTER}」是内置档位，不用也不许自己建")
        if name in seen:
            raise SheetError(f"档位名「{name}」重复了")
        seen.add(name)
        params: dict[str, Any] = {}
        for node_id, fields in (row.get("params") or {}).items():
            node_id = str(node_id)
            if node_id not in allowed:
                mapped = field_node.get(node_id)
                if not mapped:
                    raise SheetError(f"节点 {node_id} 不在这个工作流的可调参数里")
                # 平铺写法：键是字段名，值直接是该字段的值
                params.setdefault(mapped, {})[node_id] = _check_value(node_id, fields)
                continue
            for field, value in (fields or {}).items():
                if str(field) not in allowed[node_id]:
                    raise SheetError(f"节点 {node_id} 没有可调参数「{field}」")
                if value in (None, ""):
                    continue
                params.setdefault(node_id, {})[str(field)] = _check_value(str(field), value)
        out.append({"name": name, "note": str(row.get("note") or ""), "params": params})
    return out


def save_grades(cid: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把某档位组的自建档位整体写回 config.yaml（母版不落盘，永远算出来）。

    写的是 6 段组 key（如 A.t2i），并顺手清掉旧的 18 段 key，避免下次读时
    旧数据盖住新数据。同组所有组合（画法/参考变体）立刻看到这份档位。
    """
    meta(cid)                                  # 先确认组合合法
    cleaned = normalize_grades(cid, rows)
    raw = copy.deepcopy(config.raw)
    table = raw.setdefault("sheetGrades", {}) or {}
    raw["sheetGrades"] = table
    key = group_of(cid)
    layout = key.split(".")[0]
    # 清掉这一组下所有旧 18 段 key（否则兼容读取会优先命中旧数据）
    for ink in INKS:
        for ref in REFS:
            table.pop(f"{layout}.{ink}.{ref}", None)
    if cleaned:
        table[key] = cleaned
    else:
        table.pop(key, None)
    config.replace(raw)
    return grades(cid)


# ------------------------------------------------------------------ 给页面的轻量视图
def view() -> list[dict[str, Any]]:
    """/api/state 用的组合清单：18 种选择（档位按 6 组共享），只带档位名/尺寸。"""
    try:
        items = all_combos()
    except SheetError:
        return []
    out: list[dict[str, Any]] = []
    for info in items:
        try:
            grades_view = [{"name": g["name"], "locked": g["locked"], "note": g["note"],
                            "size": g["size"], "params": _flatten(g["params"])}
                           for g in grades(info["id"])]
        except SheetError:
            continue
        # 管理台要按「节点 → 字段」摆参数表，所以这里带上可调字段的节点归属；
        # 只给 node/field 两个键，避免把整个 config 片段塞进每秒轮询的 state。
        fields_view = [{"node": str(f["node"]), "field": str(f["field"])}
                       for f in _tunable(info["wf"])]
        out.append({**info, "fields": fields_view, "grades": grades_view})
    return out


def view_one(cid: str) -> dict[str, Any]:
    info = meta(cid)
    return {**info, "grades": [{"name": g["name"], "locked": g["locked"], "note": g["note"],
                                "size": g["size"], "params": _flatten(g["params"])}
                               for g in grades(cid)],
            "fields": _tunable(info["wf"]),
            "layoutParams": layout_params(cid)}
