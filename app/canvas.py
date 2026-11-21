"""视频生成画布：项目存档 + ComfyTV 功能目录 + 节点能力白名单。

画布只是「另一个前端」——节点生成仍然走 TaskService.submit_normal，
所以普通生成 / 加入队列生成、任务栏、历史记录、归档规则全部沿用同一套，
本模块只负责它自己才需要的三件事：

1. 画布项目（节点、连线、视口）落盘，一项目一份 JSON，不引数据库；
2. ComfyTV 的 186 个 stage 目录（app/comfytv_catalog.json），哪个功能上了画布、
   绑的是哪个工作流，记在 data/canvas/config.json 的 stages 里；
3. 哪些能力可以摆上画布——从 config.yaml 里挑「已启用且有工作流」的，
   再由管理台的开关排除掉几项，不在代码里写死能力清单。
"""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any

from .config import ROOT, config

CANVAS_DIR = ROOT / "data" / "canvas"
PROJECT_DIR = CANVAS_DIR / "projects"
CANVAS_CFG = CANVAS_DIR / "config.json"
CATALOG_PATH = Path(__file__).with_name("comfytv_catalog.json")

# 画布成品统一落到成果目录下的这个文件夹，与菜单名一致
ARCHIVE_FOLDER = "视频生成画布"

MAX_NODES = 400
MAX_EDGES = 600
_PID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_NODE_FIELDS = (
    "id", "type", "x", "y", "w", "title", "capId", "stage", "wf", "grade", "prompt",
    "refs", "jobId", "jobs", "img", "refId", "note", "collapsed",
    # 1.04 五类节点：kind 决定端口与内容形态，video / audio 存这次的出片参数
    "kind", "video", "audio", "media", "name", "dur",
    # 1.05 图片生成节点 = 图生图：模式 / 画法 / 风格 / 结果收起状态要跟着画布一起存盘
    "mode", "ink", "lineStyle", "style", "styleCustom", "styleText", "hideResult",
    # 1.06 子功能节点（设定图 / 故事板 / 视频四模式 / 音频四功能）的控件值统一放这里：
    # cfg = {layout, ink, ref, nochar, grid, lyrics, duration, ...}，不再给每种节点加字段
    "cfg",
)

# ComfyTV 的分类，界面上按它自己的节点菜单顺序排
CAT_ORDER = (
    "Generate", "Image", "Video", "VideoFX", "Keying", "Compose", "Timeline",
    "Audio", "AudioFX", "Music", "3D", "Material", "Storyboard", "Panorama",
    "Input", "Project", "Utils", "Misc",
)
CAT_LABEL = {
    "Generate": "生成", "Image": "图像工具", "Video": "视频", "VideoFX": "视频特效",
    "Keying": "抠像", "Compose": "合成", "Timeline": "时间线", "Audio": "音频",
    "AudioFX": "音频特效", "Music": "音乐", "3D": "三维", "Material": "材质",
    "Storyboard": "分镜", "Panorama": "全景", "Input": "输入 / 资产",
    "Project": "项目", "Utils": "工具", "Misc": "其他",
}


def _now() -> int:
    return int(time.time() * 1000)


def _ensure() -> None:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 单个坏文件不能拖垮列表
        return default


