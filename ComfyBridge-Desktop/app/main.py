"""ComfyBridge FastAPI 入口：一个进程、一个端口，页面与接口都由它发。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, ConfigError, config
from .boardlib import BoardError
from .promptlib import SheetError, library as sheet_library
from .services import service
from .state import state
from . import audiolib, canvas, imagestylelib, release, videolib, videoext
from .prompttemplates import router as prompt_templates_router

WEB_DIR = ROOT / "web"
logger = logging.getLogger("comfybridge.api")
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
ALLOWED_VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
MAX_VIDEO_MB = 256
SHEET_CAP = "sheet.character"

MEDIA_TYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".m4v": "video/mp4",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
}


class SheetPayload(BaseModel):
    """角色设定图的结构化输入：服务端据此拼提示词，前端不用自己拼字符串。"""

    layout: str = "A"            # 版式 id：A 四视图横排 / B 四宫格 / C 单张全身
    character: str = ""          # 角色行：年龄身份 + 五官 + 发型 + 服装 + 标志物
    style: str = "写实摄影"       # 风格预设名或自写风格句
    styleCustom: bool = False    # 风格是不是「自定义…」（用于「基于上一条」精确还原）
    styleText: str = ""          # 自定义风格句
    ink: str = "color"           # color 彩色 / line 线稿
    lineStyle: str | None = None  # 线稿风格句 id（W1~W4）
    ref: str = "none"            # none 无参考 / lock 锁定同角色 / style 只借画风
    grade: str = ""              # 档位名：留空＝母版；只能是当前组合下存在的档位


class ExtSheetPayload(BaseModel):
    """1.03 场景/道具设定图的结构化输入（区别能力由 capId 决定）。"""

    kind: str = "scene"              # scene / prop
    mode: str = "t2i"                # 场景: t2i/i2i/expand；道具: t2i/i2i
    layout: str = "N"                # 版式 id
    sub: str = ""                    # 版式子选项（扩图：S/L/L45/L75/R45/R75），只改提示词
    text: str = ""                   # 场景描述 / 道具描述（扩图可空）
    ink: str = "color"               # color / line
    nochar: str = "yes"              # 场景禁角色开关 yes/no
    ref: str = "none"                # 道具参考方式 none/lock
    refSlots: dict[str, str] = Field(default_factory=dict)  # 场景: {ref/pano/bird/sketch: 上传 ref id}
    refs: list[str] = Field(default_factory=list)            # 预览时把已上传 ref id 带来，用于还原图序号
    style: str = "写实摄影"
    styleCustom: bool = False
    styleText: str = ""
    lineStyle: str | None = None
    grade: str = ""


class BoardPayload(BaseModel):
    """宫格分镜故事板的结构化输入：版式内逐格描述，服务端统一拼装。"""

    layout: str = "G4"                           # G4 / G6 / G9
    cells: list[str] = Field(default_factory=list)
    ink: str = "color"                           # color / line
    lineStyle: str | None = None                 # W1~W4
    grade: str = ""
    refs: list[str] = Field(default_factory=list)  # 预览时还原参考图张数（决定图序号声明）


class VideoPayload(BaseModel):
    """视频生成的结构化输入：模式 + 三个档位参数 + 故事板规格。"""

    mode: str = "t2v"                 # t2v / fl2v / ref2v / board
    ratio: str = "16:9"
    res: str = "768P"
    steps: int = 8
    length: int = 124                 # 帧数（旧草稿兼容；有时长秒数时以 duration 为准）
    duration: float | None = None     # 需求6：秒（4~15），数值输入框为准，24fps 折算 length
    grid: int = 4                     # 故事板宫格数：4 / 6 / 9
    cells: list[str] = Field(default_factory=list)


class VideoExtPayload(BaseModel):
    """视频生成扩展的结构化输入：子功能 + 宽高比 + 参考模式（档位走 body.grade）。"""

    func: str = "multiRefFast"
    ratio: str = "16:9"
    mode: str = "fl2v"                 # fl2v 首尾帧生成 / ref2v 参考生成
    duration: float | None = None


class AudioPayload(BaseModel):
    """音频生成的结构化输入。"""

    mode: str = "music"               # music / cover / tts / clone
    lyrics: str = ""
    duration: float | None = None


class ImageStylePayload(BaseModel):
    """图片生成的画法 / 风格：选项在页面上选，句子由服务端拼（口径只有一处）。"""

    ink: str = "color"                # color / line
    lineStyle: str | None = None      # 线稿风格句 id（W1~W4）
    style: str = ""
    styleCustom: bool = False
    styleText: str = ""


class ImagePreviewPayload(BaseModel):
    prompt: str = ""
    image: ImageStylePayload = Field(default_factory=ImageStylePayload)


def compose_image_prompt(prompt: str, image: ImageStylePayload) -> str:
    """图片生成的通用画法 / 风格句，提交与「看完整提示词」共用这一处，防止两边漂移。"""
    bits: list[str] = []
    try:
        lib = imagestylelib.library.view()
    except Exception:  # noqa: BLE001
        lib = {"line_styles": [], "styles": []}
    if image.ink == "line":
        ls = next((s for s in lib.get("line_styles", []) if s.get("id") == image.lineStyle), None)
        bits.append(ls["text"] if ls else "纯黑白线稿，只有黑色线条与纯白底色，不上色、不做灰调")
    else:
        if image.styleCustom:
            st = (image.styleText or "").strip()
        else:
            st = imagestylelib.library.style_text(image.style)
            if not st:
                st = (image.style or "").strip()
        if st:
            bits.append(st)
    if image.ink == "line":
        # 线稿尾缀：放在用户提示词之后，压住用户提示词里的具体颜色词（如"蓝色工装"），防止上色泄漏
        tail = "（线稿硬性要求：整张图为纯黑白单色线稿，所有物体一律不上色，画面中不存在任何彩色色块或彩色填充）"
        return (f"画面风格：{'；'.join(bits)}。\n{prompt}" + tail) if bits else prompt + tail
    return f"画面风格：{'；'.join(bits)}。\n{prompt}" if bits else prompt


class TaskRequest(BaseModel):
    capId: str = "image.generate"
    prompt: str = ""
    grade: str = "默认"
    refs: list[str] = Field(default_factory=list)
    wf: str | None = None  # 前端显式选择的子工作流（文生图/参考生图），None 时按参考图数量自动挑
    sheet: SheetPayload | None = None  # 角色设定图专用：结构化输入，服务端组装提示词
    ext: ExtSheetPayload | None = None  # 1.03 场景/道具设定图结构化输入
    board: BoardPayload | None = None   # 1.03 宫格分镜故事板结构化输入
    video: VideoPayload | None = None   # 1.04 视频生成
    videoExt: VideoExtPayload | None = None  # 视频生成扩展（独立模块）
    audio: AudioPayload | None = None   # 1.04 音频生成
    image: ImageStylePayload | None = None  # 1.04 图片生成的画法 / 风格
    archiveFolder: str | None = None    # 画布传入：成品落到画布自己的目录


class QueueRequest(BaseModel):
    grade: str = "默认"


class UIPatch(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class ConfigPatch(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class WorkflowPatch(BaseModel):
    fields: list[dict[str, Any]] | None = None
    gradePatch: dict[str, Any] | None = None


class GradeRequest(BaseModel):
    name: str = ""
    note: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class GradeOrderRequest(BaseModel):
    order: list[str] = Field(default_factory=list)


class FolderRequest(BaseModel):
    path: str = ""
    select: bool = True      # True = 在资源管理器里选中这个文件，而不是只打开目录


class FolderPickRequest(BaseModel):
    title: str = "选择目录"
    initial: str = ""        # 打开时定位到的初始目录


# 资源管理器风格的目录选择框（在服务器本机弹出，路径通过 stdout 返回）
_PICK_FOLDER_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = 'None'
$owner.Opacity = 0
$owner.Show()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = $env:CB_PICK_TITLE
$d.ShowNewFolderButton = $true
if ($env:CB_PICK_INITIAL -and (Test-Path -LiteralPath $env:CB_PICK_INITIAL)) {
  $d.SelectedPath = $env:CB_PICK_INITIAL
}
$r = $d.ShowDialog($owner)
$owner.Close()
if ($r -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.SelectedPath) }
"""


class BatchRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class RawText(BaseModel):
    text: str = ""


