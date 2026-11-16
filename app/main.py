"""ComfyBridge FastAPI 入口：一个进程、一个端口，页面与接口都由它发。"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, ConfigError, config
from .promptlib import SheetError, library as sheet_library
from .services import service
from .state import state

WEB_DIR = ROOT / "web"
logger = logging.getLogger("comfybridge.api")
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
SHEET_CAP = "sheet.character"


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


class TaskRequest(BaseModel):
    capId: str = "image.generate"
    prompt: str = ""
    grade: str = "默认"
    refs: list[str] = Field(default_factory=list)
    wf: str | None = None  # 前端显式选择的子工作流（文生图/参考生图），None 时按参考图数量自动挑
    sheet: SheetPayload | None = None  # 角色设定图专用：结构化输入，服务端组装提示词


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
    try:
        config.load()
        service.start()
        state.last_error = ""
    except Exception as exc:  # noqa: BLE001
        state.last_error = str(exc)
        print(f"[ComfyBridge] 配置错误：{exc}")
    yield
    try:
        service.comfy.stop()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="ComfyBridge", version="1.01", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


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
    wf_name, _why = pick_workflow(cap, len(refs))
    if not wf_name:
        raise SheetError("这个能力还没配置工作流")
    if wf_name != info["wf"]:
        # 组合推出的工作流和 config 的规则对不上：宁可报错，也别拿参考生图跑文生图组合
        raise SheetError(
            f"组合「{info['name']}」要用「{info['wf']}」，但当前规则挑到「{wf_name}」，"
            "请检查能力里的工作流规则"
        )
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


# ---------------------------------------------------------------- 状态与 UI
@app.get("/api/state")
async def api_state() -> dict[str, Any]:
    payload = service.state_payload()
    payload["configError"] = state.last_error
    return payload


@app.post("/api/ui")
async def api_ui(body: UIPatch) -> dict[str, Any]:
    service.update_ui(body.patch)
    return ok()


# ---------------------------------------------------------------- 任务
@app.post("/api/tasks")
async def api_tasks(body: TaskRequest) -> dict[str, Any]:
    """提交一次生成。

    角色设定图走结构化输入（sheet）：服务端按「组合」组装整条提示词并推导参数，
    档位只提供参数覆盖；其它能力仍旧直接收 prompt 文本，行为不变。
    """
    prompt, grade, wf = body.prompt, body.grade, body.wf
    sheet_info: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    tag: str | None = None
    brief = ""
    if body.sheet is not None and body.capId == SHEET_CAP:
        cap = config.capability(body.capId) or {}
        try:
            prepared = _prepare_sheet(body.sheet, cap, body.refs)
        except SheetError as exc:
            return blocked(str(exc))
        prompt, grade, wf = prepared["prompt"], prepared["grade"], prepared["wf"]
        sheet_info, brief = prepared["sheet"], prepared["brief"]
        params, tag = prepared["params"], prepared["tag"]
    accepted, job_id, reason = service.submit_normal(
        body.capId, prompt, grade, body.refs, wf=wf, sheet=sheet_info, brief=brief,
        params=params, tag=tag)
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
async def _store_upload(file: UploadFile) -> dict[str, Any]:
    """保存参考图到应用目录，并交给 ComfyUI 取得 input 文件名。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXT:
        # 剪贴板/网页里拿到的图常常没有扩展名（浏览器就叫它 image），
        # 这时按 content-type 补一个，别把人挡在「只收常见图片格式」外面
        guess = (file.content_type or "").split("/")[-1].lower().split(";")[0]
        suffix = {"jpeg": ".jpg", "jpg": ".jpg", "pjpeg": ".jpg", "png": ".png",
                  "webp": ".webp", "bmp": ".bmp", "gif": ".gif",
                  "tiff": ".tif", "x-tiff": ".tif"}.get(guess, "")
    if suffix not in ALLOWED_IMAGE_EXT:
        raise HTTPException(400, "只收常见图片格式（png / jpg / webp / gif / bmp）")
    content = await file.read()
    if len(content) > config.service.uploadMaxMB * 1024 * 1024:
        raise HTTPException(400, f"单文件不能超过 {config.service.uploadMaxMB}MB")
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
    comfy_name = service.comfy.upload_image(path, filename)
    ref = {"id": ref_id, "kind": "image", "name": file.filename or filename,
           "filename": filename, "path": str(path), "comfyName": comfy_name}
    state.refs.add(ref_id, ref)
    return {"id": ref_id, "kind": "image", "name": ref["name"]}


@app.post("/api/uploads")
async def api_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    return ok(await _store_upload(file))


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
    ref: str | None = None, run: str | None = None, i: int = 0,
) -> Response:
    """预览/缩略图：只允许取本次上传或本次任务产出的文件。"""
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
    return FileResponse(path)


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
