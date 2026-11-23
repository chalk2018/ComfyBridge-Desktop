"""提示词模板库（1.06 新增）：提示词与标签全部落在 MD 文件里。

设计要点
--------
* 一个分类两个文件：图片.md（文生图）/ 图片-含参.md（参考生图），视频、音频同理，
  都放在 data/prompt_templates/。分类仍是「图片 / 视频 / 音频」三类，页面选一类时
  把这两个文件一起读进来；一条模板带参考素材（图片/视频/音频）就落到「-含参」那份。
  标签单独放在 标签.md，也是纯 Markdown，方便直接阅读和手工备份。
* 参考素材（图片/视频/音频本身）不放项目目录，统一存到成果目录下的
  「提示词模板库」文件夹（<ComfyUI输出>/ComfyBridgeOutput/提示词模板库/），
  和成品归档目录同一个规则，删掉整个输出目录就等于清空素材。
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
from urllib.parse import quote

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .config import ROOT, config

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])

BASE_DIR = ROOT / "data" / "prompt_templates"
CATEGORIES: dict[str, str] = {"image": "图片", "video": "视频", "audio": "音频"}
CATEGORY_FILES: dict[str, str] = {"image": "图片.md", "video": "视频.md", "audio": "音频.md"}
# 带参考素材的模板单独存一份：文生 / 参考生两组数据互不影响，手改也好对照
PARAM_SUFFIX = "-含参"
TAG_FILE = "标签.md"
MAX_TAGS_PER_TEMPLATE = 10
MAX_MEDIA_PER_TEMPLATE = 20

# 参考素材：目录名与成品目录同级，扩展名按页面用途分三类
MEDIA_DIR_NAME = "提示词模板库"
MEDIA_KINDS: dict[str, str] = {"image": "图片", "video": "视频", "audio": "音频"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
VIDEO_MAX_MB = 256
AUDIO_MAX_MB = 64
MEDIA_TYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".m4v": "video/mp4",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg",
    ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif",
    ".tif": "image/tiff", ".tiff": "image/tiff",
}
# 素材文件名只认这个形状：既防目录穿越，也避免误删用户自己放进目录的别的文件
_SAFE_MEDIA = re.compile(r"^ptpl_[A-Za-z0-9]{4,40}\.[A-Za-z0-9]{1,6}$")

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
    media: list[dict[str, Any]] = Field(default_factory=list)


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


def _paths_for(category: str) -> tuple[Path, Path]:
    """一个分类的两份数据文件：(文生 …, 参考生 …)。"""
    return (BASE_DIR / CATEGORY_FILES[category],
            BASE_DIR / f"{CATEGORIES[category]}{PARAM_SUFFIX}.md")


def media_dir() -> Path:
    """参考素材存放目录：<成果目录>/提示词模板库/（已存在就直接用）。"""
    path = Path(config.service.archiveDir).resolve() / MEDIA_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


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


# ---------------------------------------------------------------- 参考素材
def _clean_media(raw: Any, *, check_file: bool = False) -> list[dict[str, Any]]:
    """把前端/文件里的素材记录洗成合法结构：类型不对、文件名不合法的一律丢掉。"""
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    root: Path | None = None
    if check_file:
        try:
            root = media_dir()
        except OSError:
            root = None
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        name = str(item.get("file") or "").strip()
        if kind not in MEDIA_KINDS or not _SAFE_MEDIA.match(name) or name in seen:
            continue
        if root is not None and not (root / name).is_file():
            continue                     # 文件被手动删掉了，别留一个点不开的空缩略图
        rec: dict[str, Any] = {
            "id": str(item.get("id") or "").strip() or "m_" + uuid.uuid4().hex[:10],
            "kind": kind,
            "name": str(item.get("name") or name),
            "file": name,
            "size": int(item.get("size") or 0),
        }
        try:
            if item.get("duration") is not None:
                rec["duration"] = round(float(item["duration"]), 2)
        except (TypeError, ValueError):
            pass
        seen.add(name)
        out.append(rec)
    return out[:MAX_MEDIA_PER_TEMPLATE]


def _media_files(media: list[dict[str, Any]]) -> set[str]:
    return {str(m.get("file") or "") for m in media if m.get("file")}


def _drop_media_files(files: set[str]) -> None:
    """删除素材文件：只认本目录里形如 ptpl_xxx.ext 的名字。"""
    if not files:
        return
    try:
        root = media_dir()
    except OSError:
        return
    for name in files:
        if not _SAFE_MEDIA.match(name):
            continue
        try:
            (root / name).unlink(missing_ok=True)
        except OSError:
            pass


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

        def meta_list(key: str) -> list[Any]:
            try:
                got = json.loads(meta.get(key, "[]"))
            except (ValueError, TypeError):
                return []
            return got if isinstance(got, list) else []

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
            "media": _clean_media(meta_list("media")),
            "created": meta_text("created"),
            "updated": meta_text("updated"),
        })
    return out


def _render_template(t: dict[str, Any]) -> str:
    fence = _fence_for(t.get("content", ""))
    lines = [
        f'<!-- CBTPL id="{t["id"]}" -->',
        "title: " + json.dumps(t.get("title", ""), ensure_ascii=False),
        "tags: " + json.dumps(t.get("tags", []), ensure_ascii=False),
    ]
    # 只有真的带素材才写这一行：老数据重写后与原来逐字节一致，不会平白多出空字段
    media = t.get("media") or []
    if media:
        lines.append("media: " + json.dumps(media, ensure_ascii=False))
    lines += [
        "created: " + json.dumps(t.get("created", ""), ensure_ascii=False),
        "updated: " + json.dumps(t.get("updated", ""), ensure_ascii=False),
        fence,
        t.get("content", ""),
        fence,
        f'<!-- CBTPL-END id="{t["id"]}" -->',
        "",
    ]
    return "\n".join(lines)


def _write_md(path: Path, category: str, templates: list[dict[str, Any]], param: bool) -> None:
    """写一份分类数据文件；已经存在的文件沿用它的抬头，只重写记录部分。

    为什么要沿用抬头：现有 md 的抬头是人工写过的话（「精品提示词模板」等），
    保存一次卡片就把它改成通用文案属于无谓的破坏；记录本身才是数据。
    """
    head = ""
    try:
        old = _read(path)
        cut = old.find("<!-- CBTPL")
        if cut > 0:
            head = old[:cut]
    except OSError:
        head = ""
    if not head.strip():
        kind = "含参提示词模板" if param else "提示词模板"
        head = f"# {CATEGORIES[category]} · {kind}\n\n" \
               "> ComfyBridge 提示词模板库自动维护。请尽量在页面里编辑，手改后刷新页面即可生效。\n"
        if param:
            head += ("\n> 这一份是「参考生」模板：带参考图片 / 视频 / 音频，素材本体存放在成果目录的"
                     "「提示词模板库」文件夹里，这里只记文件名。\n")
        head += "\n"
    body = "".join(_render_template(t) + "\n" for t in templates)
    _atomic_write(path, head + body)


def _save_category(category: str, templates: list[dict[str, Any]]) -> None:
    """按「有没有参考素材」分回两个文件：带素材的进 -含参.md。"""
    base_path, param_path = _paths_for(category)
    plain = [t for t in templates if not t.get("media")]
    param = [t for t in templates if t.get("media")]
    _write_md(base_path, category, plain, param=False)
    _write_md(param_path, category, param, param=True)


def _load_category(category: str) -> list[dict[str, Any]]:
    """页面上的一个分类 = 文生那份 + 参考生那份，合成一个列表。"""
    out: list[dict[str, Any]] = []
    for path in _paths_for(category):
        out += _parse_templates(_read(path), category)
    return out


def load_all() -> dict[str, Any]:
    with _LOCK:
        templates: list[dict[str, Any]] = []
        for cat in CATEGORIES:
            templates += _load_category(cat)
        templates.sort(key=lambda t: t.get("updated", ""), reverse=True)
        try:
            media_root = str(media_dir())
        except OSError:
            media_root = ""
        return {"templates": templates, "tags": load_tags(), "mediaDir": media_root}


def _find(category: str, tpl_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    items = _load_category(category)
    return items, next((x for x in items if x["id"] == tpl_id), None)



def _validate(payload: TemplatePayload) -> str:
    cat = _category(payload.category)
    if not cat:
        raise HTTPException(400, "请选择分类：图片 / 视频 / 音频")
    if not payload.title.strip():
        raise HTTPException(400, "标题不能为空")
    if len(payload.tags) > MAX_TAGS_PER_TEMPLATE:
        raise HTTPException(400, f"一个模板最多 {MAX_TAGS_PER_TEMPLATE} 个标签")
    if len(payload.media or []) > MAX_MEDIA_PER_TEMPLATE:
        raise HTTPException(400, f"一个模板最多 {MAX_MEDIA_PER_TEMPLATE} 个素材")
    return cat


def _attach_media(tpl: dict[str, Any], media: list[dict[str, Any]]) -> None:
    """把前端带来的素材记录挂到模板上（只留真实存在的文件）。"""
    tpl["media"] = _clean_media(media, check_file=True)



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
            items = _load_category(cat)
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
        _attach_media(tpl, body.media or [])
        items = _load_category(cat)
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
        _attach_media(updated, body.media or [])
        src_cat = old["category"]
        if src_cat == cat:
            items = [updated if x["id"] == tpl_id else x for x in old_cat]
            _save_category(cat, items)
        else:
            _save_category(src_cat, [x for x in old_cat if x["id"] != tpl_id])
            dest = _load_category(cat)
            dest.append(updated)
            _save_category(cat, dest)
        # 卡片上被移除的素材，文件一并删掉：不然成果目录会被没人引用的素材堆满
        _drop_media_files(_media_files(old.get("media") or []) - _media_files(updated["media"]))
    return {"ok": True, "data": updated}


@router.delete("/item/{tpl_id}")
async def prompt_template_delete(tpl_id: str) -> dict[str, Any]:
    with _LOCK:
        for cat in CATEGORIES:
            items = _load_category(cat)
            hit = next((x for x in items if x["id"] == tpl_id), None)
            if hit:
                _save_category(cat, [x for x in items if x["id"] != tpl_id])
                _drop_media_files(_media_files(hit.get("media") or []))
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
            items = _load_category(cat)
            kept = [x for x in items if x["id"] not in wanted]
            n = len(items) - len(kept)
            if n:
                _save_category(cat, kept)
                deleted += n
                gone: set[str] = set()
                for x in items:
                    if x["id"] in wanted:
                        gone |= _media_files(x.get("media") or [])
                _drop_media_files(gone)
    return {"ok": True, "data": {"deleted": deleted}}


# ---------------------------------------------------------------- 参考素材接口
def _guess_suffix(file: UploadFile) -> str:
    """扩展名缺失时按 content-type 补一个（浏览器有时只给 MIME）。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix in IMAGE_EXT | VIDEO_EXT | AUDIO_EXT:
        return suffix
    guess = (file.content_type or "").split("/")[-1].lower().split(";")[0]
    return {"jpeg": ".jpg", "jpg": ".jpg", "pjpeg": ".jpg", "png": ".png",
            "webp": ".webp", "bmp": ".bmp", "gif": ".gif", "tiff": ".tif", "x-tiff": ".tif",
            "mp4": ".mp4", "webm": ".webm", "quicktime": ".mov", "x-matroska": ".mkv",
            "mpeg": ".mp3", "wav": ".wav", "x-wav": ".wav", "flac": ".flac",
            "ogg": ".ogg", "m4a": ".m4a", "aac": ".aac"}.get(guess, suffix)


