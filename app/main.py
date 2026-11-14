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
from .services import service
from .state import state

WEB_DIR = ROOT / "web"
logger = logging.getLogger("comfybridge.api")
ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


class TaskRequest(BaseModel):
    capId: str = "image.generate"
    prompt: str = ""
    grade: str = "默认"
    refs: list[str] = Field(default_factory=list)
    wf: str | None = None  # 前端显式选择的子工作流（文生图/参考生图），None 时按参考图数量自动挑


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
    accepted, job_id, reason = service.submit_normal(
        body.capId, body.prompt, body.grade, body.refs, wf=body.wf)
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
    limit = max_refs * 10
    if len(state.refs.as_list()) >= limit:
        # 参考图不随「移除」立刻删文件（要留给「基于上一条」复用），所以超限时
        # 先清掉既不在草稿里、也没被任何任务引用的旧临时图，再谈拒绝
        draft = (state.ui.get("drafts") or {}).get(cap.get("id", ""), {}) or {}
        service.prune_refs(draft.get("refs") or [], max(max_refs, limit // 3))
        if len(state.refs.as_list()) >= limit:
            raise HTTPException(400, "上传目录里的临时文件太多，先清一清")
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