def _write_json(path: Path, data: Any) -> None:
    """原子替换写入，避免服务被关掉时留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------- 画布配置
def load_config() -> dict[str, Any]:
    _ensure()
    raw = _read_json(CANVAS_CFG, {})
    if not isinstance(raw, dict):
        raw = {}
    stages = raw.get("stages")
    if not isinstance(stages, dict):
        stages = {}
    # 画布自己那个能力永远要留在画布上，不能被「排除清单」踢掉
    canvas_ids = {str(c.get("id")) for c in (config.raw.get("capabilities") or [])
                  if c.get("kind") == "canvas"}
    excluded = raw.get("excluded")
    if isinstance(excluded, list):
        return {
            "excluded": [str(x) for x in excluded if str(x) not in canvas_ids],
            "stages": {str(k): str(v) for k, v in stages.items()},
            "updated": int(raw.get("updated") or 0),
        }
    # 首次打开：默认只把「一句提示词 + 参考图」就能跑的能力摆上画布。
    # 设定图/故事板要额外的结构化表单，硬摆上去只会生成空描述，所以默认收起来，
    # 管理员可以在这页把它们放出来。
    auto = [
        str(c.get("id"))
        for c in (config.raw.get("capabilities") or [])
        if c.get("kind") != "canvas"
        and not (str(c.get("id", "")).startswith("image.")
                 or str(c.get("id", "")).startswith("video."))
    ]
    return {"excluded": auto, "stages": {str(k): str(v) for k, v in stages.items()}, "updated": 0}


def save_config(patch: dict[str, Any]) -> tuple[bool, str]:
    """管理台保存：只允许改「哪些能力不上画布」这一个开关。"""
    if not isinstance(patch, dict):
        return False, "配置格式不对"
    excluded = patch.get("excluded")
    if excluded is None:
        excluded = []
    if not isinstance(excluded, list):
        return False, "排除清单格式不对"
    known = {c.get("id") for c in (config.raw.get("capabilities") or [])}
    clean = [str(x) for x in excluded if str(x) in known]
    cfg = load_config()
    cfg["excluded"] = clean
    cfg["updated"] = _now()
    _write_json(CANVAS_CFG, cfg)
    return True, ""


def _save_stages(mapping: dict[str, str]) -> None:
    cfg = load_config()
    cfg["stages"] = {str(k): str(v) for k, v in mapping.items()}
    cfg["updated"] = _now()
    _write_json(CANVAS_CFG, cfg)


# ----------------------------------------------------------------- ComfyTV 功能目录
_CATALOG: list[dict[str, Any]] | None = None


def catalog() -> list[dict[str, Any]]:
    """ComfyTV 的 stage 清单，来自它源码解析出的 comfytv_catalog.json。"""
    global _CATALOG
    if _CATALOG is None:
        data = _read_json(CATALOG_PATH, [])
        _CATALOG = [row for row in data if isinstance(row, dict) and row.get("id")] \
            if isinstance(data, list) else []
    return _CATALOG


def _canvas_cap() -> dict[str, Any] | None:
    for cap in config.raw.get("capabilities") or []:
        if cap.get("kind") == "canvas":
            return cap
    return None


def _looks_like_workflow(path: Path) -> bool:
    """排除 schema.json 这类同目录的说明文件：真正的工作流一定带节点图。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 读不动就不是可用工作流
        return False
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("nodes"), list) and data.get("nodes"):
        return True
    for value in data.values():
        if isinstance(value, dict) and ("class_type" in value or "class" in value):
            return True
    return False


def list_wf_files() -> list[dict[str, str]]:
    """可绑定的工作流文件：项目 workflows 目录下的 ComfyUI 工作流 JSON。"""
    root = ROOT / "workflows"
    rows: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json")):
        if path.name.endswith(".bridge.json"):
            continue
        if not _looks_like_workflow(path):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        rows.append({
            "file": f"workflows/{path.name}",
            "name": path.stem,
            "kb": str(round(size / 1024, 1)),
        })
    return rows


def catalog_payload() -> dict[str, Any]:
    """管理台「视频生成画布」页：功能目录 + 已绑定情况 + 可选工作流文件。"""
    cap = _canvas_cap()
    cap_wfs = list((cap or {}).get("workflows") or [])
    smap = load_config().get("stages") or {}
    rows: list[dict[str, Any]] = []
    for stage in catalog():
        bound = smap.get(stage["id"]) or ""
        on = bool(bound) and bound in cap_wfs
        row = dict(stage)
        row["on"] = on
        row["wfName"] = bound if on else ""
        rows.append(row)

    groups: list[dict[str, Any]] = []
    for cat in CAT_ORDER:
        items = [r for r in rows if str(r.get("cat")) == cat]
        if not items:
            continue
        groups.append({
            "cat": cat,
            "label": CAT_LABEL.get(cat, cat),
            "items": items,
            "on": sum(1 for r in items if r["on"]),
        })

    return {
        "capId": (cap or {}).get("id", "canvas.video"),
        "capName": (cap or {}).get("name", "视频生成画布"),
        "total": len(rows),
        "on": sum(1 for r in rows if r["on"]),
        "groups": groups,
        "files": list_wf_files(),
    }