class DeleteItems(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    deleteFiles: bool = True


class AIOrder(BaseModel):
    capability: str = "image.generate"
    prompt: str = ""
    grade: str = "默认"
    references: list[str] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """配置损坏时仍把页面发出来：顶部红条指出问题，并允许在页面上直接改（需求 04 章）。"""
    release.load()  # 公开版的置灰能力清单（无 public_release.json 时全开放）
    try:
        config.load()
        service.start()
        # 素材索引补建：refs.json 丢了（或从旧版本升级）时按 uploads 里的文件名还原，
        # 否则画布上的资产节点重启后整片 404
        try:
            added = state.refs.scan_dir(config.resolve(config.service.uploadDir))
            if added:
                print(f"[ComfyBridge] 补回 {added} 个素材索引")
        except Exception:  # noqa: BLE001
            pass
        state.last_error = ""
    except Exception as exc:  # noqa: BLE001
        state.last_error = str(exc)
        print(f"[ComfyBridge] 配置错误：{exc}")
    yield
    try:
        service.comfy.stop()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="ComfyBridge", version="1.06", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
# 1.06：提示词模板库独立 router，不改任务/工作流/画布那套状态
app.include_router(prompt_templates_router)
# 右栏任务累积多了以后 /api/state 会到几 MB，每秒轮询一次没有压缩会很卡（浏览器甚至会被拖死）
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
_STATE_CACHE: dict[str, Any] = {"at": 0.0, "body": None}


@app.middleware("http")
async def _static_no_cache(request: Request, call_next):
    """前端静态文件一律 no-cache：本机工具经常改页面，浏览器按启发式缓存会用旧 JS。

    只加 Cache-Control（每次回源校验，etag 没变就 304），不影响任何接口。
    """
    resp = await call_next(request)
    if request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    if request.method != "GET":
        # 任何写操作（提交/删除/改配置）都让 /api/state 的 1.2 秒缓存立刻失效，
        # 否则点完生成后前端下一次拉状态可能还是旧数据
        _STATE_CACHE["body"] = None
    return resp


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, **extra}


def blocked(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason}


def _prepare_sheet(
    payload: SheetPayload, cap: dict[str, Any], refs: list[str]
) -> dict[str, Any]:
    """角色设定图：结构化输入 → (组合, 档位, 提示词, 工作流, 参数, 卡片摘要)。

    这里的分工必须清楚，否则「加入队列生成」会变成换了个配方：

    - 组合（版式 × 画法 × 参考方式）决定**配方的全部内容**：提示词段落、参考图怎么用、
      走哪条工作流、母版画幅。这部分只由用户在创作台选的东西决定，档位管不着。
    - 档位只提供**参数覆盖**（画幅 / 像素 / CFG / sigma），提示词一个字都不变。

    Raises:
        SheetError: 参考图与参考方式不匹配、组合或档位不存在、角色描述为空等。
    """
    from . import combos
    from .workflows import pick_workflow

    ref_mode = payload.ref if payload.ref in combos.REFS else "none"
    if refs and ref_mode == "none":
        # 传了图却没选参考方式：按「锁定同角色」处理（比静默丢掉参考图更符合直觉）
        ref_mode = "lock"
    if ref_mode != "none" and not refs:
        raise SheetError("「锁定同角色 / 只借画风」都要先上传一张参考图")
    layout = sheet_library.layout(payload.layout)
    cid = combos.combo_id(str(layout["id"]), payload.ink, ref_mode)
    info = combos.meta(cid)
    # 1.07：三种子功能各有独立工作流，组合已经算出该走哪条；
    # cap.rules 仍按参考图张数做通用兜底，但设定图以组合配方为准。
    wf_name = info["wf"]
    if wf_name not in (cap.get("workflows") or []):
        raise SheetError(f"能力里没有配置工作流「{wf_name}」，请先回管理台补上")
    grade_name = (payload.grade or combos.MASTER).strip() or combos.MASTER
    grade = combos.find_grade(cid, grade_name)
    if not grade:
        names = "、".join(g["name"] for g in combos.grades(cid))
        raise SheetError(f"「{info['name']}」没有档位「{grade_name}」（可选：{names}）")
    params = combos.effective_params(cid, grade_name)
    # 图生图纯转换（单人图转四视图 / 彩色转线稿）允许角色描述为空：
    # 提示词主体由版式句 + 参考声明构成，不写「角色：」这一行。
    prompt = sheet_library.compose(
        layout["id"], payload.character, payload.style,
        ink=payload.ink, ref=ref_mode, line_style=payload.lineStyle,
        allow_empty_character=(ref_mode != "none" and bool(refs)),
    )
    brief = sheet_library.brief(
        layout["id"], payload.character, ink=payload.ink, ref=ref_mode, style=payload.style,
    )
    return {
        "prompt": prompt,
        "wf": wf_name,
        "grade": grade_name,
        "params": params,
        "tag": combos.tag(cid, grade_name),
        "size": grade["size"],
        "brief": brief,
        "combo": info,
        "sheet": {
            "layout": layout["id"], "layoutName": layout["name"], "ratio": layout["ratio"],
            "ink": payload.ink, "ref": ref_mode, "style": payload.style,
            "styleCustom": payload.styleCustom, "styleText": payload.styleText,
            "character": payload.character.strip(), "lineStyle": payload.lineStyle or "",
            # 组合与档位要跟着任务走：换档重跑/基于上一条都得知道自己是哪套配方
            "combo": cid, "comboName": info["name"], "grade": grade_name,
        },
    }


def _prepare_ext(payload: ExtSheetPayload, refs: list[str]) -> dict[str, Any]:
    """场景/道具设定图：结构化输入 → (提示词, 组合, 档位, 参数, 工作流, 摘要)。"""
    from . import extsheets as ext
    if payload.kind not in (ext.SCENE, ext.PROP):
        raise ext.ExtSheetError(f"没有这个设定图类型：{payload.kind}")
    kind = payload.kind
    mode = payload.mode
    sub = ""          # 只有场景扩图有子选项；道具留空，写进卡片时字段对齐
    # 图序号按上传顺序：refs[0] → 图1，refs[1] → 图2 …
    id_to_no = {rid: i + 1 for i, rid in enumerate(refs)}
    if payload.kind == ext.SCENE:
        slots: dict[str, int] = {}
        for slot in ("ref", "pano", "bird", "sketch"):
            rid = payload.refSlots.get(slot) if payload.refSlots else None
            if rid and rid in id_to_no:
                slots[slot] = id_to_no[rid]
        # 按参考图顺序兜底：没显式指定槽位时，第一张当参考图
        if not slots and refs:
            slots = {"ref": 1}
        inks = ("color", "line")
        ink = payload.ink if payload.ink in inks else "color"
        nochars = ("yes", "no")
        nochar = payload.nochar if payload.nochar in nochars else "yes"
        cid = (f"{payload.layout}.expand.-.-" if mode == "expand"
               else f"{payload.layout}.{mode}.{ink}.{nochar}")
        sub = (payload.sub or "").strip()
        prompt = ext.compose_scene(
            mode, payload.layout, payload.text, refs=slots, ink=ink, nochar=nochar,
            style=payload.style, style_custom=payload.styleCustom,
            style_text_val=payload.styleText, line_style=payload.lineStyle, sub=sub,
        )
        brief = ext.brief(kind, payload.layout, payload.text, mode=mode, ink=ink, sub=sub)
    else:
        cid = f"{payload.layout}.{mode}.{payload.ink}.{payload.ref}"
        # 图生图会在最前面垫一张纯白占位图（见 services.submit_normal），
        # 用户那张参考图就排到第 2 位，提示词里的「图 N」要跟着对。
        ref_no = 2 if (mode == "i2i" and payload.ref == "lock" and refs) else 1
        prompt = ext.compose_prop(
            mode, payload.layout, payload.text, ref=payload.ref, ink=payload.ink,
            style=payload.style, style_custom=payload.styleCustom,
            style_text_val=payload.styleText, line_style=payload.lineStyle,
            allow_empty=(mode == "i2i" and bool(refs)),
            ref_no=ref_no,
        )
        brief = ext.brief(kind, payload.layout, payload.text, mode=mode, ink=payload.ink)

    info = ext.meta(kind, cid)
    grade_name = (payload.grade or ext.MASTER).strip() or ext.MASTER
    grade = ext.find_grade(kind, cid, grade_name)
    if not grade:
        names = "、".join(g["name"] for g in ext.grades(kind, cid))
        raise ext.ExtSheetError(f"「{info['name']}」没有档位「{grade_name}」（可选：{names}）")
    params = ext.effective_params(kind, cid, grade_name)
    return {
        "prompt": prompt, "wf": info["wf"], "grade": grade_name,
        "params": params, "tag": ext.tag(kind, cid, grade_name),
        "size": grade["size"], "brief": brief, "combo": info,
        "ext": {
            "kind": kind, "mode": mode, "layout": payload.layout, "sub": sub,
            "ink": payload.ink,
            "nochar": payload.nochar, "ref": payload.ref, "refSlots": payload.refSlots,
            "style": payload.style, "styleCustom": payload.styleCustom,
            "styleText": payload.styleText, "lineStyle": payload.lineStyle or "",
            "text": payload.text.strip(), "combo": cid, "comboName": info["name"],
            "grade": grade_name,
        },
    }


