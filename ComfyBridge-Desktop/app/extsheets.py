"""1.03 扩展设定图：场景设定图 sheet.scene / 道具设定图 sheet.prop。

与 1.02 角色设定图（combos.py + promptlib.py）完全独立：
- 不动 character 的任何代码路径，保证既有功能零影响；
- 复用 character 的 12 类风格 / 4 条线稿句（数据层面引用同一份 JSON）；
- 组合、档位、拼装都在这里自洽实现，接口与 combos.py 对齐，前端可同构消费。

组合（combo id）：
  场景  `{layout}.{mode}.{ink}.{nochar}`  例 `N.t2i.color.yes` / `TL45.expand.-.-`
  道具  `{layout}.{mode}.{ink}.{ref}`     例 `MV.t2i.color.none`
档位组（group key，档位只改参数、与提示词维度无关）：
  场景  `{mode}.{layout}`                 例 `t2i.N` / `expand.TL45`
  道具  `{mode}.{layout}`                 例 `t2i.MV`
档位存 config.yaml 的 `extGrades` 段：`{"scene": {group: [grades]}, "prop": {...}}`。
"""

from __future__ import annotations

import copy
import json
import re
import math
import re
import threading
from pathlib import Path
from typing import Any

from .config import ROOT, config
from .workflows import ASPECT_MAP

SCENE = "scene"
PROP = "prop"
CAP_OF = {SCENE: "sheet.scene", PROP: "sheet.prop"}
LIB_PATHS = {SCENE: ROOT / "prompts" / "scene_sheet.json",
             PROP: ROOT / "prompts" / "prop_sheet.json"}
CHAR_LIB_PATH = ROOT / "prompts" / "character_sheet.json"
# 每个能力用自己独立的工作流，不再和角色设定图共用一对（1.06 单点测试拆分）。
# 命名与 config.yaml 的 workflows / capabilities[].rules 完全一致。
WF_OF = {
    SCENE: {"t2i": "场景设定图-文生图", "i2i": "场景设定图-参考生图"},
    PROP: {"t2i": "道具设定图-文生图", "i2i": "道具设定图-参考生图"},
}
MASTER = "默认"
MASTER_ALIASES = (MASTER, "母版")
MAX_SCENE = 1200
MAX_STYLE = 400
_SIGMAS_RE = re.compile(r"^\s*\d+(?:\.\d+)?(?:\s*,\s*\d+(?:\.\d+)?)+\s*$")