def _unique_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    i = 2
    while f"{base} {i}" in taken:
        i += 1
    return f"{base} {i}"


def enable_stage(stage_id: str, wf_file: str) -> tuple[bool, str, str]:
    """把一个 ComfyTV 功能挂上画布，并为它建一条工作流记录（档位表的子功能）。

    返回 (ok, message, 工作流名)。工作流名就是档位表里那一行。
    """
    stage = next((x for x in catalog() if x.get("id") == stage_id), None)
    if not stage:
        return False, "没有这个功能", ""
    rel = str(wf_file or "").replace("\\", "/").strip()
    if not rel.startswith("workflows/") or ".." in rel or not rel.lower().endswith(".json"):
        return False, "工作流路径不对", ""
    target = ROOT / rel
    if not target.is_file():
        return False, "这个工作流文件不存在", ""
    # 每个工作流都靠同名的 .bridge.json 告诉程序「哪几个节点是提示词 / 参考图 / 出片」，
    # 少了它就跑不了，所以绑定时必须一对一带上
    bridge_rel = f"workflows/{target.stem}.bridge.json"
    if not (ROOT / bridge_rel).is_file():
        return False, "这个工作流缺少同名 .bridge.json，先去「能力与工作流」把字段绑定配好", ""

    cap = _canvas_cap()
    if not cap:
        return False, "config.yaml 里缺少「视频生成画布」这一项", ""

    raw = copy.deepcopy(config.raw)
    wfs = raw.setdefault("workflows", {})
    if not isinstance(wfs, dict):
        wfs = raw["workflows"] = {}
    caprow = next(c for c in raw.get("capabilities") or [] if c.get("kind") == "canvas")
    smap = dict(load_config().get("stages") or {})

    # 已经绑过：只把工作流文件换掉，保留管理员配好的档位
    old = smap.get(stage_id)
    if old and old in wfs:
        oldrow = wfs[old]
        if oldrow.get("file") != rel:
            oldrow["file"] = rel
        oldrow["bridge"] = bridge_rel
        if old not in (caprow.get("workflows") or []):
            caprow.setdefault("workflows", []).append(old)
        smap[stage_id] = old
        config.replace(raw)
        _save_stages(smap)
        return True, "", old

    name = _unique_name(str(stage.get("zh") or stage_id), set(wfs.keys()) | set(smap.values()))
    wfs[name] = {
        "file": rel,
        "bridge": bridge_rel,
        "status": "ok",
        "fields": [],
        "grades": [{"name": "默认", "locked": True, "note": "原样出图"}],
    }
    wf_list = caprow.setdefault("workflows", [])
    if not isinstance(wf_list, list):
        wf_list = caprow["workflows"] = []
    wf_list.append(name)
    smap[stage_id] = name
    config.replace(raw)
    _save_stages(smap)
    return True, "", name


def disable_stage(stage_id: str) -> tuple[bool, str]:
    """把功能从画布摘掉：能力里去掉这一行，没别处用它就把工作流记录删掉。"""
    smap = dict(load_config().get("stages") or {})
    name = smap.pop(stage_id, "")
    if not name:
        return False, "这个功能还没上画布"

    raw = copy.deepcopy(config.raw)
    caprow = next((c for c in raw.get("capabilities") or [] if c.get("kind") == "canvas"), None)
    if caprow:
        wf_list = [x for x in (caprow.get("workflows") or []) if x != name]
        caprow["workflows"] = wf_list
    used_elsewhere = any(
        name in (c.get("workflows") or [])
        for c in (raw.get("capabilities") or [])
        if c.get("kind") != "canvas"
    )
    if not used_elsewhere:
        (raw.get("workflows") or {}).pop(name, None)
    config.replace(raw)
    _save_stages(smap)
    return True, ""


# ----------------------------------------------------------------- 能力白名单
def _wf_brief(name: str) -> dict[str, Any]:
    row = (config.raw.get("workflows") or {}).get(name) or {}
    grades = [{"name": str(g.get("name") or "默认")} for g in (row.get("grades") or [])]
    return {
        "name": name,
        "grades": grades or [{"name": "默认"}],
        "kind": str((row.get("output") or {}).get("kind") or ""),
        "status": str(row.get("status") or ""),
    }