def _prepare_board(payload: BoardPayload, refs: list[str]) -> dict[str, Any]:
    """宫格分镜故事板：逐格描述 → (提示词, 组合, 档位, 参数, 工作流, 摘要)。"""
    from . import boardlib as board
    cid = f"{payload.layout}.{payload.ink if payload.ink in ('color', 'line') else 'color'}"
    info = board.meta(cid)
    cells = [str(x or "") for x in (payload.cells or [])][:int(info["cells"])]
    if not any(x.strip() for x in cells):
        raise BoardError("至少写一格的画面描述再发送")
    if len(refs) > 6:
        raise BoardError("宫格分镜故事板最多 6 张参考图")
    prompt = board.compose(payload.layout, cells, ink=payload.ink,
                           line_style=payload.lineStyle, refs=len(refs))
    grade_name = (payload.grade or board.MASTER).strip() or board.MASTER
    grade = board.find_grade(cid, grade_name)
    if not grade:
        names = "、".join(g["name"] for g in board.grades(cid))
        raise BoardError(f"「{info['name']}」没有档位「{grade_name}」（可选：{names}）")
    params = board.effective_params(cid, grade_name)
    if payload.ink == "line":
        # 线稿模式兜底：工作流默认 cfg=1，提示词没有引导力，夕阳/暖光格实测必然渗色
        # （G4 第4格、G6 第4格、G9 第8格都复现）。提高 CFG 让黑白线稿约束真正生效。
        # 两个故事板工作流的 CFGGuider 都是 459_507；只影响线稿组合，彩色档不受影响。
        params.setdefault("459_507", {})["cfg"] = 3.5
    return {
        "prompt": prompt, "wf": info["wf"], "grade": grade_name,
        "params": params, "tag": board.tag(cid, grade_name), "size": grade["size"],
        "brief": board.brief(payload.layout, cells), "combo": info,
        "sheet": {
            "kind": "board", "layout": payload.layout, "cells": cells,
            "ink": payload.ink, "lineStyle": payload.lineStyle or "",
            "combo": cid, "comboName": info["name"], "grade": grade_name,
        },
    }


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """任何没接住的异常也回一段 JSON。

    为什么：以前未捕获异常会被 Starlette 回成纯文本 "Internal Server Error"，
    前端 `resp.json()` 直接解析失败，只能提示「返回格式不对」——把真正原因藏起来了。
    """
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        {"ok": False, "reason": f"服务内部出错：{type(exc).__name__}: {exc}"},
        status_code=500,
    )


# ---------------------------------------------------------------- 页面
@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8")


# PWA：清单与 Service Worker 必须挂在根路径，安装后作用域才能覆盖整个应用
@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest() -> FileResponse:
    return FileResponse(WEB_DIR / "manifest.webmanifest",
                        media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})


