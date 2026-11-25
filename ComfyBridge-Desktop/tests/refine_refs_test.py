"""「基于上一条」参考图回归：跨 prune / 重启都不掉图。

起因（用户报的问题）：点「↗ 基于上一条」弹「参考图已被清理，带不回来」。
根因有两条，都在服务端：

    1. ``prune_refs`` 只按内存里的 ref id 保护，而 ref id 一重启就重排、
       「基于上一条」重新登记后也会换新 id —— id 和磁盘文件会脱钩，
       于是还在被任务引用的图被当成孤儿删掉。
    2. ``restore_refs`` 每次都重新复制一份文件，点 19 次上传目录就多 19 个副本。

这个套件把那条路径走一遍：
    上传参考图 → 提交设定图（真出图）→ 灌满上传目录逼 prune 真跑
    → 断言参考图文件还在 → 「基于上一条」能把图带回来 → 连点两次不重复落文件。

前置：应用在 http://127.0.0.1:8190、ComfyUI 在 8188 在线、队列空闲。
运行：``.venv\\Scripts\\python.exe tests\\refine_refs_test.py``

注意：本套件会往 uploadDir 写约 30 张几十字节的纯色 PNG（道具图，只为了把
目录灌到上限触发 prune）。跑完会尽力删掉它们；即使没删干净也无业务影响，
后续上传自带的 prune 会回收。
"""

from __future__ import annotations

import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Any

import httpx

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
HTTP = httpx.Client(base_url=BASE, timeout=60.0)
RESULTS: list[tuple[str, bool, str]] = []
PROPS: list[str] = []

SAMPLE_CHAR = ("二十五岁中国女子，清冷气质，长直黑发用竹簪高盘，细眉凤眼，穿素白交领长衫，"
               "腰系青色织带，脚踩黑布靴，腰侧挂一枚青玉玉佩")


def check(name: str, ok: Any, detail: str = "") -> None:
    passed = bool(ok)
    RESULTS.append((name, passed, detail[:240]))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail[:200]}" if detail else ""), flush=True)