def available_caps() -> list[dict[str, Any]]:
    """可以摆上画布的能力：已启用、已挂工作流、没被管理台排除。

    workflows 返回的是「工作流对象」（名字 + 档位名 + 输出类型），
    不是字符串——画布节点要靠它列出档位下拉。
    """
    excluded = set(load_config().get("excluded") or [])
    rows: list[dict[str, Any]] = []
    for cap in config.raw.get("capabilities", []) or []:
        if not cap.get("enabled"):
            continue
        names = [str(x) for x in (cap.get("workflows") or [])]
        if not names:
            continue
        if cap.get("id") in excluded:
            continue
        rows.append({
            "id": cap.get("id", ""),
            "name": cap.get("name", ""),
            "em": cap.get("em", ""),
            "group": cap.get("group", ""),
            "desc": cap.get("desc", ""),
            "kind": cap.get("kind", ""),
            "maxRefs": int(cap.get("maxRefs", 3) or 3),
            "workflows": [_wf_brief(n) for n in names],
        })
    return rows


def stages_for_canvas() -> list[dict[str, Any]]:
    """上了画布的 ComfyTV 功能，带输入输出类型，画布节点据此决定槽位。"""
    cap = _canvas_cap()
    if not cap:
        return []
    smap = load_config().get("stages") or {}
    on_names = set(cap.get("workflows") or [])
    rows: list[dict[str, Any]] = []
    for stage in catalog():
        name = smap.get(stage["id"])
        if not name or name not in on_names:
            continue
        row = dict(stage)
        row["wfName"] = name
        row["catLabel"] = CAT_LABEL.get(str(stage.get("cat")), str(stage.get("cat")))
        row["grades"] = _wf_brief(name)["grades"]
        rows.append(row)
    return rows


# ----------------------------------------------------------------- 画布项目
def list_projects() -> list[dict[str, Any]]:
    _ensure()
    items: list[dict[str, Any]] = []
    for path in sorted(PROJECT_DIR.glob("*.json")):
        if not _PID.match(path.stem):
            continue
        data = _read_json(path, {})
        if not isinstance(data, dict):
            continue
        items.append({
            "id": path.stem,
            "name": str(data.get("name") or "未命名画布"),
            "nodes": len(data.get("nodes") or []),
            "edges": len(data.get("edges") or []),
            "updated": int(data.get("updated") or 0),
        })
    items.sort(key=lambda x: -x["updated"])
    return items


def get_project(pid: str) -> dict[str, Any] | None:
    if not _PID.match(pid or ""):
        return None
    path = PROJECT_DIR / f"{pid}.json"
    data = _read_json(path, None)
    if not isinstance(data, dict):
        return None
    data.setdefault("id", pid)
    data.setdefault("name", "未命名画布")
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    if not isinstance(data.get("view"), dict):
        data["view"] = {"x": 0, "y": 0, "k": 1}
    return data


def new_project(name: str = "未命名画布") -> dict[str, Any]:
    _ensure()
    pid = f"cv{_now()}"
    suffix = 1
    while (PROJECT_DIR / f"{pid}.json").exists():
        suffix += 1
        pid = f"cv{_now()}{suffix}"
    data = {
        "id": pid,
        "name": (name or "未命名画布")[:60],
        "view": {"x": 0, "y": 0, "k": 1},
        "nodes": [],
        "edges": [],
        "updated": _now(),
    }
    _write_json(PROJECT_DIR / f"{pid}.json", data)
    return data