@app.get("/sw.js", include_in_schema=False)
async def pwa_service_worker() -> FileResponse:
    return FileResponse(WEB_DIR / "sw.js",
                        media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# ---------------------------------------------------------------- 状态与 UI
@app.get("/api/state")
async def api_state() -> Response:
    # 任务攒到上千条时整包有三四 MB，而前端每隔几秒就要拉一次；
    # 这里缓存 1.2 秒内的同一份字节，多个页面同时轮询时不至于把 CPU 打满。
    now = time.time()
    if _STATE_CACHE["body"] is not None and now - _STATE_CACHE["at"] < 1.2:
        return Response(content=_STATE_CACHE["body"], media_type="application/json")
    payload = service.state_payload()
    payload["configError"] = state.last_error
    payload["release"] = {"disabled": release.disabled()}
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    _STATE_CACHE.update(at=now, body=body)
    return Response(content=body, media_type="application/json")


@app.post("/api/ui")
async def api_ui(body: UIPatch) -> dict[str, Any]:
    service.update_ui(body.patch)
    return ok()


# ---------------------------------------------------------------- 任务
def submit_task_body(body: TaskRequest, archive_folder: str | None = None) -> tuple[bool, str, str]:
    """把一次请求整理成任务：结构化输入 → 提示词 / 档位 / 工作流 / 参数。

    创作台 /api/tasks 与画布 /api/canvas/tasks 共用这一段，
    设定图、故事板、视频四模式、音频四功能的规则只有这一份，两边不会分叉。
    返回 (是否受理, job_id, 拒收原因)。
    """
    if release.is_disabled(body.capId):
        return False, "", f"「{release.name_of(body.capId)}」{release.reason(body.capId)}"
    prompt, grade, wf = body.prompt, body.grade, body.wf
    sheet_info: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    tag: str | None = None
    brief = ""
    video_info: dict[str, Any] | None = None
    audio_info: dict[str, Any] | None = None
    # 「基于上一条」要把用户原话和画法/线稿选项整条带回。服务端 job.prompt 存的是
    # compose 之后的成片提示词（含线稿前缀/尾缀），直接回填会二次拼装、把旧线稿句
    # 写死在开头（用户报的「4 个线稿选项切换无效」）。所以另存一份拼装前的原话和
    # 结构化 image，供 refine 精确复原。只对图片生成能力生效，别的能力是 None。
    raw_prompt: str | None = None
    image_info: dict[str, Any] | None = None
    video_ext_info: dict[str, Any] | None = None
    if body.videoExt is not None and body.capId == videoext.CAP_ID:
        func = videoext.find_func(body.videoExt.func)
        if not func:
            return False, "", "没有这个子功能"
        gs = videoext.grades(func["id"])
        if body.grade not in gs:
            grade = videoext.default_grade(func["id"])
        ratio = body.videoExt.ratio if body.videoExt.ratio in videoext.RATIOS else videoext.RATIOS[0]
        kinds = [r.get("kind", "image") for r in service.resolve_refs(body.refs)]
        images, videos, audios = kinds.count("image"), kinds.count("video"), kinds.count("audio")
        if images > int(func.get("maxImages", 6)):
            return False, "", f"最多 {func.get('maxImages', 6)} 张参考图"
        if videos > int(func.get("maxVideos", 1)):
            return False, "", f"最多 {func.get('maxVideos', 1)} 段参考视频"
        if audios > int(func.get("maxAudios", 3)):
            return False, "", f"最多 {func.get('maxAudios', 3)} 段参考音频"
        if not body.prompt.strip():
            return False, "", "写点描述再发送"
        mode = videoext.norm_mode(func["id"], body.videoExt.mode)
        video_ext_info = {"func": func["id"], "ratio": ratio, "mode": mode,
                          "duration": body.videoExt.duration}
        # 和图片生成同理：job.prompt 是 compose 后的成片提示词，
        # 「基于上一条」要用用户原话，否则那句「不要出现字幕…」会被重复拼上。
        raw_prompt = body.prompt
        prompt = videoext.compose(body.prompt)
        brief = (f"{func['name']} · {videoext.mode_name(func['id'], mode)} · {ratio} · "
                 f"{(body.prompt or '').strip()[:40]}")
        wf = videoext.wf_for(func["id"], grade)
    if body.image is not None and body.capId == "image.generate":
        raw_prompt = body.prompt
        image_info = body.image.model_dump()
        # 需求2：画法 / 风格从「图片生成通用模板」取（主体无关），和角色设定图库分开。
        prompt = compose_image_prompt(prompt, body.image)
    if body.video is not None and video_ext_info is None:
        # 视频的 prompt 会被 videolib.compose 拼上「首帧声明 / 不要字幕」等前后缀，
        # 「基于上一条」必须用原话回填，否则二次拼装、写法被写死在开头。
        raw_prompt = body.prompt
        mode = body.video.mode if body.video.mode in videolib.MODE_IDS else "t2v"
        info = videolib.MODE_IDS[mode]
        if not info.get("enabled"):
            return False, "", f"「{info['name']}」还没开放"
        if mode != "board" and not body.prompt.strip():
            return False, "", "写点描述再发送"
        # 参考素材按类型点数：提示词里的 <Picture N> 必须和实际槽位对得上
        kinds = [r.get("kind", "image") for r in service.resolve_refs(body.refs)]
        images, videos, audios = kinds.count("image"), kinds.count("video"), kinds.count("audio")
        if mode == "t2v" and (images or videos or audios):
            return False, "", "文生视频不需要上传素材，换「参考生视频」再用参考"
        if mode == "fl2v":
            if not images:
                return False, "", "首尾帧生视频至少要一张首帧图"
            if images > 2:
                return False, "", "首尾帧最多两张图：首帧 + 尾帧"
            if videos or audios:
                return False, "", "首尾帧生视频只收图片"
        if mode == "board":
            if not images:
                return False, "", "故事板生视频要先上传一张故事板"
            grid = body.video.grid if body.video.grid in (4, 6, 9) else 4
            board_cells = [c for c in body.video.cells if str(c).strip()]
            if len(board_cells) > grid:
                return False, "", f"选了 {grid} 宫格，格子描述不能超过 {grid} 条"
            video_info = {"mode": mode, "grid": grid, "cells": board_cells,
                          "ratio": body.video.ratio, "res": body.video.res,
                          "steps": body.video.steps, "length": body.video.length,
                          "duration": body.video.duration}
            prompt = videolib.compose(mode, body.prompt, images=images, videos=videos,
                                      audios=audios, board={"grid": grid, "cells": board_cells})
        else:
            video_info = {"mode": mode, "ratio": body.video.ratio, "res": body.video.res,
                          "steps": body.video.steps, "length": body.video.length,
                          "duration": body.video.duration}
            prompt = videolib.compose(mode, prompt, images=images, videos=videos, audios=audios)
        wf = info["wf"]
        brief = videolib.brief(mode, prompt, images, videos, audios,
                               board={"grid": video_info.get("grid", 4),
                                      "cells": video_info.get("cells", [])})
    if body.audio is not None:
        # 同视频：compose 会给「音乐」拼收尾句，回填要用用户原话。
        raw_prompt = body.prompt
        amode = body.audio.mode if body.audio.mode in audiolib.MODE_IDS else "music"
        info = audiolib.MODE_IDS[amode]
        if not info.get("enabled"):
            return False, "", f"「{info['name']}」还没开放"
        if not body.prompt.strip():
            return False, "", "写点描述再发送"
        kinds = [r.get("kind", "image") for r in service.resolve_refs(body.refs)]
        audios = kinds.count("audio")
        if info["needsAudio"] and not audios:
            return False, "", f"「{info['name']}」要先上传一段参考音频"
        if not info["needsAudio"] and audios:
            return False, "", "这个功能不需要参考音频，换「参考生成音乐」或「声音克隆」"
        audio_info = {"mode": amode, "lyrics": body.audio.lyrics,
                      "duration": body.audio.duration}
        prompt = audiolib.compose(amode, prompt)
        wf = info["wf"]
        brief = f"{info['name']} · {(body.prompt or '').strip()[:40]}"
    if body.sheet is not None and body.capId == SHEET_CAP:
        cap = config.capability(body.capId) or {}
        try:
            prepared = _prepare_sheet(body.sheet, cap, body.refs)
        except SheetError as exc:
            return False, "", (str(exc))
        prompt, grade, wf = prepared["prompt"], prepared["grade"], prepared["wf"]
        sheet_info, brief = prepared["sheet"], prepared["brief"]
        params, tag = prepared["params"], prepared["tag"]
    if body.ext is not None:
        cap = config.capability(body.capId) or {}
        try:
            prepared = _prepare_ext(body.ext, body.refs)
        except Exception as exc:  # noqa: BLE001 - ExtSheetError 与其它异常都给人话
            reason = str(exc) if exc.__class__.__name__ == "ExtSheetError" else f"设定图参数有误：{exc}"
            return False, "", (reason)
        prompt, grade, wf = prepared["prompt"], prepared["grade"], prepared["wf"]
        sheet_info, brief = prepared["ext"], prepared["brief"]
        params, tag = prepared["params"], prepared["tag"]
    if body.board is not None:
        if body.capId != "board.story":
            return False, "", "宫格分镜故事板的能力编号不对"
        try:
            prepared = _prepare_board(body.board, body.refs)
        except Exception as exc:  # noqa: BLE001 - 提示词库/参数错误都给人话
            reason = str(exc) if exc.__class__.__name__ == "BoardError" else f"分镜故事板参数有误：{exc}"
            return False, "", (reason)
        prompt, grade, wf = prepared["prompt"], prepared["grade"], prepared["wf"]
        sheet_info, brief = prepared["sheet"], prepared["brief"]
        params, tag = prepared["params"], prepared["tag"]
    accepted, job_id, reason = service.submit_normal(
        body.capId, prompt, grade, body.refs, wf=wf, sheet=sheet_info, brief=brief,
        params=params, tag=tag, video=video_info, audio=audio_info,
        image=image_info, raw_prompt=raw_prompt, archive_folder=archive_folder,
        video_ext=video_ext_info)
    return accepted, job_id, reason


@app.post("/api/tasks")
async def api_tasks(body: TaskRequest) -> dict[str, Any]:
    """提交一次生成（规则见 submit_task_body，与画布节点同一套）。"""
    accepted, job_id, reason = submit_task_body(body, body.archiveFolder)
    return ok({"job_id": job_id}) if accepted else blocked(reason)



@app.post("/api/tasks/{job_id}/queue")
async def api_queue(job_id: str, body: QueueRequest) -> dict[str, Any]:
    accepted, reason = service.submit_queue(job_id, body.grade)
    return ok() if accepted else blocked(reason)


@app.post("/api/tasks/{job_id}/retry")
async def api_retry(job_id: str) -> dict[str, Any]:
    accepted, _job_id, reason = service.repeat(job_id)
    return ok() if accepted else blocked(reason)


@app.post("/api/tasks/{job_id}/again")
async def api_again(job_id: str) -> dict[str, Any]:
    accepted, _job_id, reason = service.repeat(job_id)
    return ok() if accepted else blocked(reason)


@app.post("/api/tasks/{job_id}/refs")
async def api_task_refs(job_id: str) -> dict[str, Any]:
    """「基于上一条」把参考图重新带回来：按任务记录的本地副本重新登记一组 ref。"""
    accepted, reason, refs = service.restore_refs(job_id)
    return ok({"refs": refs}) if accepted else blocked(reason)


@app.post("/api/runs/{run_id}/cancel")
async def api_cancel(run_id: str) -> dict[str, Any]:
    return ok() if service.cancel(run_id) else blocked("找不到这条任务")


# ---------------------------------------------------------------- 上传与文件
async def _store_upload(file: UploadFile, want: str = "") -> dict[str, Any]:
    """保存参考素材到应用目录，并交给 ComfyUI 取得 input 文件名。

    want 是页面声明的素材类型（image / video / audio）：视频生成那里「首帧槽」
    和「参考视频槽」是两个上传位，只靠扩展名有时分不清用户想放进哪个槽。
    """
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXT | ALLOWED_VIDEO_EXT | ALLOWED_AUDIO_EXT:
        # 剪贴板/网页里拿到的图常常没有扩展名（浏览器就叫它 image），
        # 这时按 content-type 补一个；猜不出来时保留原后缀，别把真实的 .mp3/.mp4 盖掉
        guess = (file.content_type or "").split("/")[-1].lower().split(";")[0]
        suffix = {"jpeg": ".jpg", "jpg": ".jpg", "pjpeg": ".jpg", "png": ".png",
                  "webp": ".webp", "bmp": ".bmp", "gif": ".gif",
                  "tiff": ".tif", "x-tiff": ".tif", "mp4": ".mp4", "webm": ".webm",
                  "quicktime": ".mov", "x-matroska": ".mkv",
                  "mpeg": ".mp3", "wav": ".wav", "x-wav": ".wav", "flac": ".flac",
                  "ogg": ".ogg", "m4a": ".m4a", "aac": ".aac"}.get(guess, suffix)
    if want == "audio" and suffix not in ALLOWED_AUDIO_EXT:
        raise HTTPException(400, "这里只收音频（mp3 / wav / flac / ogg / m4a）")
    if want == "video" and suffix not in ALLOWED_VIDEO_EXT:
        raise HTTPException(400, "这里只收视频（mp4 / webm / mov / mkv）")
    if suffix in ALLOWED_VIDEO_EXT:
        kind, cap_mb = "video", MAX_VIDEO_MB
    elif suffix in ALLOWED_AUDIO_EXT:
        kind, cap_mb = "audio", config.service.uploadMaxMB
    elif suffix in ALLOWED_IMAGE_EXT:
        kind, cap_mb = "image", config.service.uploadMaxMB
    else:
        raise HTTPException(400, "只收图片、视频或音频文件")
    content = await file.read()
    if len(content) > cap_mb * 1024 * 1024:
        raise HTTPException(400, f"单个{ {'image': '图片', 'video': '视频', 'audio': '音频'}[kind] }不能超过 {cap_mb}MB")
    cap = config.capability(state.ui.get("capId", "image.generate")) or {}
    max_refs = int(cap.get("maxRefs", 3) or 3)
    # 上限按「已启用能力里最大的参考图数」算，而不是只看当前能力：
    # 临时图是全局共享的，改成只收 1 张参考图的设定图时，不该因为别处攒了十几张
    # 就突然一张都传不上来（原来 maxRefs=1 → 上限 10，切过去直接死锁）。
    try:
        widest = max([int(c.get("maxRefs", 3) or 3) for c in config.raw.get("capabilities", [])
                      if c.get("enabled") and c.get("workflows")] or [max_refs])
    except (TypeError, ValueError):
        widest = max_refs
    limit = max(max_refs, widest) * 10
    if len(state.refs.as_list()) >= limit:
        # 参考图不随「移除」立刻删文件（要留给「基于上一条」复用），所以超限时
        # 先清掉既不在草稿里、也没被任何仍在会话里的任务引用的旧临时图，再谈拒绝
        draft = (state.ui.get("drafts") or {}).get(cap.get("id", ""), {}) or {}
        service.prune_refs(draft.get("refs") or [], max(max_refs, limit // 3))
        left = len(state.refs.as_list())
        if left >= limit:
            raise HTTPException(400, f"临时图已存 {left} 张（上限 {limit} 张）：先在右栏删掉不用的"
                                     "会话任务，或减少参考图张数再传")
    # 需求 04 章：上传目录塞满要明确提示，不能让用户以为文件丢了
    try:
        free = shutil.disk_usage(str(config.resolve(config.service.uploadDir))).free
    except OSError:
        free = 0
    if free and free < max(64 * 1024 * 1024, len(content) * 3):
        raise HTTPException(400, "上传目录满了，先清理一下再传")
    ref_id = state.next_id("ref")
    filename = f"{ref_id}{suffix}"
    path = config.resolve(config.service.uploadDir) / filename
    path.write_bytes(content)
    comfy_name = service.comfy.upload_asset(path, filename)
    ref = {"id": ref_id, "kind": kind, "name": file.filename or filename,
           "filename": filename, "path": str(path), "comfyName": comfy_name}
    state.refs.add(ref_id, ref)
    return {"id": ref_id, "kind": kind, "name": ref["name"]}


def _ref_from_run(run_id: str, index: int = 0) -> dict[str, Any] | None:
    """把某次任务的成品登记成参考素材。

    画布上「上游节点的产出」不该再绕一圈浏览器上传：这里直接取任务文件，
    复用同一套上传目录和 ref 编号，于是 submit_normal 拿到的就是普通 refs，
    不需要给画布单开一条素材通道。同一文件重复引用只登记一次。
    """
    info = service.file_for_run(run_id, index)
    if not info:
        return None
    src = Path(info.get("path") or "")
    if not src.is_file():
        return None
    for ref in state.refs.as_list():
        if str(ref.get("path") or "") == str(src):
            return ref
    kind = str(info.get("kind") or "")
    suffix = src.suffix.lower()
    if kind not in ("image", "video", "audio"):
        kind = ("video" if suffix in ALLOWED_VIDEO_EXT else
                "audio" if suffix in ALLOWED_AUDIO_EXT else
                "image" if suffix in ALLOWED_IMAGE_EXT else "")
    if not kind:
        return None
    ref_id = state.next_id("ref")
    filename = f"{ref_id}{suffix}"
    dest = config.resolve(config.service.uploadDir) / filename
    try:
        shutil.copyfile(src, dest)
    except OSError:
        return None
    comfy_name = service.comfy.upload_asset(dest, filename)
    ref = {"id": ref_id, "kind": kind, "name": src.name,
           "filename": filename, "path": str(dest), "comfyName": comfy_name}
    state.refs.add(ref_id, ref)
    return ref


def _resolve_ext_slots(payload: ExtSheetPayload | None, refs: list[str]) -> ExtSheetPayload | None:
    """画布场景节点的四个上传位：前端只知道「第几个输入」，不知道素材登记后的 ref id。

    所以画布提交时把槽位写成 `#1` / `#2`（inputs 里的序号），在这里换成真正的 ref id
    再交给 _prepare_ext —— 和创作台上传后直接给 ref id 的语义完全一致。
    """
    if payload is None or not payload.refSlots:
        return payload
    slots: dict[str, str] = {}
    for slot, value in payload.refSlots.items():
        token = str(value or "")
        if token.startswith("#") and token[1:].isdigit():
            index = int(token[1:]) - 1
            if 0 <= index < len(refs):
                slots[slot] = refs[index]
            continue
        if token:
            slots[slot] = token
    data = payload.model_dump()
    data["refSlots"] = slots
    return ExtSheetPayload(**data)


@app.post("/api/uploads")
async def api_upload(file: UploadFile = File(...), kind: str = Query("")) -> dict[str, Any]:
    return ok(await _store_upload(file, kind))


@app.delete("/api/uploads/{ref_id}")
async def api_remove_upload(ref_id: str) -> dict[str, Any]:
    ref = state.refs.remove(ref_id)
    if ref:
        try:
            Path(ref["path"]).unlink(missing_ok=True)
        except OSError:
            pass
    return ok()


@app.get("/api/media")
async def api_media(
    request: Request, ref: str | None = None, run: str | None = None, i: int = 0,
) -> Response:
    """预览/缩略图：只允许取本次上传或本次任务产出的文件。

    视频和音频要能拖动进度条，就必须支持 Range 请求（206）：浏览器发
    `Range: bytes=...` 时只回那一段，否则 `<video>` 会把整个文件下完才能播。
    """
    if ref:
        item = state.refs.get(ref)
        path = Path(item["path"]) if item else None
    elif run:
        info = service.file_for_run(run, i)
        path = Path(info["path"]) if info else None
    else:
        path = None
    if not path or not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
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
    return FileResponse(path, headers={"Accept-Ranges": "bytes"})


@app.get("/api/download")
async def api_download(run: str, i: int = 0) -> Response:
    info = service.file_for_run(run, i)
    if not info:
        raise HTTPException(404, "文件不存在")
    return FileResponse(Path(info["path"]), filename=info["filename"])


@app.post("/api/download/batch")
async def api_batch_download(body: BatchRequest) -> Response:
    selections = [(str(item.get("run", "")), int(item.get("index", 0))) for item in body.items]
    content = service.build_zip(selections)
    return Response(content, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="ComfyBridge.zip"'})


@app.post("/api/open-folder")
async def api_open_folder(body: FolderRequest) -> dict[str, Any]:
    accepted, reason, info = service.open_folder(body.path, body.select)
    return ok(info) if accepted else blocked(reason)


@app.post("/api/pick-folder")
def api_pick_folder(body: FolderPickRequest) -> dict[str, Any]:
    """弹资源管理器的目录选择框，返回用户选中的文件夹路径。"""
    if os.name != "nt":
        return blocked("当前系统不支持目录选择框")
    env = dict(os.environ)
    env["CB_PICK_TITLE"] = body.title
    env["CB_PICK_INITIAL"] = body.initial or ""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", _PICK_FOLDER_PS],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=900,
        )
    except Exception as exc:  # noqa: BLE001
        return blocked(f"打开目录选择框失败：{exc}")
    picked = (proc.stdout or "").strip()
    if not picked:
        return blocked("已取消选择")
    return ok(picked)


@app.post("/api/history/delete")
async def api_history_delete(body: DeleteItems) -> dict[str, Any]:
    """历史页删除：选中成品连同本地文件一起删（只在历史页能删文件）。"""
    accepted, reason, info = service.delete_history(body.items, body.deleteFiles)
    return ok(info) if accepted else blocked(reason)


@app.post("/api/session/tasks/{job_id}/delete")
async def api_session_delete(job_id: str) -> dict[str, Any]:
    """会话任务删除：移走右栏与主页面的对话记录，成品文件保留。"""
    accepted, reason = service.delete_session_job(job_id)
    return ok() if accepted else blocked(reason)


class SessionTasksDelete(BaseModel):
    ids: list[str] = Field(default_factory=list)


@app.post("/api/session/tasks/delete")
async def api_session_delete_bulk(body: SessionTasksDelete) -> dict[str, Any]:
    """会话任务批量删除：逐条移走对话记录（正在跑的会被跳过），成品文件保留。"""
    if not body.ids:
        return blocked("先勾选要删除的对话")
    accepted, reason, count = service.delete_session_jobs(body.ids)
    return ok({"count": count, "reason": reason}) if accepted else blocked(reason)


# ---------------------------------------------------------------- 管理台
@app.get("/api/diag")
async def api_diag() -> dict[str, Any]:
    return ok(service.diag())


@app.post("/api/config")
async def api_save_config(body: ConfigPatch) -> dict[str, Any]:
    accepted, reason = service.save_config(body.patch)
    return ok() if accepted else blocked(reason)


@app.post("/api/config/reload")
async def api_reload_config() -> dict[str, Any]:
    accepted, reason = service.reload_config()
    return ok() if accepted else blocked(reason)


@app.get("/api/storage")
async def api_storage() -> dict[str, Any]:
    """管理台「当前占用」：上传/任务记录/档位拷贝/成果目录各占多少。"""
    return ok(service.storage_info())


@app.get("/api/config/raw")
async def api_config_raw() -> dict[str, Any]:
    """配置原文（配置损坏时页面上直接改）。"""
    return ok(service.config_raw())


@app.put("/api/config/raw")
async def api_config_raw_save(body: RawText) -> dict[str, Any]:
    """保存页面里改过的配置原文，成功后重读配置。"""
    accepted, reason = service.save_config_raw(body.text)
    return ok() if accepted else blocked(reason)


@app.post("/api/capabilities/{action}/{cap_id}")
async def api_capability_action(action: str, cap_id: str) -> dict[str, Any]:
    accepted, reason = service.capability_action(action, cap_id)
    return ok() if accepted else blocked(reason)


@app.get("/api/workflows/{name}/nodes")
async def api_workflow_nodes(name: str) -> dict[str, Any]:
    from .workflows import registry
    return ok({"name": name, "nodes": service_nodes(name), "schema": registry.schema})


def service_nodes(name: str) -> list[dict[str, Any]]:
    """节点清单 + 每格参数的控件形态（类型来自工作流自身）。"""
    from .workflows import registry
    nodes = registry.node_list(name)
    for node in nodes:
        meta = []
        for field in node["w"]:
            info = registry.field_meta(node["cls"], field)
            meta.append({"field": field, **info})
        node["fields"] = meta
    return nodes


@app.post("/api/workflows/{name}")
async def api_workflow_update(name: str, body: WorkflowPatch) -> dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    accepted, reason = service.update_workflow(name, patch)
    return ok() if accepted else blocked(reason)


@app.post("/api/workflows/{name}/grades")
async def api_grade_add(name: str, body: GradeRequest) -> dict[str, Any]:
    accepted, reason = service.add_grade(name, body.name, body.params, body.note)
    return ok() if accepted else blocked(reason)


@app.put("/api/workflows/{name}/grades")
async def api_grade_edit(name: str, body: GradeRequest) -> dict[str, Any]:
    patch = {"gradePatch": {"name": body.name, "note": body.note, "params": body.params}}
    accepted, reason = service.update_workflow(name, patch)
    return ok() if accepted else blocked(reason)


@app.delete("/api/workflows/{name}/grades/{grade_name}")
async def api_grade_delete(name: str, grade_name: str) -> dict[str, Any]:
    accepted, reason = service.delete_grade(name, grade_name)
    return ok() if accepted else blocked(reason)


@app.post("/api/workflows/{name}/grades/reorder")
async def api_grade_reorder(name: str, body: GradeOrderRequest) -> dict[str, Any]:
    """拖拽重排档位：按给定顺序排非默认档，默认档固定在首位。"""
    accepted, reason = service.reorder_grades(name, body.order)
    return ok() if accepted else blocked(reason)


@app.get("/api/workflows/{name}/grades/{grade}/effective")
async def api_grade_effective(name: str, grade: str) -> dict[str, Any]:
    """这一档的参数是否真的会提交：逐项 want/got + 出图尺寸说明。"""
    from .workflows import WorkflowError, registry
    try:
        return ok(registry.grade_effective(name, grade))
    except WorkflowError as exc:
        return blocked(str(exc))


# ---------------------------------------------------------------- 角色设定图提示词库
@app.get("/api/image-style/library")
async def api_image_style_library() -> dict[str, Any]:
    """图片生成通用模板：线稿风格句 + 风格预设（主体无关）。"""
    try:
        return ok(imagestylelib.library.view())
    except imagestylelib.ImageStyleError as exc:
        return blocked(str(exc))


@app.put("/api/image-style/library")
async def api_image_style_library_save(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return ok(imagestylelib.library.save(body))
    except imagestylelib.ImageStyleError as exc:
        return blocked(str(exc))


@app.post("/api/image/preview")
async def api_image_preview(body: ImagePreviewPayload) -> dict[str, Any]:
    """图片生成「看完整提示词」：和真正提交走同一条服务端组装函数。"""
    return ok({"prompt": compose_image_prompt(body.prompt, body.image)})


@app.get("/api/sheet/library")
async def api_sheet_library() -> dict[str, Any]:
    """提示词库快照：通用块、版式、风格预设、15 条已实测组合。"""
    try:
        return ok(sheet_library.view())
    except SheetError as exc:
        return blocked(str(exc))


@app.put("/api/sheet/library")
async def api_sheet_library_save(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """管理台保存通用块 / 风格预设；先校验再写回，写坏了不让过。"""
    try:
        sheet_library.save(body)
        return ok(sheet_library.view())
    except SheetError as exc:
        return blocked(str(exc))


@app.post("/api/sheet/preview")
async def api_sheet_preview(body: SheetPayload) -> dict[str, Any]:
    """创作台「看完整提示词」：不提交任务，只回组装结果与这一档的出图尺寸。"""
    from .workflows import registry
    cap = config.capability(SHEET_CAP) or {}
    # 预览阶段还没必要逼用户先传图：按「有没有选参考方式」假定图已就位
    refs = ["preview"] if body.ref in ("lock", "style") else []
    try:
        prepared = _prepare_sheet(body, cap, refs)
    except SheetError as exc:
        return blocked(str(exc))
    try:
        # 尺寸以「真正要提交的那份工作流」为准，和生成时用的是同一套参数
        effective = registry.grade_effective(
            prepared["wf"], prepared["grade"],
            params=prepared["params"], tag=prepared["tag"],
        )
        size = effective["size"]["label"]
        all_ok = effective["allOk"]
    except Exception:  # noqa: BLE001 - 尺寸只是提示，算不出来不影响预览
        size, all_ok = prepared["size"], False
    return ok({
        "prompt": prepared["prompt"], "brief": prepared["brief"],
        "grade": prepared["grade"], "wf": prepared["wf"],
        "combo": prepared["combo"], "comboId": prepared["combo"]["id"],
        "sheet": prepared["sheet"], "size": size, "allOk": all_ok,
        "params": prepared["params"],
    })


@app.get("/api/sheet/combos")
async def api_sheet_combos() -> dict[str, Any]:
    """管理台「档位表」：18 个组合 + 每个组合的档位（母版 + 自建），带参数生效自检。"""
    from . import combos
    from .workflows import registry
    out: list[dict[str, Any]] = []
    for info in combos.all_combos():
        item = combos.view_one(info["id"])
        rows: list[dict[str, Any]] = []
        for grade in item["grades"]:
            try:
                eff = registry.grade_effective(
                    info["wf"], grade["name"],
                    params=combos.effective_params(info["id"], grade["name"]),
                    tag=combos.tag(info["id"], grade["name"]),
                )
                rows.append({**grade, "allOk": eff["allOk"], "size": eff["size"]["label"],
                             "paramsRows": eff["rows"]})
            except Exception as exc:  # noqa: BLE001 - 单档算不出来不能拖垮整表
                rows.append({**grade, "allOk": False, "error": str(exc)})
        out.append({**item, "grades": rows})
    return ok({"combos": out, "master": combos.MASTER,
               "inks": list(combos.INKS), "refs": list(combos.REFS)})


@app.put("/api/sheet/combos/{cid}/grades")
async def api_sheet_combo_grades(cid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """保存某个组合的自建档位（整体替换）；母版不落盘，也不许被覆盖。"""
    from . import combos
    from .workflows import registry
    try:
        combos.save_grades(cid, list(body.get("grades") or []))
    except SheetError as exc:
        return blocked(str(exc))
    registry.invalidate()
    registry.rebuild_all_copies()
    return ok(combos.view_one(cid))


# ---------------------------------------------------------------- 1.03 扩展设定图（场景 / 道具）
def _ext_kind(cap_id: str) -> str:
    from . import extsheets as ext
    if cap_id == "sheet.scene":
        return ext.SCENE
    if cap_id == "sheet.prop":
        return ext.PROP
    raise HTTPException(400, "不是场景/道具设定图能力")


@app.post("/api/ext/preview")
async def api_ext_preview(body: ExtSheetPayload) -> dict[str, Any]:
    """创作台「看完整提示词」：与提交走同一个 compose。"""
    from .workflows import registry
    from . import extsheets as ext
    try:
        # 预览阶段优先用前端已上传的 ref id 还原图序号；没有才用占位图
        if body.mode == "t2i":
            refs = []
        else:
            refs = body.refs or (["preview"] * (4 if body.mode == "i2i" else 1))
        prepared = _prepare_ext(body, refs)
    except Exception as exc:  # noqa: BLE001
        reason = str(exc) if exc.__class__.__name__ == "ExtSheetError" else f"设定图参数有误：{exc}"
        return blocked(reason)
    try:
        effective = registry.grade_effective(
            prepared["wf"], prepared["grade"], params=prepared["params"], tag=prepared["tag"])
        size, all_ok = effective["size"]["label"], effective["allOk"]
    except Exception:  # noqa: BLE001
        size, all_ok = prepared["size"], False
    return ok({
        "prompt": prepared["prompt"], "brief": prepared["brief"],
        "grade": prepared["grade"], "wf": prepared["wf"],
        "combo": prepared["combo"], "comboId": prepared["combo"]["id"],
        "ext": prepared["ext"], "size": size, "allOk": all_ok,
        "params": prepared["params"],
    })


@app.get("/api/ext/{cap_id}/library")
async def api_ext_library(cap_id: str) -> dict[str, Any]:
    from . import extsheets as ext
    try:
        return ok(ext.library_view(_ext_kind(cap_id)))
    except ext.ExtSheetError as exc:
        return blocked(str(exc))


@app.put("/api/ext/{cap_id}/library")
async def api_ext_library_save(cap_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from . import extsheets as ext
    try:
        return ok(ext.update_template(_ext_kind(cap_id), body))
    except ext.ExtSheetError as exc:
        return blocked(str(exc))


@app.get("/api/ext/{cap_id}/combos")
async def api_ext_combos(cap_id: str) -> dict[str, Any]:
    """管理台档位表：组合清单 + 档位 + 参数生效自检。"""
    from . import extsheets as ext
    from .workflows import registry
    kind = _ext_kind(cap_id)
    out: list[dict[str, Any]] = []
    for info in ext.all_combos(kind):
        rows: list[dict[str, Any]] = []
        for g in ext.grades(kind, info["id"]):
            try:
                eff = registry.grade_effective(
                    info["wf"], g["name"],
                    params=ext.effective_params(kind, info["id"], g["name"]),
                    tag=ext.tag(kind, info["id"], g["name"]))
                rows.append({**g, "allOk": eff["allOk"], "size": eff["size"]["label"],
                             "paramsRows": eff["rows"]})
            except Exception as exc:  # noqa: BLE001
                rows.append({**g, "allOk": False, "error": str(exc)})
        out.append({**info, "grades": rows,
                    "fields": [{"node": str(f["node"]), "field": str(f["field"])}
                               for f in ext._fields(info["wf"])]})
    return ok({"combos": out, "master": ext.MASTER, "kind": kind})


@app.put("/api/ext/{cap_id}/combos/{cid}/grades")
async def api_ext_combo_grades(cap_id: str, cid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from . import extsheets as ext
    from .workflows import registry
    kind = _ext_kind(cap_id)
    try:
        ext.save_grades(kind, cid, list(body.get("grades") or []))
    except ext.ExtSheetError as exc:
        return blocked(str(exc))
    registry.invalidate()
    registry.rebuild_all_copies()
    return ok({"id": cid, "grades": ext.grades(kind, cid)})


# ---------------------------------------------------------------- 对外接口（斜线技能用）
@app.get("/api/v1/health")
async def v1_health() -> dict[str, Any]:
    service.refresh_health()
    return {"comfy_online": state.comfy_online, "queue": state.comfy_queue}


@app.get("/api/v1/capabilities")
async def v1_capabilities() -> dict[str, Any]:
    return service.ai_capabilities()


@app.post("/api/v1/orders")
async def v1_order(body: AIOrder) -> dict[str, Any]:
    cap = body.capability or "image.generate"
    if release.is_disabled(cap):
        return {"ok": False, "reason": f"「{release.name_of(cap)}」{release.reason(cap)}"}
    return service.ai_order(body.model_dump())


@app.get("/api/v1/orders/{job_id}")
async def v1_status(job_id: str) -> dict[str, Any]:
    return service.ai_status(job_id)


@app.post("/api/v1/uploads")
async def v1_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    return await _store_upload(file)


@app.post("/api/v1/precheck")
async def v1_precheck(body: AIOrder) -> dict[str, Any]:
    ok_norm, reason = service.check_normal(body.capability or "image.generate")
    return {"ready": ok_norm, "reason": reason}


@app.post("/api/v1/orders/{job_id}/cancel")
async def v1_cancel(job_id: str) -> dict[str, Any]:
    job = state.job(job_id)
    if not job:
        return {"canceled": False, "reason": "没有这个任务编号"}
    for run in job["runs"]:
        if run["state"] in ("queued", "running"):
            service.cancel(run["id"])
    return {"canceled": True}


@app.get("/api/v1/tasks")
async def v1_tasks() -> dict[str, Any]:
    with state.lock:
        return {"tasks": [
            {"job_id": j["id"], "capability": j["cap"], "state": j["runs"][-1]["state"] if j["runs"] else "none",
             "progress": j["runs"][-1].get("pct", 0) if j["runs"] else 0}
            for j in state.jobs
        ]}


@app.get("/api/v1/files/{run_id}/{index}")
async def v1_file(run_id: str, index: int) -> Response:
    info = service.file_for_run(run_id, index)
    if not info:
        raise HTTPException(404, "文件不存在")
    return FileResponse(Path(info["path"]), filename=info["filename"])


@app.get("/api/v1/skill")
async def v1_skill() -> PlainTextResponse:
    path = ROOT / "skills" / "comfybridge-image-generate.md"
    if not path.exists():
        return PlainTextResponse("", status_code=404)
    return PlainTextResponse(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1.03 宫格分镜故事板
@app.post("/api/board/preview")
async def api_board_preview(body: BoardPayload) -> dict[str, Any]:
    """创作台「看完整提示词」：与提交走同一个 compose。"""
    from .workflows import registry
    try:
        prepared = _prepare_board(body, body.refs)
    except Exception as exc:  # noqa: BLE001
        reason = str(exc) if exc.__class__.__name__ == "BoardError" else f"分镜故事板参数有误：{exc}"
        return blocked(reason)
    try:
        effective = registry.grade_effective(
            prepared["wf"], prepared["grade"], params=prepared["params"], tag=prepared["tag"])
        size, all_ok = effective["size"]["label"], effective["allOk"]
    except Exception:  # noqa: BLE001
        size, all_ok = prepared["size"], False
    return ok({
        "prompt": prepared["prompt"], "brief": prepared["brief"],
        "grade": prepared["grade"], "wf": prepared["wf"],
        "combo": prepared["combo"], "comboId": prepared["combo"]["id"],
        "sheet": prepared["sheet"], "size": size, "allOk": all_ok,
        "params": prepared["params"],
    })


@app.get("/api/board/library")
async def api_board_library() -> dict[str, Any]:
    from . import boardlib as board
    try:
        return ok(board.library_view())
    except BoardError as exc:
        return blocked(str(exc))


@app.put("/api/board/library")
async def api_board_library_save(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from . import boardlib as board
    try:
        from .config import config as _config
        # 只改提示词数据文件，不碰配置与其它库
        board._LIB.save(body)
        return ok(board.library_view())
    except Exception as exc:  # noqa: BLE001
        return blocked(str(exc))


@app.get("/api/board/combos")
async def api_board_combos() -> dict[str, Any]:
    from . import boardlib as board
    from .workflows import registry
    out: list[dict[str, Any]] = []
    for info in board.all_combos():
        item = board.view_one(info["id"])
        rows: list[dict[str, Any]] = []
        for g in item["grades"]:
            try:
                eff = registry.grade_effective(
                    info["wf"], g["name"],
                    params=board.effective_params(info["id"], g["name"]),
                    tag=board.tag(info["id"], g["name"]),
                )
                rows.append({**g, "allOk": eff["allOk"], "size": eff["size"]["label"],
                             "paramsRows": eff["rows"]})
            except Exception as exc:  # noqa: BLE001
                rows.append({**g, "allOk": False, "error": str(exc)})
        out.append({**item, "grades": rows})
    return ok({"combos": out, "master": board.MASTER})


@app.put("/api/board/combos/{cid}/grades")
async def api_board_combo_grades(cid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from . import boardlib as board
    from .workflows import registry
    try:
        board.save_grades(cid, list(body.get("grades") or []))
    except BoardError as exc:
        return blocked(str(exc))
    registry.invalidate()
    registry.rebuild_all_copies()
    return ok(board.view_one(cid))


# ---------------------------------------------------------------- 视频生成画布
class OutRef(BaseModel):
    """画布上的上游生成节点：直接用它的成品当参考素材。"""
    run: str = ""
    i: int = 0


class CanvasInput(BaseModel):
    """按连线顺序排好的一个输入位。

    资产节点给 ref（上传素材 id），上游生成节点给 run（成品）。
    顺序就是提示词里 [图 N] / [视频 N] 的编号，所以必须按连线顺序传，
    不能像 refs + outs 那样分成两段拼接 —— 中间夹一个资产节点就会错位。
    """
    ref: str = ""
    run: str = ""
    i: int = 0


class CanvasTaskRequest(BaseModel):
    """画布上的一张节点卡提交一次生成。

    一个节点 = 功能页面里的一个子功能，所以这里和 TaskRequest 收同一套结构化输入：
    图片生成（image）、角色设定图（sheet）、场景/道具（ext）、故事板（board）、
    视频四模式（video）、音频四功能（audio）。整理成任务交给 submit_task_body，
    画布与创作台的规则只有一份。
    """
    capId: str = "image.generate"
    prompt: str = ""
    grade: str = "默认"
    refs: list[str] = Field(default_factory=list)
    outs: list[OutRef] = Field(default_factory=list)   # 旧字段：上游生成节点的成品（run 编号）
    inputs: list[CanvasInput] = Field(default_factory=list)  # 新：按连线顺序，ref / run 混排
    wf: str | None = None
    projectId: str = ""
    nodeId: str = ""
    image: ImageStylePayload | None = None   # 图片生成节点：画法 / 风格
    video: VideoPayload | None = None        # 视频节点：模式 / 比例 / 分辨率 / 步数 / 时长 / 宫格
    audio: AudioPayload | None = None        # 音频节点：模式 / 歌词 / 时长
    sheet: SheetPayload | None = None        # 角色设定图节点
    ext: ExtSheetPayload | None = None       # 场景 / 道具设定图节点
    board: BoardPayload | None = None        # 宫格分镜故事板节点


@app.get("/api/canvas")
async def api_canvas_state() -> dict[str, Any]:
    """画布开机数据：能用哪些能力、有哪些已有项目、管理台排除了谁。"""
    return ok(canvas.state_payload())


@app.post("/api/canvas/project")
async def api_canvas_new(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return ok(canvas.new_project(str(body.get("name") or "未命名画布")))


@app.get("/api/canvas/project/{pid}")
async def api_canvas_get(pid: str) -> dict[str, Any]:
    data = canvas.get_project(pid)
    return ok(data) if data else blocked("这个画布不存在")


@app.put("/api/canvas/project/{pid}")
async def api_canvas_save(pid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    accepted, reason = canvas.save_project(pid, body)
    return ok() if accepted else blocked(reason)


@app.post("/api/canvas/project/{pid}/rename")
async def api_canvas_rename(pid: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    accepted, reason = canvas.rename_project(pid, str(body.get("name") or ""))
    return ok() if accepted else blocked(reason)


@app.delete("/api/canvas/project/{pid}")
async def api_canvas_delete(pid: str) -> dict[str, Any]:
    accepted, reason = canvas.delete_project(pid)
    return ok() if accepted else blocked(reason)


@app.put("/api/canvas/config")
async def api_canvas_config(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    accepted, reason = canvas.save_config(body)
    return ok(canvas.load_config()) if accepted else blocked(reason)


@app.post("/api/canvas/tasks")
async def api_canvas_task(body: CanvasTaskRequest) -> dict[str, Any]:
    """画布节点的一次生成。

    节点 = 功能页面里的一个子功能，所以这里只做两件事：
    ① 把连线接进来的素材（资产 ref / 上游生成节点的成品 run）按顺序整理成 refs；
    ② 其余整段交给 submit_task_body —— 和创作台 /api/tasks 走的是同一份规则
    （设定图组合、故事板分格、视频四模式、音频四功能的校验与拼词都在里面）。
    唯一多出来的只有「归档到画布目录」和右栏标一个 canvas。
    """
    refs: list[str] = []
    if body.inputs:
        # 按连线顺序一次给齐：ref = 上传素材，run = 上游生成节点的成品
        for it in body.inputs:
            if it.ref:
                refs.append(it.ref)
            elif it.run:
                made = _ref_from_run(it.run, it.i)
                if made:
                    refs.append(made["id"])
    else:
        refs = list(body.refs)
        # 上游生成节点的成品：不经过浏览器，直接在本机登记成参考素材
        for o in body.outs:
            made = _ref_from_run(o.run, o.i)
            if made:
                refs.append(made["id"])
    merged = TaskRequest(
        capId=body.capId, prompt=body.prompt, grade=body.grade, refs=refs, wf=body.wf,
        image=body.image, video=body.video, audio=body.audio,
        sheet=body.sheet, ext=_resolve_ext_slots(body.ext, refs), board=body.board,
        archiveFolder=canvas.ARCHIVE_FOLDER,
    )
    accepted, job_id, reason = submit_task_body(merged, canvas.ARCHIVE_FOLDER)
    if not accepted:
        return blocked(reason)
    job = state.job(job_id)
    if job:
        # 右栏任务栏据此标一行「画布」，不必再给 job 拆一套结构
        job["src"] = "canvas"
        if body.projectId:
            job["canvas"] = {"project": body.projectId, "node": body.nodeId}
        state.persist()
    return ok({"job_id": job_id})


@app.get("/api/canvas/catalog")
async def api_canvas_catalog() -> dict[str, Any]:
    """管理台「视频生成画布」页：ComfyTV 全部功能 + 已绑定情况 + 可选工作流文件。"""
    return ok(canvas.catalog_payload())


@app.post("/api/canvas/stage")
async def api_canvas_stage(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """把一个 ComfyTV 功能挂上 / 摘下画布。

    挂上时会同时在工作流表里建一条记录（名字=功能名），
    于是「档位表 → 视频生成画布 → 该功能」就能直接配档位。
    """
    action = str(body.get("action") or "enable")
    stage_id = str(body.get("stageId") or "")
    if action == "disable":
        accepted, reason = canvas.disable_stage(stage_id)
        if not accepted:
            return blocked(reason)
        return ok(canvas.catalog_payload())

    accepted, reason, name = canvas.enable_stage(stage_id, str(body.get("wfFile") or ""))
    if not accepted:
        return blocked(reason)
    payload = canvas.catalog_payload()
    payload["wfName"] = name
    return ok(payload)


# ---------------------------------------------------------------- 视频 / 音频档位
class GradeRows(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/api/video/grades/{mode}")
async def api_video_grades(mode: str) -> dict[str, Any]:
    """视频某一模式的档位表（比例 / 分辨率 / 步数），带算出来的实际尺寸与 LoRA。"""
    if mode not in videolib.MODE_IDS:
        return blocked("没有这个视频模式")
    rows = []
    for g in videolib.grades(mode):
        try:
            rows.append(videolib.grade_row(mode, g["name"]) or g)
        except Exception:  # noqa: BLE001 - 工作流还没登记时先给原始档位
            rows.append(g)
    return ok({"mode": mode, "rows": rows,
               "ratios": list(videolib.RATIOS), "reses": list(videolib.RESES),
               "steps": list(videolib.STEPS)})


@app.put("/api/video/grades/{mode}")
async def api_video_grades_save(mode: str, body: GradeRows) -> dict[str, Any]:
    try:
        videolib.save_grades(mode, body.rows)
    except ValueError as exc:
        return blocked(str(exc))
    return ok({"rows": videolib.grades(mode)})


@app.get("/api/audio/grades/{mode}")
async def api_audio_grades(mode: str) -> dict[str, Any]:
    if mode not in audiolib.MODE_IDS:
        return blocked("没有这个音频功能")
    return ok({"mode": mode, "rows": audiolib.grades(mode),
               "durations": list(audiolib.DURATIONS),
               "voiceFields": audiolib.VOICE_FIELDS})


@app.put("/api/audio/grades/{mode}")
async def api_audio_grades_save(mode: str, body: GradeRows) -> dict[str, Any]:
    try:
        audiolib.save_grades(mode, body.rows)
    except ValueError as exc:
        return blocked(str(exc))
    return ok({"rows": audiolib.grades(mode)})


@app.get("/api/video/modes")
async def api_video_modes() -> dict[str, Any]:
    return ok(videolib.state_payload())


@app.get("/api/audio/modes")
async def api_audio_modes() -> dict[str, Any]:
    return ok(audiolib.state_payload())


@app.get("/api/canvas/funcs")
async def api_canvas_funcs() -> dict[str, Any]:
    """画布两个生成子功能 + 可绑定的工作流清单（管理台下拉用）。"""
    rows = canvas.funcs()
    wf_names = list((config.raw.get("workflows") or {}).keys())
    return ok({"funcs": rows, "workflows": wf_names})


@app.put("/api/canvas/funcs")
async def api_canvas_funcs_save(body: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """管理台改画布子功能：任意节点键的 换绑工作流 / 改名 / 启停开关。"""
    patch: dict[str, Any] = {k: (v or {}) for k, v in (body or {}).items() if isinstance(v, dict)}
    if not patch:
        return blocked("没有要改的东西")
    try:
        canvas.save_funcs(patch)
    except ValueError as exc:
        return blocked(str(exc))
    return ok({"funcs": canvas.funcs()})