def api(path: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    resp = HTTP.request(method, path, json=body) if body is not None else HTTP.request(method, path)
    try:
        return resp.json()
    except ValueError:
        return {"ok": False, "reason": f"HTTP {resp.status_code}"}


def png_bytes(seed: int, width: int = 16, height: int = 16) -> bytes:
    """造一张纯色 PNG。只为了让「上传参考图」这条链路有真文件可传，seed 决定颜色。"""
    color = bytes([(seed * 7) % 256, (seed * 13) % 256, (seed * 29) % 256])
    raw = b"".join(b"\x00" + color * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def upload(seed: int, prop: bool = False) -> str:
    resp = HTTP.post("/api/uploads", files={"file": (f"refine_{seed}.png", png_bytes(seed), "image/png")})
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"第 {seed} 张被拦下：{data.get('reason') or data}")
    ref_id = data["data"]["id"]
    if prop:
        PROPS.append(ref_id)
    return ref_id


def upload_limit() -> int:
    """上传接口的容量上限：max(已启用能力的 maxRefs) × 10。到这一步才会触发 prune。"""
    st = api("/api/state")
    widest = max([int(c.get("maxRefs", 3) or 3) for c in st.get("capabilities") or []
                  if c.get("enabled") and c.get("workflows")] or [3])
    return max(1, widest) * 10


def wait_run(job_id: str, timeout: float = 240.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = job_of(job_id)
        if not job:
            return {}
        run = job["runs"][0] if job["runs"] else {}
        if run.get("state") in ("done", "failed", "canceled"):
            return run
        time.sleep(1.5)
    return {}


def job_of(job_id: str) -> dict[str, Any]:
    return next((j for j in api("/api/state").get("jobs", []) if j["id"] == job_id), {})


def media_ok(ref_id: str) -> bool:
    resp = HTTP.get("/api/media", params={"ref": ref_id})
    return resp.status_code == 200 and len(resp.content) > 50


def main() -> int:
    print("\n===== 参考图：「基于上一条」跨 prune 不掉图 =====", flush=True)
    limit = upload_limit()
    print(f"上传上限 {limit} 张（超过才触发 prune）", flush=True)

    # 1) 上传参考图，并用它提交一条设定图（参考生图 · 默认档，真出图）
    ref = upload(900)
    check("1 参考图上传成功", bool(ref), ref)

    sent = api("/api/tasks", "POST", {
        "capId": "sheet.character", "grade": "默认", "refs": [ref], "wf": "设定图-参考生图",
        "sheet": {"layout": "C", "ink": "color", "ref": "lock", "style": "写实摄影",
                  "lineStyle": None, "character": SAMPLE_CHAR, "grade": "默认"},
    })
    ok_submit = bool(sent.get("ok"))
    check("2 提交成功（参考生图 · 默认）", ok_submit, str(sent.get("reason") or ""))
    if not ok_submit:
        return report()

    job_id = sent["data"]["job_id"]
    run = wait_run(job_id)
    check("3 这一张真实出图完成", run.get("state") == "done",
          f"{run.get('state')} {run.get('size') or ''}")

    saved_path = Path(str(((job_of(job_id).get("refsInfo") or [{}])[0]).get("path") or ""))
    check("4 任务记录里存下了参考图的本地副本路径", saved_path.is_file(), str(saved_path))
    if not saved_path.is_file():
        return report()
    upload_dir = saved_path.parent

    # 2) 把上传目录灌到上限，逼 prune 真跑一遍。
    #    关键断言：这条任务还在会话里，它的参考图不能被当孤儿删掉。
    print(f"灌入 {limit} 张道具图触发 prune…", flush=True)
    blocked_reason = ""
    for i in range(limit):
        try:
            upload(2000 + i, prop=True)
        except RuntimeError as exc:
            blocked_reason = str(exc)
            break
    check("5 prune 跑过且还留得出空间（灌图没被卡死）", not blocked_reason,
          blocked_reason or f"已灌 {len(PROPS)} 张")
    check("6 prune 之后参考图文件仍在（不再把 id 和文件脱钩误判孤儿）",
          saved_path.is_file(), str(saved_path))

    # 3) 「基于上一条」：按任务记录重新登记，必须把图带回来
    resp = api(f"/api/tasks/{job_id}/refs", "POST", {})
    back = ((resp.get("data") or {}).get("refs") or []) if resp.get("ok") else []
    check("7 「基于上一条」把参考图带回来了", len(back) == 1, str(resp.get("reason") or back))
    if not back:
        return report()
    check("8 带回来的图真的能读取（缩略图有内容）", media_ok(str(back[0]["id"])), str(back[0]["id"]))

    # 4) 连点两次不能多出一份副本（以前点几次就多几个文件）
    files_before = len(list(upload_dir.glob("*")))
    again = api(f"/api/tasks/{job_id}/refs", "POST", {})
    again_refs = ((again.get("data") or {}).get("refs") or []) if again.get("ok") else []
    files_after = len(list(upload_dir.glob("*")))
    check("9 再点一次「基于上一条」复用同一张图（不重复落文件）",
          len(again_refs) == 1 and again_refs[0]["id"] == back[0]["id"] and files_after == files_before,
          f"{back[0]['id']} vs {again_refs[0]['id'] if again_refs else '—'} / "
          f"文件 {files_before} → {files_after}")

    # 5) 服务端根本不存在的任务要给出可读的拒绝，而不是 500
    missing = api("/api/tasks/job_不存在/refs", "POST", {})
    check("10 对不存在的任务给出可读拒绝", missing.get("ok") is False and bool(missing.get("reason")),
          str(missing.get("reason"))[:60])

    return report()


def cleanup() -> None:
    """道具图没有业务含义，跑完尽力删掉；失败不影响结论。"""
    for ref_id in PROPS:
        try:
            HTTP.delete(f"/api/uploads/{ref_id}")
        except Exception:  # noqa: BLE001
            pass


def report() -> int:
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n================ 结果 ================", flush=True)
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"FAIL  {name}  {detail}", flush=True)
    print(f"通过 {passed}/{len(RESULTS)}", flush=True)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        cleanup()
    sys.exit(code)