def save_project(pid: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """保存画布：只留必要字段，挡住浏览器里那些临时对象被整包写盘。"""
    if not _PID.match(pid or ""):
        return False, "画布编号不合法"
    if not isinstance(payload, dict):
        return False, "画布内容不合法"
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return False, "节点清单不合法"
    if len(nodes) > MAX_NODES:
        return False, f"一个画布最多 {MAX_NODES} 个节点"
    edges = payload.get("edges")
    if not isinstance(edges, list):
        edges = []
    if len(edges) > MAX_EDGES:
        edges = edges[:MAX_EDGES]

    clean_nodes: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        row: dict[str, Any] = {}
        for key in _NODE_FIELDS:
            if key in node:
                row[key] = node.get(key)
        # 坐标/宽度都转成数字，前端画错时会得到 NaN，落盘后整块画布就废了
        for key in ("x", "y", "w"):
            if key in row:
                try:
                    row[key] = round(float(row[key]), 1)
                except (TypeError, ValueError):
                    row.pop(key, None)
        clean_nodes.append(row)

    clean_edges: list[dict[str, Any]] = []
    ids = {n["id"] for n in clean_nodes}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if src in ids and dst in ids and src != dst:
            row: dict[str, Any] = {"from": src, "to": dst}
            # 多连接点节点（场景图生图/扩图 4 个上传位、首尾帧 2 个）要记住线接在哪个槽
            slot = str(edge.get("slot") or "")
            if slot:
                row["slot"] = slot
            clean_edges.append(row)

    view = payload.get("view") if isinstance(payload.get("view"), dict) else {}
    try:
        view = {
            "x": round(float(view.get("x", 0)), 1),
            "y": round(float(view.get("y", 0)), 1),
            "k": min(2.5, max(0.25, round(float(view.get("k", 1)), 3))),
        }
    except (TypeError, ValueError):
        view = {"x": 0, "y": 0, "k": 1}

    data = {
        "id": pid,
        "name": str(payload.get("name") or "未命名画布")[:60],
        "view": view,
        "nodes": clean_nodes,
        "edges": clean_edges,
        "updated": _now(),
    }
    _write_json(PROJECT_DIR / f"{pid}.json", data)
    return True, ""


def rename_project(pid: str, name: str) -> tuple[bool, str]:
    data = get_project(pid)
    if not data:
        return False, "这个画布不存在"
    cleaned = str(name or "").strip()
    if not cleaned:
        return False, "名字不能为空"
    data["name"] = cleaned[:60]
    data["updated"] = _now()
    _write_json(PROJECT_DIR / f"{pid}.json", data)
    return True, ""


def delete_project(pid: str) -> tuple[bool, str]:
    """只删 data/canvas/projects 里自己这一个文件，不动成果目录。"""
    if not _PID.match(pid or ""):
        return False, "画布编号不合法"
    path = PROJECT_DIR / f"{pid}.json"
    if not path.exists():
        return False, "这个画布不存在"
    try:
        path.unlink()
    except OSError as exc:
        return False, f"删不掉：{exc}"
    return True, ""


def save_funcs(patch: dict[str, Any]) -> None:
    """管理台改画布的两个子功能：换绑工作流、改名。

    只动 config.yaml 里的 canvasFuncs 段；能力清单 canvas.video 的 workflows
    同步跟上，不然画布节点上选不出工作流。
    """
    if not isinstance(patch, dict):
        raise ValueError("参数不对")
    raw = config.raw
    funcs_cfg = dict(raw.get("canvasFuncs") or {})
    cap = _canvas_cap()
    cap_wfs = list((cap or {}).get("workflows") or [])
    allowed = set((config.raw.get("workflows") or {}).keys())
    legacy = {"imagegen": "image", "videogen": "video"}
    # 管理台里每个生成节点都有一行开关；图片/视频节点的键沿用旧配置的 image / video
    for key, row in patch.items():
        if key not in FUNC_IDS:
            continue
        store_key = legacy.get(key, key)
        row = row or {}
        merged = dict(funcs_cfg.get(store_key) or {})
        # 只有图片/视频节点允许换绑工作流，其它节点的工作流由组合表决定
        if key in ("imagegen", "videogen"):
            wf = str(row.get("wf") or "").strip()
            if wf and wf not in allowed:
                raise ValueError(f"找不到工作流「{wf}」")
            if wf:
                merged["wf"] = wf
                if wf not in cap_wfs:
                    cap_wfs.append(wf)
            elif "wf" in row:
                merged.pop("wf", None)
        name = str(row.get("name") or "").strip()
        if name:
            merged["name"] = name
        # 需求13：节点开关（是否允许在画布上摆这个生成节点）
        if "enabled" in row:
            merged["enabled"] = bool(row.get("enabled"))
        funcs_cfg[store_key] = merged
    raw["canvasFuncs"] = funcs_cfg
    if cap is not None:
        cap["workflows"] = cap_wfs
    config.replace(raw)


# ---------------------------------------------------------------- 生成节点清单
# 画布上的「生成节点 = 一个子功能」。这一张表就是菜单里的全部生成节点：
#   (kind, 名字, 图标, 分组, 能力 id, 工作流, 媒体, 模式, 档位来源)
# 档位来源：
#   wf    —— workflows.<名字>.grades（文生图 / 图生图）
#   video —— videoGrades.<模式>
#   audio —— audioGrades.<模式>
#   combo —— 组合档位，由前端按「版式 × 画法 × 参考」去 /api/sheet|ext|board/combos 取，
#            这里不给（组合太多，塞进开机数据只会更慢）
NODE_FUNCS: tuple[tuple[str, str, str, str, str, str, str, str, str], ...] = (
    ("t2i", "图片生成-文生图", "🖌", "图片", "image.generate", "文生图", "image", "", "wf"),
    ("imagegen", "图片生成-图生图", "🖼", "图片", "image.generate", "参考生图", "image", "", "wf"),
    ("sheetchar", "角色设定图-文生图", "👤", "图片", "sheet.character", "", "image", "", "combo"),
    ("sheetchar_i2i", "角色设定图-图生图", "👥", "图片", "sheet.character", "", "image", "i2i", "combo"),
    ("sheetscene", "场景设定图-文生图", "🏞", "图片", "sheet.scene", "", "image", "", "combo"),
    ("sheetscene_i2i", "场景设定图-图生图", "🌅", "图片", "sheet.scene", "", "image", "i2i", "combo"),
    ("sheetscene_expand", "场景设定图-扩图", "🔍", "图片", "sheet.scene", "", "image", "expand", "combo"),
    ("sheetprop", "道具设定图-文生图", "🗡", "图片", "sheet.prop", "", "image", "", "combo"),
    ("sheetprop_i2i", "道具设定图-图生图", "🛠", "图片", "sheet.prop", "", "image", "i2i", "combo"),
    ("board", "宫格分镜故事板", "▦", "图片", "board.story", "", "image", "", "combo"),
    ("t2v", "视频生成-文生视频", "🎬", "视频", "video.generate", "视频生成-文生视频-h3", "video", "t2v", "video"),
    ("fl2v", "视频生成-首尾帧视频", "🎞", "视频", "video.generate", "视频生成-首尾帧生视频-h3", "video", "fl2v", "video"),
    ("videogen", "视频生成-参考生视频", "🎬", "视频", "video.generate", "视频生成-参考生视频-h3", "video", "ref2v", "video"),
    ("board2v", "故事板生视频", "▦", "视频", "video.generate", "视频生成-故事板生视频-h3", "video", "board", "video"),
    ("music", "音频生成-音乐生成", "🎵", "音频", "audio.generate", "音频-音乐生成", "audio", "music", "audio"),
    ("refmusic", "音频生成-参考生成音乐", "🎼", "音频", "audio.generate", "音频-参考生成音乐", "audio", "cover", "audio"),
    ("tts", "音频生成-语音生成", "🗣", "音频", "audio.generate", "音频-语音生成", "audio", "tts", "audio"),
    ("clone", "音频生成-声音克隆", "🎙", "音频", "audio.generate", "音频-声音克隆", "audio", "clone", "audio"),
)
FUNC_IDS = {row[0] for row in NODE_FUNCS}
# 一个子功能拆成多个节点（角色/场景/道具的 文生图·图生图·扩图）
# 时，节点 id 的前缀才是它对应的子功能
FUNC_FAMILY = {"sheetchar_i2i": "sheetchar", "sheetscene_i2i": "sheetscene",
               "sheetscene_expand": "sheetscene", "sheetprop_i2i": "sheetprop"}


def _wf_grades(wf: str) -> list[dict[str, Any]]:
    wf_cfg = (config.raw.get("workflows") or {}).get(wf) or {}
    rows = [g for g in (wf_cfg.get("grades") or [])
            if isinstance(g, dict) and str(g.get("name") or "").strip()]
    out = [{"name": str(g.get("name") or "默认"), "locked": bool(g.get("locked")),
            "note": str(g.get("note") or "")} for g in rows]
    return out or [{"name": "默认", "locked": True, "note": "按工作流原样出图"}]


def _grades_for(src: str, wf: str, mode: str) -> list[dict[str, Any]]:
    if src == "video":
        from . import videolib
        return [{"name": g["name"], "note": g.get("note", ""), "ratio": g.get("ratio"),
                 "res": g.get("res"), "steps": g.get("steps")}
                for g in videolib.grades(mode or "ref2v")]
    if src == "audio":
        from . import audiolib
        return [{"name": g["name"], "note": g.get("note", ""), "duration": g.get("duration")}
                for g in audiolib.grades(mode or "music")]
    if src == "wf" and wf:
        return _wf_grades(wf)
    return []


def funcs() -> list[dict[str, Any]]:
    """画布的全部生成子功能。

    每个子功能对应一个节点（文生图 / 图生图 / 三种设定图 / 故事板 / 四种视频 / 四种音频），
    档位一律沿用功能页面那一套，画布不维护第二份：
      · 文生图 / 图生图 → workflows.<wf>.grades
      · 设定图 / 故事板 → 组合档位（前端按版式×画法×参考去 combos 接口取）
      · 视频 / 音频 → videoGrades / audioGrades 按模式
    管理台「视频生成画布」只配 image / video 两项（= 图生图 / 参考生视频），
    换绑工作流与开关仍照旧生效。
    """
    raw = config.raw.get("canvasFuncs") or {}
    caps = {str(c.get("id")): c for c in (config.raw.get("capabilities") or [])}
    # 管理台里每个节点都可以单独开关：canvasFuncs.<节点 id>.enabled。
    # 旧配置把图片/视频节点记在 image / video 两个键上，这里继续兼容。
    legacy = {"imagegen": "image", "videogen": "video"}
    out: list[dict[str, Any]] = []
    for kind, name, em, group, cap_id, wf, media, mode, src in NODE_FUNCS:
        cap = caps.get(cap_id) or {}
        row = raw.get(legacy.get(kind, kind)) or {}
        if kind in ("imagegen", "videogen"):
            # 管理台可以换绑，但只有落在该能力自己的工作流清单里才采用：
            # 旧配置里绑的是「画布-图片生成」（属于 canvas.video），换了 capId 之后
            # 拿它去提交会被后端当成非法 wf 再回退，不如在这里就给回标准工作流。
            bound = str(row.get("wf") or "")
            if bound and bound in (cap.get("workflows") or []):
                wf = bound
            enabled = bool(row.get("enabled", True)) and bool(cap.get("enabled", True))
            name = str(row.get("name") or name) or name
        else:
            enabled = bool(cap.get("enabled", False)) and bool(
                not wf or wf in (cap.get("workflows") or []))
            if "enabled" in row:
                enabled = enabled and bool(row.get("enabled"))
            name = str(row.get("name") or name) or name
        grades = [] if src == "combo" else _grades_for(src, wf, mode)
        out.append({
            "id": kind, "name": name, "em": em, "group": group,
            "capId": cap_id, "wf": wf, "wfRef": "", "media": media, "mode": mode,
            "gradesFrom": src, "grades": grades, "enabled": enabled,
        })
    return out



def state_payload() -> dict[str, Any]:
    """画布页一次拿全：全部生成子功能（= 生成节点）、可用能力、项目清单、配置。"""
    rows = funcs()
    kinds = [
        {"id": "image", "name": "图片", "media": "image", "gen": False, "group": "素材"},
        {"id": "video", "name": "视频", "media": "video", "gen": False, "group": "素材"},
        {"id": "audio", "name": "音频", "media": "audio", "gen": False, "group": "素材"},
    ]
    for f in rows:
        kinds.append({"id": f["id"], "name": f["name"], "media": f["media"], "gen": True,
                      "group": f["group"], "em": f["em"], "capId": f["capId"], "mode": f["mode"]})
    return {
        "caps": available_caps(),
        "stages": stages_for_canvas(),
        "funcs": rows,
        "projects": list_projects(),
        "config": copy.deepcopy(load_config()),
        "archiveFolder": ARCHIVE_FOLDER,
        "nodeKinds": kinds,
    }