class ExtSheetError(Exception):
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
    """按 mtime 缓存的库文件读取器。"""

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
                raise ExtSheetError(f"找不到提示词库：{self.path.name}") from exc
            if self._cache is not None and self._mtime == mtime:
                return copy.deepcopy(self._cache)
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ExtSheetError(f"提示词库 {self.path.name} 第 {exc.lineno} 行有问题：{exc.msg}") from exc
            self._cache, self._mtime = raw, mtime
            return copy.deepcopy(raw)

    def save(self, raw: dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self._cache = None
            self._mtime = None


_LIBS = {k: _Lib(v) for k, v in LIB_PATHS.items()}


def lib(kind: str) -> _Lib:
    if kind not in _LIBS:
        raise ExtSheetError(f"没有这个设定图类型：{kind}")
    return _LIBS[kind]


def char_lib() -> dict[str, Any]:
    """角色设定图库：只为复用 styles / line_styles / overlay。"""
    return json.loads(CHAR_LIB_PATH.read_text(encoding="utf-8"))


def library_view(kind: str) -> dict[str, Any]:
    d = lib(kind).load()
    cl = char_lib()
    scope = "scene" if kind == SCENE else "prop"
    return {
        "file": lib(kind).path.name,
        "name": d.get("name", ""),
        "note": d.get("note", ""),
        "modes": d.get("modes", []),
        "layouts": d.get("layouts", []),
        "refSlots": d.get("refSlots", []) if scope == SCENE else [],
        "blocks": d.get("blocks", {}),
        "refDeclare": d.get("refDeclare", {}) if scope == SCENE else {},
        "expandBase": d.get("expandBase", "") if scope == SCENE else "",
        "ref_modes": d.get("ref_modes", []),
        "ink_modes": d.get("ink_modes", []),
        "variable_hints": d.get("variable_hints", {}),
        "styles": d.get("styles") or cl.get("styles", []),
        "line_styles": d.get("line_styles") or cl.get("line_styles", []),
        "line_style_overlay": cl.get("line_style_overlay", {}),
        "line_style_profiles": cl.get("line_style_profiles", {}),
    }


# ------------------------------------------------------------------ 拼装
def _styles_of(kind: str) -> list[dict[str, Any]]:
    """优先用本库同名风格（场景/道具版措辞），空则用角色库。"""
    own = lib(kind).load().get("styles") or []
    return own if own else char_lib().get("styles", [])


def style_text(style: str, kind: str = SCENE) -> str:
    text = str(style or "").strip()
    if not text:
        raise ExtSheetError("先选一个画风，或自己写一句风格")
    for preset in _styles_of(kind):
        if preset["name"] == text:
            return preset["text"]
    return text


def line_style_text(line_style: str | None, kind: str = SCENE) -> str:
    # 用本能力自己的线稿母版（场景/道具已各自定制），缺该风格才回角色库兜底，
    # 避免把「角色设定图」字样带进场景/道具提示词。
    items = lib(kind).load().get("line_styles") or []
    if not line_style:
        return items[0]["text"] if items else ""
    for item in items:
        if item["id"] == line_style or item["name"] == line_style:
            return item["text"]
    for item in char_lib().get("line_styles", []):
        if item["id"] == line_style or item["name"] == line_style:
            return item["text"]
    raise ExtSheetError(f"没有这个线稿风格：{line_style}")


def line_overlay(style: str) -> str:
    text = str(style or "").strip()
    if not text:
        return ""
    cl = char_lib()
    preset = next((s for s in cl.get("styles", []) if s["name"] == text), None)
    name = preset["name"] if preset else "自定义画风"
    profile = str((preset and cl.get("line_style_profiles", {}).get(name, "")) or (not preset and text[:120]) or "")
    if not profile:
        profile = f"{name} 的造型比例与线条节奏"
    if len(profile) > MAX_STYLE:
        profile = profile[:MAX_STYLE]
    tpl = str(cl.get("line_style_overlay", {}).get("template") or
              "造型气质：{name} —— {profile}（只借五官比例、形体节奏与线条节奏；画面仍然只有黑白线条和纯白纸底，不上色、不加光影、不做材质渲染）")
    return tpl.format(name=name, profile=profile)


def subs_of(kind: str, layout_id: str) -> list[dict[str, Any]]:
    """版式下的子选项（扩图的短/长、左右转角度）。子选项只改提示词，不是新母版。"""
    if kind != SCENE:
        return []
    lay = next((x for x in lib(SCENE).load()["layouts"] if str(x["id"]) == layout_id), {})
    return [s for s in (lay.get("subs") or []) if isinstance(s, dict) and s.get("id")]


def sub_of(kind: str, layout_id: str, sub_id: str) -> dict[str, Any] | None:
    subs = subs_of(kind, layout_id)
    if not subs:
        return None
    return next((s for s in subs if str(s["id"]) == str(sub_id)), None) or subs[0]


def _layout_head(lay: dict[str, Any], sub_id: str = "") -> tuple[str, str]:
    """取版式机位句；有子选项时按子选项取。返回 (head, 子选项名)。"""
    subs = [s for s in (lay.get("subs") or []) if isinstance(s, dict) and s.get("id")]
    if not subs:
        return str(lay.get("head") or "").strip(), ""
    pick = next((s for s in subs if str(s["id"]) == str(sub_id)), subs[0])
    return str(pick.get("head") or "").strip(), str(pick.get("name") or "")


def compose_scene(
    mode: str, layout_id: str, scene: str,
    refs: dict[str, int] | None = None,
    ink: str = "color", nochar: str = "yes",
    style: str = "写实摄影", style_custom: bool = False, style_text_val: str = "",
    line_style: str | None = None,
    sub: str = "",
) -> str:
    """拼场景设定图提示词。

    refs: {slot: 图序号}，slot ∈ ref/pano/bird/sketch，序号从 1 起；只含用户实际上传的槽位。
    sub: 版式子选项（扩图：S/L/L45/L75/R45/R75），只影响机位句。
    """
    d = lib(SCENE).load()
    layouts = {str(x["id"]): x for x in d["layouts"]}
    modes = {str(x["id"]): x for x in d["modes"]}
    if mode not in modes:
        raise ExtSheetError(f"没有这个模式：{mode}")
    if layout_id not in layouts:
        raise ExtSheetError(f"没有这个版式：{layout_id}")
    lay = layouts[layout_id]
    if mode not in str(lay.get("mode", "")).split(","):
        raise ExtSheetError(f"版式「{lay['name']}」不属于模式「{modes[mode]['name']}」")
    head, _sub_name = _layout_head(lay, sub)
    scene = str(scene or "").strip()
    if len(scene) > MAX_SCENE:
        raise ExtSheetError(f"场景描述太长了（{len(scene)} 字），控制在 {MAX_SCENE} 字以内")

    slots = {k: int(v) for k, v in (refs or {}).items() if int(v) > 0}
    # 版式禁用槽位（全景图生图不收全景图、鸟瞰图生图不收鸟瞰图）——后端再拦一次，
    # 免得接口被直接调用时把不该收的图塞进来。
    blocked = set((d.get("blockedSlots") or {}).get(layout_id) or [])
    if mode == "i2i" and blocked & set(slots):
        bad = "、".join(next((r["name"] for r in d.get("refSlots", []) if r["id"] == s), s)
                        for s in blocked & set(slots))
        raise ExtSheetError(f"「{lay['name']}」图生图不接收{bad}，换个槽位或去掉这张")
    has_layout_ref = any(k in slots for k in ("bird", "sketch"))
    blocks = d.get("blocks", {})

    pieces: list[str] = []
    if mode == "t2i":
        if not scene:
            raise ExtSheetError("先写场景描述")
        if head:
            pieces.append(head)
        pieces.append(scene)

    elif mode == "i2i":
        if not scene:
            raise ExtSheetError("图生图要写提示词：写明想要的效果和从参考图识别出的方位")
        if not slots:
            raise ExtSheetError("图生图要至少上传一张参考图")
        declare = d.get("refDeclare", {})
        for slot in ("ref", "pano", "bird", "sketch"):
            if slot not in slots:
                continue
            spec = next((r for r in d.get("refSlots", []) if r["id"] == slot), {})
            kind = spec.get("kind", "layout")
            key = slot if (slot in ("bird", "sketch") and slot in declare) else ("base" if slot == "ref" else ("pano" if slot == "pano" else "layout"))
            tpl = str(declare.get(key, ""))
            if tpl:
                pieces.append(tpl.format(n=slots[slot]))
        if head:
            pieces.append(head)
        if has_layout_ref:
            pieces.append(str(blocks.get("NO_COPY_LAYOUT", "")))
        # 布局图纸类参考：图纸本身是俯视的，模型容易照着图纸画成俯视图，
        # 这里再补一句平视机位；normal 版式末尾还有 CAMERA 兜底。
        if layout_id == "N" and has_layout_ref:
            pieces.append(str(blocks.get("EYELEVEL", "")))
        pieces.append(scene)

    else:  # expand
        if "ref" not in slots:
            raise ExtSheetError("扩图必须上传参考图")
        pieces.append(str(d.get("expandBase", "以图 1 为同一场景的基准，")).strip())
        if head:
            pieces.append(head)
        # 扩图同样只把布局图/草图当相对方位参考，绝不能照搬色块、符号和像素
        if has_layout_ref:
            pieces.append(str(blocks.get("NO_COPY_LAYOUT", "")))
            pieces.append(str(blocks.get("NO_COPY_EXPAND", "")))
        if scene:
            pieces.append(scene)

    # 禁角色：仅文生图/图生图，且选「是」才强调；选「否」完全不写。
    # 扩图没有禁角色开关，产出永远是空场景设定图，参考图里的人物不能带进成图。
    if (mode in ("t2i", "i2i") and str(nochar) == "yes") or mode == "expand":
        pieces.append(str(blocks.get("NO_CHAR", "画面里没有任何人物。")))

    # 画法 + 风格：扩图两者都没有
    if mode in ("t2i", "i2i"):
        if ink == "line":
            pieces.append(line_style_text(line_style, SCENE))
            use_style = style_text_val if style_custom else style
            if use_style:
                pieces.append(line_overlay(use_style))
        else:
            use_style = style_text_val if style_custom else style
            if use_style:
                st = style_text(use_style, SCENE)
                if len(st) > MAX_STYLE:
                    raise ExtSheetError("风格描述太长了，一两句就够")
                pieces.append(st)

    pieces.append(str(blocks.get("TAIL", "")))
    # 机位句放最末尾、近位权重最高：布局图/参考图的俯视方向会把模型带跑，
    # 全靠最后这一句把机位钉回来。普通版式要求平视，全景抬高俯拍，鸟瞰垂直向下。
    if layout_id == "N":
        cam = str(blocks.get("CAMERA", "") or "").strip()
    elif layout_id == "P":
        cam = str(blocks.get("CAMERA_P", "") or "").strip()
    elif layout_id == "B":
        cam = str(blocks.get("CAMERA_B", "") or "").strip()
    else:
        cam = ""
    if cam:
        pieces.append(cam)
    return _clean("\n".join(p for p in pieces if str(p).strip()))


_VIEW_WORDS = re.compile(
    r"正面|背面|侧面|顶面|顶部|底面|底部|伞面|伞底|展开|撑开|合上|合起|收起|收拢|关闭|打开|开启|合拢|折叠"
    r"|装上|卸下|拆开|组装|特写|俯|仰|斜"
    r"|内侧|外侧|内部|外部|整体|局部|近景|全景|正视|背视|侧视|竖立|横放|平放"
)
# 一个「视角项」最多这么长。超过就不是方位词，而是描述句了
# （「顶部一圈银色锅盖」也含方位词，但它是零件描述，不是让你画一个顶视图）。
_VIEW_MAXLEN = 6
_STATE_WORDS = re.compile(r"打开|关闭|开启|合上|收起|展开|折叠|合拢|敞开")
_FRAME_TAIL = re.compile(r"(都要展示|都要画|都要表现|都要有|都展示|也要展示|也要画|都要|一并展示|都要体现)+$")
_FRAME_HEAD = re.compile(r"^(请|帮我|给我|先生成|生成|画出|画|做|来)+")
_FRAME_MID = re.compile(r"[^，,。；;]*的?道具设定图|道具设定图|设定图")
# 这里曾经有四张「道具名词表」（球体/圆罐该只画一个、方形的不算、桃木不是果实……）。
# 名词表永远补不全用户会写的物件，漏一个就少画两个视角，所以整套删掉：
# 统一画三个视角，长得一样的那几区由出图后的像素比对收掉（app/viewdedup.py）。
_BOTTOM_VIEW = re.compile(r"底部|底面|伞底|内侧|内部|里面|内腔|下方|下面")
_SEP = re.compile(r"[、,，/／;；\n]+")


def _clean_item(raw: str) -> str:
    """把清单里的一项洗成真正的方位/状态描述，剔掉「生成…的道具设定图」这类框架话。"""
    t = str(raw or "").strip()
    t = _FRAME_MID.sub("", t)
    t = _FRAME_TAIL.sub("", t)
    t = _FRAME_HEAD.sub("", t)
    return t.strip(" 的。.，,、；;")


_COLON = re.compile(r"[：:]")


def _split_head(text: str) -> tuple[str, str, bool]:
    """把输入切成（道具名那一段, 冒号后面那一段, 有没有冒号分界）。

    冒号后面紧跟数字的那一个不算分界——「1:72 螺旋桨战斗机模型」里的是比例号，
    以前把它当分隔符，名字会被切出一个孤零零的「1」，整段描述全丢。
    """
    t = str(text or "").strip()
    for m in _COLON.finditer(t):
        i = m.start()
        if t[i + 1:i + 2].isdigit():
            continue
        label, body = t[:i].strip(), t[i + 1:].strip()
        if label and body:
            return label, body, True
    return t, t, False


def _view_list(text: str) -> list[str]:
    """只有当整段读起来确实是一份「视角/状态清单」时，才返回拆好的项；否则返回空。

    以前只要有逗号就拆，结果一段普通描述也被当成清单：
    「美的牌白色电饭煲，圆桶形锅身，顶部一圈银色锅盖，正面一块黑色显示屏」
    拆出四项，还因为含「顶部」「正面」被判成用户指定视角，画成四个电饭煲。
    现在的判据是：每一项都得短（≤6 字）且确实是方位/状态词；没有冒号分界时，
    还要求每一段都符合——真正的清单不会夹杂零件描述。
    """
    label, body, has_colon = _split_head(text)
    src = body if has_colon else str(text or "")
    parts = [x for x in (_clean_item(y) for y in _SEP.split(src)) if x]
    if len(parts) < 2:
        return []
    good = [p for p in parts if len(p) <= _VIEW_MAXLEN and _VIEW_WORDS.search(p)]
    if has_colon:
        ok = len(good) >= 2 and len(good) >= int(len(parts) * 0.6)
    else:
        # 没有冒号时，描述句里的逗号最不可靠，要求「整段每一项都是视角词」才算清单
        ok = len(good) == len(parts) and len(str(text or "")) <= 40
    return parts[:6] if ok else []


def view_source(prop: str) -> str:
    """这批视角是用户点的（`user`）还是系统默认给的（`auto`）。

    出图后的去重强度要跟着它走：用户自己列了「正面、背面」，那两个视角就必须都留着；
    只有系统自动凑的三个视角，才允许把「本来就是同一个形态」的块合掉。
    """
    return str(_view_plan(prop).get("source") or "auto")


def _view_plan(prop: str) -> dict[str, Any]:
    """决定多视图要画几个、各是什么方位。

    规则（按用户 1.03 口径）：
    - 用户提示词里明确列了视角/状态清单 → 按清单，不重复。
    - 其余一律三个：正面、侧面、背面。
    - 哪几个其实长得一样，由出图后的像素比对去掉（`app/viewdedup.py`），
      不靠道具名词表去猜。
    - 所有方位默认斜向下 30° 俯拍；只有用户明确写「底部/底面/内部」的项才保留自下而上的机位。

    为什么不用「球体/圆罐只画一个」这类名词表：词表永远覆盖不全用户会写的物件，
    而漏一个就少画两个视角、还没人发现。统一画三个、让去重收敛，才是与词表无关的做法
    ——足球三个视角一样，去重后同样只剩一个。
    """
    raw = str(prop or "")
    views = _view_list(raw)
    if len(views) < 2:
        # 不写「绕轴转 90 度」这种抽象方位：模型会把三个方位画成同一张图。
        # 改成直接描述「画面里主要能看到什么」，靠视觉差异把三格拉开；
        # 每区都点名，免得参考图里旁边那件家具被当成第二、三区的主体。
        name = _prop_name(raw)
        who = f"「{name}」" if name else "同一个道具"
        return {"n": 3, "source": "auto",
                "items": [f"{who}最典型、最容易辨认的那一面完全对着镜头，整件完整画在区域里",
                          f"{who}转到侧面，整件仍然完整画在区域里，但看到的是很窄的一条侧边轮廓，"
                          f"正面的大部分都转到看不见的方向去了",
                          f"{who}完全转到背面，整件仍然完整画在区域里、一个部件都不能少，"
                          f"只是看到的是平时看不到的那一面"]}
    subj = _prop_subject(raw)
    name = _prop_name(raw)
    items = []
    for v in views[:6]:
        it = v
        if name and name not in it:
            it = f"「{name}」{it}"
        elif not name and subj and len(it) <= 6 and subj not in it:
            it = f"{subj}{it}"
        items.append(it)
    return {"n": len(items), "source": "user", "items": items}


_NOISE_HEAD = re.compile(
    r"^(?:(?:图中|图里|画面中|画面里|桌上|案上|地上|几上|船上|桌面|上面|江面上|水面上|船头|船尾|案头|那|这"
    r"|一个|一只|一把|一颗|一册|一套|一对|一顶|一卷|一幅|一本|一张|一片|一摞|一根|一盏|一尊|一座|一面|一块"
    r"|一支|一条|一艘|一架|一株|一棵|一匹|一柄|一具|一函|一叠|这个|那个"
    r"|颗|个|把|只|顶|卷|幅|册|片(?![面状])|方(?![形块向面])|套|对|张|条|支|座|件|棵|株|艘|架|匹|柄)+的?)+")
# 「一面铜镜」「一柄玉如意」是量词要去掉；但「方形玻璃墨水瓶」里的「方」是形状，
# 去掉就成了「形玻璃墨水瓶」，所以「方」后面跟「形/块/向/面」时不吃。
_SCALE_HEAD = re.compile(r"^\d+\s*[:：]\s*\d+\s*")


_SUBJ_RE = re.compile(
    r"(?:生成|画出|画|做|来|请)*(?:图中|图里|画面中)?的?([\u4e00-\u9fa5A-Za-z0-9]{2,8}?)的(?:道具)?设定图"
)


def _prop_brief(prop: str, plan: dict[str, Any]) -> str:
    """拼在「道具：」后面、交给模型的那段字。

    用户写的就是道具描述时整段原样送进去——以前没有冒号就只取第一个逗号前的
    主体名，「美的牌白色电饭煲，圆桶形锅身，顶部一圈银色锅盖…」会被截成
    「美的牌白色电饭煲」，锅身、锅盖、显示屏全丢。只有遇到
    「生成图中那艘乌篷船的道具设定图」这种指令句，才退化成只留主体名。
    """
    t = str(prop or "").strip()
    if _SUBJ_RE.search(t):
        return _prop_subject(t) or t
    return t


def _prop_name(prop: str) -> str:
    """抠一个短名字，用在每一区的描述里。

    为什么每个区都要点名：参考图是一整张「道具套装」时，只写「同一个道具转个角度」，
    模型会把旁边的书案、竹帘当成第二三区的道具画进去——实测「摊开的古籍」那一张，
    三区里两区画成了榆木书案。每区都把名字念一遍，才算把主体钉住。

    拿哪一段当名字：冒号前面那段的最后一个「的」后面通常就是主体名
    （「文房四宝里的石质镇纸」→「石质镇纸」）；那一段太短或干脆没有，
    才退回冒号后面那句（「文房四宝里的纸」→「一本摊开的古籍线装书」）。
    """
    label, body, has_colon = _split_head(prop)
    if not has_colon or _view_list(prop):
        src = label
    else:
        lab = label.rsplit("的", 1)[-1].strip() if "的" in label else label
        src = lab if len(lab) >= 2 else body
    head = re.split(r"[，,。；;、\n]", src)[0].strip()
    head = _NOISE_HEAD.sub("", head).strip()
    # 量词去掉之后才轮到比例号：「一架 1:72 …」得先吃掉「一架」
    head = _SCALE_HEAD.sub("", head).strip()
    if len(head) > 8 and "的" in head:
        tail = head.rsplit("的", 1)[-1].strip()
        if 2 <= len(tail) <= 12:
            head = tail
    if len(head) > 14:
        head = head[-8:]
    return head


def _prop_subject(prop: str) -> str:
    """从提示词里抠出道具主体名，用来给「关闭」「打开」这类短项补主语。"""
    t = str(prop or "").strip()
    m = _SUBJ_RE.search(t)
    if m:
        sub = _NOISE_HEAD.sub("", m.group(1).strip()).strip()
        return sub or m.group(1).strip()
    label, _body, _has = _split_head(t)
    head = re.split(r"[，,。；;\n]", label)[0]
    head = re.sub(r"^(生成|画出|画|做|来|请|帮我|给我)+", "", head).strip()
    head = _SCALE_HEAD.sub("", head)
    head = _NOISE_HEAD.sub("", head).strip()
    return head if 1 < len(head) <= 10 else ""


STATE_HINT = [
    (re.compile(r"关闭|关上|合闭"), "合上、收进去的样子，看不到内部空间"),
    (re.compile(r"打开|开启|敞开"), "敞开、拉出来或掀开的样子，能看到内部空间"),
    (re.compile(r"合上|收起|合拢|折叠"), "收拢合好，体积明显变小"),
    (re.compile(r"展开|撑开"), "完全撑展开的样子，体积明显变大"),
]


def _state_hint(text: str) -> str:
    for pat, hint in STATE_HINT:
        if pat.search(text):
            return hint
    return ""


ZONE_3 = ["左一区（最左）", "中间区", "右一区（最右）"]


def _zones(n: int) -> tuple[str, list[str]]:
    """区域划分句 + 各区域名。学角色设定图四视图的写法：先把画布切块，再逐块点名。"""
    if n <= 1:
        return ("画面正中只有一个道具，没有其他区域。", ["画面正中"])
    if n == 2:
        return ("从左到右被平均分成两个等宽的纵向区域，两块拼在一起正好填满整张画布。",
                ["左一区", "右一区"])
    if n == 3:
        return ("从左到右被平均分成三个等宽的纵向区域，三块拼在一起正好填满整张画布。", list(ZONE_3))
    if n == 4:
        return ("画面被平均分成上下两行、左右两列，一共四个等大的方格，四块拼在一起正好填满整张画布。",
                ["左上格", "右上格", "左下格", "右下格"])
    if n == 5:
        return ("画面分成上下两行，上排三个等宽方格、下排两个等宽方格，五块拼在一起正好填满整张画布。",
                ["左上格", "中上格", "右上格", "左下格", "右下格"])
    return ("画面被平均分成上下两行、每行三个，一共六个等大的方格，六块拼在一起正好填满整张画布。",
            ["左上格", "中上格", "右上格", "左下格", "中下格", "右下格"])


def _zone_text(item: str, idx: int) -> str:
    """一句区域描述。默认斜向下 30°，只有明确写底部/内部的才保留自下而上的机位。"""
    it = str(item).strip()
    if _BOTTOM_VIEW.search(it):
        return f"{it}，机位移到道具的斜下方往上拍，能看到底面与内侧结构"
    has_azimuth = re.search(r"正面|背面|侧面|顶面|顶部|底面|底部|伞面|整体|局部", it)
    if _STATE_WORDS.search(it) and not has_azimuth:
        hint = _state_hint(it)
        if hint:
            return f"同一个道具「{it}」的样子——{hint}，机位在斜上方俯角三十度"
        return f"同一个道具处于「{it}」的状态，外形与结构和其它区明显不同，机位在斜上方俯角三十度"
    return f"{it}，机位在斜上方俯角三十度，看得到这一侧的外轮廓，也看得到顶部的表面"


def compose_prop(
    mode: str, layout_id: str, prop: str,
    ref: str = "none", ink: str = "color",
    style: str = "写实摄影", style_custom: bool = False, style_text_val: str = "",
    line_style: str | None = None,
    allow_empty: bool = False,
    ref_no: int = 1,
) -> str:
    """道具设定图拼装：复用角色五段式，版式换成多视图/单视图。

    ref_no：参考图在提交给 ComfyUI 的图片序列里排第几张。道具图生图会在最前面
    垫一张纯白占位图（把用户图从「要编辑的原图」位挪到「参考」位），此时是 2。
    """
    d = lib(PROP).load()
    layouts = {str(x["id"]): x for x in d["layouts"]}
    if mode not in ("t2i", "i2i"):
        raise ExtSheetError(f"没有这个模式：{mode}")
    if layout_id not in layouts:
        raise ExtSheetError(f"没有这个版式：{layout_id}")
    lay = layouts[layout_id]
    prop = str(prop or "").strip()
    if not prop and not allow_empty:
        raise ExtSheetError("先写道具描述")
    if len(prop) > MAX_SCENE:
        raise ExtSheetError(f"道具描述太长了（{len(prop)} 字），控制在 {MAX_SCENE} 字以内")
    if ref not in ("none", "lock"):
        raise ExtSheetError("参考方式只能是 无参考图 / 锁定同道具")

    pieces: list[str] = []
    # 图生图时模型会优先照抄参考图里的主体（常常是画面里最大那件），
    # 把「只画这一件」顶到最前面，用文字压过图片，才有机会选对道具。
    if ref == "lock" and prop:
        pieces.append(f"这次只画这一件东西：{_prop_brief(prop, {'source': 'auto', 'items': []})}。"
                      f"图 {ref_no} 只用来确认这一件的造型与材质，不是要照着整张图重画。")
    if layout_id == "MV":
        plan = _view_plan(prop)
        if plan["n"] <= 1:
            # 对称道具只剩一个视角，这时「多视图」那句话全是废话（还容易勾出分身），
            # 直接借单视图版式的头部与画面要求，只补一句这一件怎么摆。
            sv = layouts.get("SV") or lay
            pieces.append(str(sv.get("head") or lay.get("head") or ""))
            pieces.append(f"这一件是：{_zone_text(plan['items'][0], 1)}。")
            pieces.append(str(sv.get("cells") or lay.get("cells") or ""))
        else:
            zone_rule, names = _zones(plan["n"])
            lines = []
            for i, it in enumerate(plan["items"]):
                nm = names[i] if i < len(names) else f"第 {i + 1} 区"
                lines.append(f"{nm}：{_zone_text(it, i + 1)}。")
            head = str(lay.get("head") or "").replace("{zone_rule}", zone_rule)
            cells = str(lay.get("cells") or "").replace("{cells_text}", "".join(lines))
            pieces.append(head)
            pieces.append(cells)
        cells = ""
    else:
        pieces.append(str(lay.get("head") or "").strip())
        pieces.append(str(lay.get("cells") or ""))
    if ref == "lock":
        blocks = d.get("blocks", {})
        r1 = str(blocks.get("R1", {}).get("text", ""))
        pieces.append(r1.replace("{n}", str(ref_no)))
        if layout_id == "MV":
            pieces.append(str(blocks.get("SINGLE", {}).get("text", "")))

    if ink == "line":
        pieces.append(line_style_text(line_style, PROP))
        use_style = style_text_val if style_custom else style
        if use_style:
            pieces.append(line_overlay(use_style))
        variables = f"道具：{_prop_brief(prop, plan if layout_id == 'MV' else {'source': 'auto', 'items': []})}" if prop else ""
    else:
        use_style = style_text_val if style_custom else style
        st = style_text(use_style, PROP) if use_style else ""
        if len(st) > MAX_STYLE:
            raise ExtSheetError("风格描述太长了，一两句就够")
        # plan 只在 MV 分支赋值；SV/其它版式要用默认占位，否则会访问未定义变量
        p = plan if layout_id == "MV" else {"source": "auto", "items": []}
        variables = (f"道具：{_prop_brief(prop, p)}\n" if prop else "") + (f"风格：{st}" if st else "")
    if variables:
        pieces.append(variables)
    pieces.append(str(d.get("blocks", {}).get("TAIL", {}).get("text", "")))
    return _clean("\n".join(p for p in pieces if str(p).strip()))


def brief(kind: str, layout_id: str, text: str, mode: str = "", ink: str = "", sub: str = "") -> str:
    d = lib(kind).load()
    lay = next((x for x in d["layouts"] if str(x["id"]) == layout_id), {})
    head = str(text or "").strip().replace("\n", " ")
    if len(head) > 30:
        head = head[:30] + "…"
    bits = [str(lay.get("name", layout_id))]
    if sub:
        s = sub_of(kind, layout_id, sub)
        if s and s.get("name"):
            bits[-1] = f"{bits[-1]} · {s['name']}"
    if mode:
        bits.append(str(mode).upper())
    if ink and ink in ("color", "line"):
        bits.append("线稿" if ink == "line" else "彩色")
    if head:
        bits.append(head)
    return " · ".join(bits)


# ------------------------------------------------------------------ 组合
def _combos_scene() -> list[dict[str, Any]]:
    d = lib(SCENE).load()
    out: list[dict[str, Any]] = []
    for m in d["modes"]:
        mid = str(m["id"])
        for lay in d["layouts"]:
            if mid not in str(lay.get("mode", "")).split(","):
                continue
            inks = ["color", "line"] if m.get("ink") else ["-"]
            nochars = ["yes", "no"] if m.get("nochar") else ["-"]
            subs = [{"id": str(s["id"]), "name": str(s.get("name", ""))}
                    for s in (lay.get("subs") or []) if s.get("id")]
            for ink in inks:
                for nc in nochars:
                    cid = f"{lay['id']}.{mid}.{ink}.{nc}"
                    out.append({
                        "id": cid, "layout": lay["id"], "layoutName": lay["name"],
                        "mode": mid, "modeName": str(m["name"]), "group": f"{mid}.{lay['id']}",
                        "groupName": f"{lay.get('name', lay['id'])}·{m['name']}",
                        "subGroup": str(lay.get("group", "")),
                        "subs": subs,
                        "ink": ink, "nochar": nc, "ratio": "16:9",
                        "size": "1536 × 864", "aspect": lay["aspect"],
                        "wf": WF_OF[SCENE]["t2i"] if mid == "t2i" else WF_OF[SCENE]["i2i"],
                        "style": str(m.get("style", "none")),
                        "promptRequired": bool(m.get("promptRequired")),
                        "name": f"{lay['name']} · {m['name']}",
                    })
    return out


def _combos_prop() -> list[dict[str, Any]]:
    d = lib(PROP).load()
    out: list[dict[str, Any]] = []
    for m in d["modes"]:
        mid = str(m["id"])
        for lay in d["layouts"]:
            for ink in ("color", "line"):
                for ref in ("none", "lock"):
                    cid = f"{lay['id']}.{mid}.{ink}.{ref}"
                    out.append({
                        "id": cid, "layout": lay["id"], "layoutName": lay["name"],
                        "mode": mid, "modeName": str(m["name"]), "group": f"{mid}.{lay['id']}",
                        "groupName": f"{lay['name']}·{m['name']}", "subGroup": "",
                        "ink": ink, "ref": ref, "ratio": "16:9", "size": "1536 × 864",
                        "aspect": lay["aspect"], "wf": WF_OF[PROP]["t2i"] if mid == "t2i" else WF_OF[PROP]["i2i"],
                        "style": str(m.get("style", "none")), "promptRequired": True,
                        "name": f"{lay['name']} · {m['name']}",
                    })
    return out


def all_combos(kind: str) -> list[dict[str, Any]]:
    return _combos_scene() if kind == SCENE else _combos_prop()


def meta(kind: str, cid: str) -> dict[str, Any]:
    item = next((c for c in all_combos(kind) if c["id"] == cid), None)
    if not item:
        raise ExtSheetError(f"没有这个组合：{cid}")
    cap = config.capability(CAP_OF[kind]) or {}
    if item["wf"] not in (cap.get("workflows") or []):
        raise ExtSheetError("这个能力没配对应的子工作流，先回管理台补上")
    return item


def parse(kind: str, cid: str) -> dict[str, Any] | None:
    return next((c for c in all_combos(kind) if c["id"] == cid), None)


# ------------------------------------------------------------------ 参数/档位
def _fields(wf_name: str) -> list[dict[str, Any]]:
    wf = config.workflow(wf_name) or {}
    return [dict(f) for f in (wf.get("fields") or []) if f.get("node") and f.get("field")]


def _size_node(wf_name: str) -> dict[str, Any] | None:
    for name in ("aspect_ratio", "width"):
        found = next((f for f in _fields(wf_name) if f["field"] == name), None)
        if found:
            return found
    return None


def layout_params(kind: str, cid: str) -> dict[str, Any]:
    info = meta(kind, cid)
    node = _size_node(info["wf"])
    if not node:
        return {}
    nid = str(node["node"])
    d = lib(kind).load()
    lay = next((x for x in d["layouts"] if str(x["id"]) == info["layout"]), {})
    names = {str(f["field"]) for f in _fields(info["wf"]) if str(f["node"]) == nid}
    fields: dict[str, Any] = {"aspect_ratio": lay.get("aspect")}
    if "megapixels" in names:
        fields["megapixels"] = lay.get("megapixels")
    if "multiple" in names:
        fields["multiple"] = lay.get("multiple")
    return {nid: {k: v for k, v in fields.items() if v not in (None, "")}}


def approx_size(kind: str, cid: str, params: dict[str, Any]) -> str:
    info = meta(kind, cid)
    d = lib(kind).load()
    lay = next((x for x in d["layouts"] if str(x["id"]) == info["layout"]), {})
    aspect = str(lay.get("aspect", ""))
    mp = float(lay.get("megapixels", 1) or 1)
    mult = int(lay.get("multiple", 16) or 16)
    node = _size_node(info["wf"])
    if node:
        got = (params.get(str(node["node"])) or {})
        aspect = str(got.get("aspect_ratio", aspect))
        mp = float(got.get("megapixels", mp) or mp)
        mult = int(got.get("multiple", mult) or mult) or 8
    wr, hr = ASPECT_MAP.get(aspect, (1, 1))
    scale = math.sqrt(mp * 1024 * 1024 / (wr * hr))
    w = round(wr * scale / mult) * mult
    h = round(hr * scale / mult) * mult
    return f"{w} × {h}"


def _custom_grades(kind: str, cid: str) -> list[dict[str, Any]]:
    info = meta(kind, cid)
    table = (config.raw.get("extGrades") or {}).get(kind) or {}
    return [copy.deepcopy(g) for g in (table.get(info["group"]) or []) if isinstance(g, dict) and g.get("name")]


def master_grade(kind: str, cid: str) -> dict[str, Any]:
    info = meta(kind, cid)
    params = layout_params(kind, cid)
    return {"name": MASTER, "locked": True, "params": params,
            "note": f"{info['layoutName']} 16:9，这一档就是组合的完整配方",
            "size": approx_size(kind, cid, params)}


def grades(kind: str, cid: str) -> list[dict[str, Any]]:
    out = [master_grade(kind, cid)]
    for g in _custom_grades(kind, cid):
        params = effective_params(kind, cid, str(g["name"]))
        out.append({"name": str(g["name"]), "locked": False,
                    "params": copy.deepcopy(g.get("params") or {}),
                    "note": str(g.get("note") or ""),
                    "size": approx_size(kind, cid, params)})
    return out


def find_grade(kind: str, cid: str, name: str) -> dict[str, Any] | None:
    return next((g for g in grades(kind, cid) if g["name"] == name), None)


def effective_params(kind: str, cid: str, name: str) -> dict[str, Any]:
    merged = copy.deepcopy(layout_params(kind, cid))
    if name == MASTER:
        return merged
    g = next((x for x in _custom_grades(kind, cid) if x["name"] == name), None)
    if not g:
        raise ExtSheetError(f"这个组合没有档位「{name}」")
    for nid, fields in (g.get("params") or {}).items():
        if isinstance(fields, dict):
            merged.setdefault(str(nid), {}).update(fields)
    return merged


def tag(kind: str, cid: str, name: str) -> str:
    return f"{kind}_{meta(kind, cid)['group']}__{name}"


def _check(field: str, value: Any) -> Any:
    if field == "aspect_ratio":
        if str(value) not in ASPECT_MAP:
            raise ExtSheetError(f"宽高比只能是工作流支持的选项，收到「{value}」")
        return str(value)
    if field == "megapixels":
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise ExtSheetError("百万像素要填数字") from None
        if not 0.05 <= n <= 8:
            raise ExtSheetError("百万像素建议在 0.05 ~ 8 之间")
        return n
    if field == "multiple":
        try:
            n = int(float(value))
        except (TypeError, ValueError):
            raise ExtSheetError("对齐倍数要填整数") from None
        if not 4 <= n <= 128:
            raise ExtSheetError("对齐倍数建议在 4 ~ 128 之间")
        return n
    if field == "cfg":
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise ExtSheetError("CFG 要填数字") from None
        if not 0 <= n <= 30:
            raise ExtSheetError("CFG 建议在 0 ~ 30 之间")
        return n
    if field == "sigmas":
        text = str(value or "").strip()
        if not _SIGMAS_RE.match(text) or len(text.split(",")) < 2:
            raise ExtSheetError("sigma 序列要写成一串数字，逗号分隔，例如 1.0, 0.7, 0.3, 0")
        return text
    return value


def save_grades(kind: str, cid: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    info = meta(kind, cid)
    allowed: dict[str, set[str]] = {}
    field_node: dict[str, str] = {}
    for f in _fields(info["wf"]):
        nid, field = str(f["node"]), str(f["field"])
        allowed.setdefault(nid, set()).add(field)
        field_node.setdefault(field, nid)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name or len(name) > 12:
            raise ExtSheetError("档位名不能为空，且不超过 12 字")
        if name in MASTER_ALIASES:
            raise ExtSheetError(f"「{MASTER}」是内置档位，不许自己建")
        if name in seen:
            raise ExtSheetError(f"档位名「{name}」重复了")
        seen.add(name)
        params: dict[str, Any] = {}
        for nid, fields in (row.get("params") or {}).items():
            nid = str(nid)
            if nid not in allowed:
                mapped = field_node.get(nid)
                if not mapped:
                    raise ExtSheetError(f"节点 {nid} 不在这个工作流的可调参数里")
                params.setdefault(mapped, {})[nid] = _check(nid, fields)
                continue
            for field, value in (fields or {}).items():
                if str(field) not in allowed[nid]:
                    raise ExtSheetError(f"节点 {nid} 没有可调参数「{field}」")
                if value in (None, ""):
                    continue
                params.setdefault(nid, {})[str(field)] = _check(str(field), value)
        out.append({"name": name, "note": str(row.get("note") or ""), "params": params})
    raw = copy.deepcopy(config.raw)
    ext = raw.setdefault("extGrades", {}) or {}
    raw["extGrades"] = ext
    scope = ext.setdefault(kind, {}) or {}
    ext[kind] = scope
    if out:
        scope[info["group"]] = out
    else:
        scope.pop(info["group"], None)
    config.replace(raw)
    return grades(kind, cid)


def _flatten(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fields in (params or {}).values():
        if isinstance(fields, dict):
            out.update(fields)
    return out


def view(kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for info in all_combos(kind):
        try:
            grows = []
            for g in grades(kind, info["id"]):
                grows.append({"name": g["name"], "locked": g["locked"], "note": g["note"],
                              "size": g["size"], "params": _flatten(g["params"])})
            out.append({**info, "grades": grows,
                        "fields": [{"node": str(f["node"]), "field": str(f["field"])}
                                   for f in _fields(info["wf"])]})
        except ExtSheetError:
            continue
    return out


def update_template(kind: str, patch: dict[str, Any]) -> dict[str, Any]:
    """管理台模板编辑：只允许改 styles（风格预设），其余只读固定配方。"""
    p = lib(kind).path
    d = copy.deepcopy(lib(kind).load())
    if "styles" in patch:
        styles = patch["styles"]
        if not isinstance(styles, list) or not styles:
            raise ExtSheetError("风格预设不能为空")
        for s in styles:
            if not str(s.get("name") or "").strip() or not str(s.get("text") or "").strip():
                raise ExtSheetError("每个风格都要有名字和描述")
        # 场景/道具库里 styles 为空数组（运行时复用角色库），这里写到本库以覆盖
        d["styles"] = styles
    lib(kind).save(d)
    return library_view(kind)
