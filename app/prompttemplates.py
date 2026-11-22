"""提示词模板库（1.06 新增）：提示词与标签全部落在 MD 文件里。

设计要点
--------
* 一个分类一个文件：图片.md / 视频.md / 音频.md，放在 data/prompt_templates/。
  标签单独放在 标签.md，也是纯 Markdown，方便直接阅读和手工备份。
* 文件里的记录用 HTML 注释做边界，正文放在一段「长波浪线」围栏里；
  围栏长度按正文内容动态取，保证正文里出现同样的围栏也不会被误切开。
* 本模块完全独立：只在 main.py 里挂一个 router，不碰任务、工作流、画布那套状态。
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .config import ROOT

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])

BASE_DIR = ROOT / "data" / "prompt_templates"
CATEGORIES: dict[str, str] = {"image": "图片", "video": "视频", "audio": "音频"}
CATEGORY_FILES: dict[str, str] = {"image": "图片.md", "video": "视频.md", "audio": "音频.md"}
TAG_FILE = "标签.md"
MAX_TAGS_PER_TEMPLATE = 10

DEFAULT_TAGS: list[dict[str, str]] = [
    {"id": "tag_role", "name": "角色", "color": "#e0523a"},
    {"id": "tag_scene", "name": "场景", "color": "#2f6bff"},
    {"id": "tag_prop", "name": "道具", "color": "#e8a33a"},
    {"id": "tag_board", "name": "故事板", "color": "#7c6bd6"},
    {"id": "tag_firstlast", "name": "首尾帧", "color": "#2fa36b"},
    {"id": "tag_swap", "name": "角色替换", "color": "#d4509a"},
    {"id": "tag_music", "name": "音乐复刻", "color": "#0f9aa8"},
    {"id": "tag_voice", "name": "声音克隆", "color": "#8a6b3f"},
]

# 新建标签时的候选色：优先挑当前没被占用的
PALETTE = [
    "#e0523a", "#2f6bff", "#e8a33a", "#7c6bd6", "#2fa36b", "#d4509a",
    "#0f9aa8", "#8a6b3f", "#3f7d8c", "#b5533d", "#5b61c7", "#4a8b3a",
    "#c27a17", "#9a4f7d", "#167d70", "#5d6b8a",
]

_TPL_BEGIN = re.compile(r'^<!--\s*CBTPL\s+id="([^"]+)"\s*-->\s*$')
_FENCE = re.compile(r"^~{5,}\s*$")
_TAG_BLOCK = re.compile(
    r'<!--\s*CBTAG\s+id="([^"]+)"\s+color="([^"]+)"\s*-->\s*\n(.*?)\n\s*<!--\s*CBTAG-END\s*-->',
    re.S,
)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOCK = threading.RLock()


class TemplatePayload(BaseModel):
    id: str = ""
    title: str = ""
    content: str = ""
    category: str = "image"
    tags: list[str] = []


class TagPayload(BaseModel):
    id: str = ""
    name: str = ""
    color: str = ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _category(value: str) -> str:
    v = str(value or "").strip()
    if v in CATEGORIES:
        return v
    for key, name in CATEGORIES.items():
        if v == name:
            return key
    return ""


def _path_for(category: str) -> Path:
    return BASE_DIR / CATEGORY_FILES[category]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _atomic_write(path: Path, text: str) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _fence_for(content: str) -> str:
    """围栏必须比正文里任意连续波浪线都长，否则正文会把记录截断。"""
    longest = max((len(m.group(0)) for m in re.finditer(r"~+", content)), default=0)
    return "~" * max(5, longest + 3)


# ---------------------------------------------------------------- 标签
def _parse_tags(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in _TAG_BLOCK.finditer(text):
        name = m.group(3).strip()
        if name:
            out.append({"id": m.group(1), "name": name, "color": m.group(2)})
    return out


def load_tags() -> list[dict[str, str]]:
    with _LOCK:
        tags = _parse_tags(_read(BASE_DIR / TAG_FILE))
        if tags:
            return tags
        # 第一次运行：写入默认标签。用户之后删到空也算「有意留空」，不再自动补。
        if not (BASE_DIR / TAG_FILE).exists():
            tags = [dict(t) for t in DEFAULT_TAGS]
            _save_tags(tags)
        return tags


def _save_tags(tags: list[dict[str, str]]) -> None:
    lines = [
        "# 标签库",
        "",
        "> ComfyBridge 提示词模板库的标签。一个标签一个色，颜色不重复。",
        "",
    ]
    for t in tags:
        lines += [
            f'<!-- CBTAG id="{t["id"]}" color="{t["color"]}" -->',
            t["name"],
            "<!-- CBTAG-END -->",
            "",
        ]
    _atomic_write(BASE_DIR / TAG_FILE, "\n".join(lines) + "\n")


def _free_color(tags: list[dict[str, str]], preferred: str = "") -> str:
    used = {t["color"].lower() for t in tags}
    if preferred and _HEX.match(preferred) and preferred.lower() not in used:
        return preferred
    for c in PALETTE:
        if c.lower() not in used:
            return c
    # 颜色用完了就按色环生成一个，仍然尽量避开已用色
    i = len(tags)
    while True:
        c = "#%06x" % ((i * 0x2F3B7 % 0xFFFFFF) or 0x888888)
        if c.lower() not in used:
            return c
        i += 1


# ---------------------------------------------------------------- 模板
def _parse_templates(text: str, category: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        m = _TPL_BEGIN.match(lines[i])
        if not m:
            i += 1
            continue
        tpl_id = m.group(1)
        i += 1
        meta: dict[str, str] = {}
        while i < len(lines) and not _FENCE.match(lines[i]):
            line = lines[i]
            if ": " in line:
                k, v = line.split(": ", 1)
                meta[k.strip()] = v
            i += 1
        if i >= len(lines):
            break
        fence = lines[i].strip()
        i += 1
        body: list[str] = []
        while i < len(lines) and lines[i].strip() != fence:
            body.append(lines[i])
            i += 1
        if i < len(lines):
            i += 1  # 跳过收尾围栏

        def meta_text(key: str, default: str = "") -> str:
            raw = meta.get(key, "")
            try:
                return str(json.loads(raw))
            except (ValueError, TypeError):
                return raw

        try:
            tag_ids = json.loads(meta.get("tags", "[]"))
            if not isinstance(tag_ids, list):
                tag_ids = []
        except (ValueError, TypeError):
            tag_ids = []
        out.append({
            "id": tpl_id,
            "title": meta_text("title"),
            "content": "\n".join(body),
            "category": category,
            "tags": [str(x) for x in tag_ids],
            "created": meta_text("created"),
            "updated": meta_text("updated"),
        })
    return out


def _render_template(t: dict[str, Any]) -> str:
    fence = _fence_for(t.get("content", ""))
    return "\n".join([
        f'<!-- CBTPL id="{t["id"]}" -->',
        "title: " + json.dumps(t.get("title", ""), ensure_ascii=False),
        "tags: " + json.dumps(t.get("tags", []), ensure_ascii=False),
        "created: " + json.dumps(t.get("created", ""), ensure_ascii=False),
        "updated: " + json.dumps(t.get("updated", ""), ensure_ascii=False),
        fence,
        t.get("content", ""),
        fence,
        f'<!-- CBTPL-END id="{t["id"]}" -->',
        "",
    ])


def _save_category(category: str, templates: list[dict[str, Any]]) -> None:
    lines = [
        f"# {CATEGORIES[category]} · 提示词模板",
        "",
        "> ComfyBridge 提示词模板库自动维护。请尽量在页面里编辑，手改后刷新页面即可生效。",
        "",
    ]
    for t in templates:
        lines.append(_render_template(t))
    _atomic_write(_path_for(category), "\n".join(lines) + "\n")


def load_all() -> dict[str, Any]:
    with _LOCK:
        templates: list[dict[str, Any]] = []
        for cat in CATEGORIES:
            templates += _parse_templates(_read(_path_for(cat)), cat)
        templates.sort(key=lambda t: t.get("updated", ""), reverse=True)
        return {"templates": templates, "tags": load_tags()}


def _find(category: str, tpl_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    items = _parse_templates(_read(_path_for(category)), category)
    return items, next((x for x in items if x["id"] == tpl_id), None)


def _validate(payload: TemplatePayload) -> str:
    cat = _category(payload.category)
    if not cat:
        raise HTTPException(400, "请选择分类：图片 / 视频 / 音频")
    if not payload.title.strip():
        raise HTTPException(400, "标题不能为空")
    if len(payload.tags) > MAX_TAGS_PER_TEMPLATE:
        raise HTTPException(400, f"一个模板最多 {MAX_TAGS_PER_TEMPLATE} 个标签")
    return cat


# ---------------------------------------------------------------- 接口
@router.get("")
async def prompt_templates_all() -> dict[str, Any]:
    return {"ok": True, "data": load_all()}


@router.get("/tags")
async def prompt_tags_list() -> dict[str, Any]:
    return {"ok": True, "data": load_tags()}


@router.post("/tags")
async def prompt_tag_add(body: TagPayload = Body(...)) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    with _LOCK:
        tags = load_tags()
        if any(t["name"] == name for t in tags):
            raise HTTPException(400, "已经有同名标签了")
        tag = {"id": "tag_" + uuid.uuid4().hex[:10], "name": name,
               "color": _free_color(tags, body.color)}
        tags.append(tag)
        _save_tags(tags)
    return {"ok": True, "data": tag}


@router.put("/tags/{tag_id}")
async def prompt_tag_edit(tag_id: str, body: TagPayload = Body(...)) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    with _LOCK:
        tags = load_tags()
        tag = next((t for t in tags if t["id"] == tag_id), None)
        if not tag:
            raise HTTPException(404, "标签不存在")
        if any(t["name"] == name and t["id"] != tag_id for t in tags):
            raise HTTPException(400, "已经有同名标签了")
        others = [t for t in tags if t["id"] != tag_id]
        tag["name"] = name
        tag["color"] = _free_color(others, body.color)
        _save_tags(tags)
    return {"ok": True, "data": tag}


@router.delete("/tags/{tag_id}")
async def prompt_tag_delete(tag_id: str) -> dict[str, Any]:
    """删标签时，同时把它从所有模板上摘掉，避免卡片里留一个点不开的幽灵标签。"""
    with _LOCK:
        tags = load_tags()
        tag = next((t for t in tags if t["id"] == tag_id), None)
        if not tag:
            raise HTTPException(404, "标签不存在")
        tags = [t for t in tags if t["id"] != tag_id]
        _save_tags(tags)
        for cat in CATEGORIES:
            items = _parse_templates(_read(_path_for(cat)), cat)
            changed = False
            for t in items:
                if tag_id in t["tags"]:
                    t["tags"] = [x for x in t["tags"] if x != tag_id]
                    t["updated"] = _now()
                    changed = True
            if changed:
                _save_category(cat, items)
    return {"ok": True}


@router.post("")
async def prompt_template_add(body: TemplatePayload = Body(...)) -> dict[str, Any]:
    cat = _validate(body)
    now = _now()
    tpl = {
        "id": "tpl_" + uuid.uuid4().hex[:12],
        "title": body.title.strip(),
        "content": body.content,
        "category": cat,
        "tags": list(dict.fromkeys(body.tags)),
        "created": now,
        "updated": now,
    }
    with _LOCK:
        valid_tags = {t["id"] for t in load_tags()}
        tpl["tags"] = [x for x in tpl["tags"] if x in valid_tags]
        items = _parse_templates(_read(_path_for(cat)), cat)
        items.append(tpl)
        _save_category(cat, items)
    return {"ok": True, "data": tpl}


@router.put("/item/{tpl_id}")
async def prompt_template_edit(tpl_id: str, body: TemplatePayload = Body(...)) -> dict[str, Any]:
    cat = _validate(body)
    with _LOCK:
        old_cat, old = _find(body.category, tpl_id)
        # id 一定在它原来所在的分类文件里；从这里取权威的分类，防止前端传错
        if not old:
            for other in CATEGORIES:
                if other == cat:
                    continue
                old_cat, old = _find(other, tpl_id)
                if old:
                    break
        if not old:
            raise HTTPException(404, "模板不存在")
        valid_tags = {t["id"] for t in load_tags()}
        updated = {
            **old,
            "title": body.title.strip(),
            "content": body.content,
            "category": cat,
            "tags": [x for x in dict.fromkeys(body.tags) if x in valid_tags],
            "updated": _now(),
        }
        src_cat = old["category"]
        if src_cat == cat:
            items = [updated if x["id"] == tpl_id else x for x in old_cat]
            _save_category(cat, items)
        else:
            _save_category(src_cat, [x for x in old_cat if x["id"] != tpl_id])
            dest = _parse_templates(_read(_path_for(cat)), cat)
            dest.append(updated)
            _save_category(cat, dest)
    return {"ok": True, "data": updated}


@router.delete("/item/{tpl_id}")
async def prompt_template_delete(tpl_id: str) -> dict[str, Any]:
    with _LOCK:
        for cat in CATEGORIES:
            items = _parse_templates(_read(_path_for(cat)), cat)
            if any(x["id"] == tpl_id for x in items):
                _save_category(cat, [x for x in items if x["id"] != tpl_id])
                return {"ok": True}
    raise HTTPException(404, "模板不存在")


class BulkDeletePayload(BaseModel):
    ids: list[str]


@router.post("/items/bulk-delete")
async def prompt_templates_bulk_delete(body: BulkDeletePayload = Body(...)) -> dict[str, Any]:
    """批量删除：一次按分类分组、每类只读写一次盘。

    逐条调用单删接口会让 2000 条模板反复全量重写 MD 文件（几十秒卡死），
    这里先把要删的 id 聚成集合，每个分类只 parse/save 一次。返回实际删掉的条数。
    """
    wanted = set(body.ids)
    if not wanted:
        return {"ok": True, "data": {"deleted": 0}}
    deleted = 0
    with _LOCK:
        for cat in CATEGORIES:
            items = _parse_templates(_read(_path_for(cat)), cat)
            kept = [x for x in items if x["id"] not in wanted]
            n = len(items) - len(kept)
            if n:
                _save_category(cat, kept)
                deleted += n
    return {"ok": True, "data": {"deleted": deleted}}