@router.post("/media")
async def prompt_template_media_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传一张参考图片 / 一段参考视频 / 一段参考音频，存进「提示词模板库」目录。"""
    suffix = _guess_suffix(file)
    if suffix in VIDEO_EXT:
        kind, cap_mb = "video", VIDEO_MAX_MB
    elif suffix in AUDIO_EXT:
        kind, cap_mb = "audio", max(AUDIO_MAX_MB, int(config.service.uploadMaxMB or 0))
    elif suffix in IMAGE_EXT:
        kind, cap_mb = "image", int(config.service.uploadMaxMB or 32)
    else:
        raise HTTPException(400, "只收图片、视频或音频文件")
    content = await file.read()
    if not content:
        raise HTTPException(400, "这个文件是空的")
    if len(content) > cap_mb * 1024 * 1024:
        label = MEDIA_KINDS[kind]
        raise HTTPException(400, f"单个{label}不能超过 {cap_mb}MB")
    try:
        root = media_dir()
    except OSError as exc:
        raise HTTPException(400, f"素材目录建不出来：{exc}") from exc
    name = f"ptpl_{uuid.uuid4().hex[:12]}{suffix}"
    dest = root / name
    try:
        dest.write_bytes(content)
    except OSError as exc:
        raise HTTPException(400, f"素材存不进去（目录可能满了或没有写权限）：{exc}") from exc
    rec: dict[str, Any] = {"id": "m_" + uuid.uuid4().hex[:10], "kind": kind,
                           "name": file.filename or name, "file": name, "size": len(content)}
    if kind in ("video", "audio"):
        # 顺手探一次时长：缩略图上能标「12.4 秒」，探不到也不影响上传
        try:
            from .mediaprobe import duration as probe_duration
            got = probe_duration(dest)
            if got:
                rec["duration"] = got
        except Exception:  # noqa: BLE001 - 探测失败只是少一个数字
            pass
    return {"ok": True, "data": rec}


@router.get("/media/{filename}")
async def prompt_template_media_get(
    request: Request, filename: str, download: int = 0, name: str = Query(""),
) -> Response:
    """素材预览 / 播放 / 下载。

    视频和音频要能拖动进度条就必须支持 Range（206），否则整个文件下完才开播。
    """
    if not _SAFE_MEDIA.match(filename):
        raise HTTPException(404, "素材不存在")
    try:
        path = media_dir() / filename
    except OSError:
        raise HTTPException(404, "素材不存在")
    if not path.is_file():
        raise HTTPException(404, "素材不存在")
    if download:
        # 下载用原始文件名（用户上传时叫什么就给什么），落盘名只是内部编号
        label = (name or filename).replace("\\", "_").replace("/", "_").strip() or filename
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(label)}"}
        return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(),
                                                            "application/octet-stream"),
                            headers=headers)
    size = path.stat().st_size
    rng = request.headers.get("range") or ""
    if rng.startswith("bytes="):
        try:
            start_s, _, end_s = rng[6:].partition("-")
            start = int(start_s or 0)
            end = int(end_s) if end_s else size - 1
            end = min(end, size - 1)
            if start > end:
                raise ValueError
        except ValueError:
            raise HTTPException(416, "Range 不合法")
        length = end - start + 1
        with path.open("rb") as fh:
            fh.seek(start)
            chunk = fh.read(length)
        return Response(chunk, status_code=206, headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(chunk)),
            "Content-Type": MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        })
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower())
                        or "application/octet-stream", headers={"Accept-Ranges": "bytes"})


@router.delete("/media/{filename}")
async def prompt_template_media_delete(filename: str) -> dict[str, Any]:
    """删素材文件：还被某条模板引用着就不让删（先把它从卡片上摘掉再保存）。"""
    if not _SAFE_MEDIA.match(filename):
        raise HTTPException(404, "素材不存在")
    with _LOCK:
        for cat in CATEGORIES:
            _base, param_path = _paths_for(cat)
            for tpl in _parse_templates(_read(param_path), cat):
                if filename in _media_files(tpl.get("media") or []):
                    raise HTTPException(400, "这个素材还挂在一个模板上，先从卡片上移除再删")
        _drop_media_files({filename})
    return {"ok": True}
