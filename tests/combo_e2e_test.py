"""角色设定图「组合 / 档位」E2E：18 个组合 + 档位参数覆盖 + 同图换档 + 页面交互。

这一版的口径（跟旧版「档位=版式」完全不同）：
    组合 = 子功能 × 版式 × 画法 × 参考方式，一共 18 条默认配方；
    档位挂在组合上，只能改参数（画幅/像素/倍数/CFG/sigma），提示词一个字都不能变。
所以本文件重点断言：同组合不同档位「提示词完全相同、只有尺寸/参数不同」。

前置：应用在 http://127.0.0.1:8190、ComfyUI 在 8188 在线、队列空闲。
运行：
    .venv\\Scripts\\python.exe tests\\combo_e2e_test.py            # 接口 + 页面 + 5 张真实出图
    .venv\\Scripts\\python.exe tests\\combo_e2e_test.py a           # 只跑 A 段（接口）
    .venv\\Scripts\\python.exe tests\\combo_e2e_test.py bcd         # 只跑指定段
    .venv\\Scripts\\python.exe tests\\combo_e2e_test.py gen18       # 额外把 18 个组合各出一张（约 10 分钟）
"""

from __future__ import annotations

import io
import json
import struct
import subprocess
import sys
import time
import urllib.request
import zlib
from pathlib import Path
from typing import Any

import httpx
import websocket

APP = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8190"
CDP_PORT = 9226
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = APP / ".cache" / "edge_combo"
HTTP = httpx.Client(base_url=BASE, timeout=40.0)

RESULTS: list[tuple[str, bool, str]] = []
UPLOADED: list[str] = []
CREATED_GRADE_COMBOS: list[str] = []
SAMPLE_CHAR = ("二十五岁中国女子，清冷气质，长直黑发用竹簪高盘，细眉凤眼，穿素白交领长衫，"
               "腰系青色织带，脚踩黑布靴，腰侧挂一枚青玉玉佩")
STYLE = "写实摄影"
LINE_STYLE = "W3"

LAYOUT_MARK = {"A": ["三视图", "16:9"], "B": ["四个等大的方格", "9:16"], "C": ["画面正中只有一个角色"]}
LAYOUT_SIZE = {"A": (1536, 864), "B": (864, 1536), "C": (864, 1536)}
WF_BY_REF = {"none": "设定图-文生图", "lock": "设定图-参考生图", "style": "设定图-参考生图"}


# ---------------------------------------------------------------- 基础工具
def check(name: str, ok: Any, detail: str = "") -> None:
    passed = bool(ok)
    RESULTS.append((name, passed, detail[:240]))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail[:200]}" if detail else ""), flush=True)


def api(path: str, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
    resp = HTTP.request(method, path, json=body) if body is not None else HTTP.request(method, path)
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": resp.text[:200]}


def combos() -> list[dict[str, Any]]:
    return api("/api/state").get("sheetCombos") or []


def combo(cid: str) -> dict[str, Any]:
    return next((c for c in combos() if c["id"] == cid), {})


def grade_names(cid: str) -> list[str]:
    return [g["name"] for g in (combo(cid).get("grades") or [])]


def png_bytes(width: int = 64, height: int = 96) -> bytes:
    """造一张纯色 PNG：只为了让「上传参考图」这条链路有真文件可传。"""
    raw = b"".join(b"\x00" + b"\x9a\xb0\xc8" * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def upload_ref() -> str:
    resp = HTTP.post("/api/uploads", files={"file": ("combo_ref.png", png_bytes(), "image/png")})
    data = resp.json()
    assert data.get("ok"), f"上传失败：{data}"
    UPLOADED.append(data["data"]["id"])
    return data["data"]["id"]


def preview(layout: str, ink: str, ref: str, grade: str = "", character: str = SAMPLE_CHAR) -> dict[str, Any]:
    body = {"layout": layout, "ink": ink, "ref": ref, "style": STYLE,
            "lineStyle": LINE_STYLE, "character": character}
    if grade:
        body["grade"] = grade
    return api("/api/sheet/preview", "POST", body)


def wait_idle(timeout: float = 200.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api("/api/state")
        if not any(r["state"] in ("queued", "running") for j in st.get("jobs", []) for r in j["runs"]):
            return True
        time.sleep(1)
    return False


def submit_sheet(layout: str, ink: str, ref: str, grade: str, refs: list[str]) -> dict[str, Any]:
    wait_idle()
    body = {
        "capId": "sheet.character", "grade": grade, "refs": refs, "wf": WF_BY_REF[ref],
        "sheet": {"layout": layout, "ink": ink, "ref": ref, "style": STYLE,
                  "lineStyle": LINE_STYLE, "character": SAMPLE_CHAR, "grade": grade},
    }
    return api("/api/tasks", "POST", body)


def wait_run(job_id: str, timeout: float = 240.0) -> dict[str, Any]:
    """等这条任务的第一张跑完，返回那个 run。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = api("/api/state")
        job = next((j for j in st.get("jobs", []) if j["id"] == job_id), None)
        if not job:
            return {}
        run = job["runs"][0] if job["runs"] else {}
        if run.get("state") in ("done", "failed", "canceled"):
            run["_job"] = job
            return run
        time.sleep(1.5)
    return {}


def run_size(run: dict[str, Any]) -> str:
    """这一张真实出图的尺寸：优先用 run.size（读的回图元信息），退到文件的宽高。"""
    if run.get("size"):
        return str(run["size"])
    files = run.get("files") or []
    if files and files[0].get("width") and files[0].get("height"):
        return f"{files[0]['width']}×{files[0]['height']}"
    return ""


def size_wh(text: str) -> tuple[int, int]:
    import re
    match = re.search(r"(\d+)\s*[×x]\s*(\d+)", str(text or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


# ---------------------------------------------------------------- A. 组合与组装
def section_api() -> None:
    print("\n===== A 组合枚举与提示词组装 =====", flush=True)
    items = combos()
    check("A1 18 个组合、id 唯一", len(items) == 18 and len({c["id"] for c in items}) == 18,
          f"{len(items)} 个")
    t2i = [c for c in items if c["ref"] == "none"]
    i2i = [c for c in items if c["ref"] != "none"]
    check("A2 文生图 6 个 / 图生图 12 个", len(t2i) == 6 and len(i2i) == 12,
          f"{len(t2i)} + {len(i2i)}")
    check("A3 组合 → 工作流映射正确",
          all(c["wf"] == WF_BY_REF[c["ref"]] for c in items),
          str({c["id"]: c["wf"] for c in items if c["wf"] != WF_BY_REF[c["ref"]]}))

    bad: list[str] = []
    for c in items:
        got = preview(c["layout"], c["ink"], c["ref"])
        if not got.get("ok"):
            bad.append(f"{c['id']} 预览失败：{got.get('reason')}")
            continue
        d = got["data"]
        marks = LAYOUT_MARK[c["layout"]]
        if not all(m in d["prompt"] for m in marks):
            bad.append(f"{c['id']} 缺版式关键句 {marks}")
        w, h = LAYOUT_SIZE[c["layout"]]
        if str(w) not in d["size"] or str(h) not in d["size"]:
            bad.append(f"{c['id']} 尺寸不对：{d['size']}")
        if d["wf"] != WF_BY_REF[c["ref"]]:
            bad.append(f"{c['id']} wf={d['wf']}")
    check("A4 18 个组合逐个组装：版式句 + 画幅 + 工作流全对", not bad, "；".join(bad[:4]))

    # 画法：线稿把风格行整行换掉
    color = preview("C", "color", "none")["data"]["prompt"]
    line = preview("C", "line", "none")["data"]["prompt"]
    line_sentence = api("/api/sheet/library")["data"]["line_styles"][2]["text"]
    check("A5 线稿用线稿句、彩色用风格句",
          line_sentence[:16] in line and line_sentence[:16] not in color
          and "写实人像摄影" in color and "写实人像摄影" not in line)

    lib = api("/api/sheet/library")["data"]
    r1 = lib["blocks"]["R1"]["text"][:12]
    r2 = lib["blocks"]["R2"]["text"][:12]
    lock = preview("C", "color", "lock")["data"]["prompt"]
    style = preview("C", "color", "style")["data"]["prompt"]
    none = preview("C", "color", "none")["data"]["prompt"]
    check("A6 锁定同角色含 R1 + 尾句；只借画风以 R2 开头；无参考两者都没有",
          r1 in lock and lock.rstrip().endswith("画风与图 1 保持一致")
          and style.startswith(r2) and r1 not in style and r1 not in none and r2 not in none)

    # 档位不改提示词：这是整条需求的核心
    cid = "C.line.lock"
    names = grade_names(cid)
    if len(names) < 2:
        api(f"/api/sheet/combos/{cid}/grades", "PUT", {"grades": [
            {"name": "快速", "note": "测试", "params": {"megapixels": 0.6}},
            {"name": "精细", "note": "测试", "params": {"megapixels": 2, "multiple": 20}}]})
        CREATED_GRADE_COMBOS.append(cid)
        names = grade_names(cid)
    prompts = {n: preview("C", "line", "lock", grade=n)["data"] for n in names}
    same_prompt = len({p["prompt"] for p in prompts.values()}) == 1
    diff_size = len({p["size"] for p in prompts.values()}) == len(prompts)
    check("A7 同组合各档位提示词完全相同、只有尺寸不同",
          same_prompt and diff_size,
          f"{[(n, p['size']) for n, p in prompts.items()]}")


# ---------------------------------------------------------------- B. 档位模型与校验
def section_grades() -> None:
    print("\n===== B 档位：默认 / 覆盖 / 隔离 / 校验 =====", flush=True)
    cid = "B.color.none"
    master = next(g for g in combo(cid)["grades"] if g["locked"])
    check("B1 默认不可删且带版式画幅",
          master["name"] == "默认" and master["locked"]
          and master["params"].get("aspect_ratio", "").startswith("9:16")
          and master["params"].get("megapixels") == 1.265625,
          json.dumps(master["params"], ensure_ascii=False))

    rows = [{"name": "只改像素", "note": "", "params": {"megapixels": 1.8}}]
    saved = api(f"/api/sheet/combos/{cid}/grades", "PUT", {"grades": rows})
    CREATED_GRADE_COMBOS.append(cid)
    grade = next((g for g in saved["data"]["grades"] if g["name"] == "只改像素"), {})
    # 档位只记「它改了什么」；宽高比/对齐倍数从默认继承（这样默认升级会一起生效）
    inherited = preview("B", "color", "none", grade="只改像素")["data"]
    check("B2 自建档位只覆盖它写的字段（其余继承默认）",
          set(grade.get("params", {}).keys()) == {"megapixels"}
          and grade["params"]["megapixels"] == 1.8
          and inherited["size"].startswith("9:16") and "1024" in inherited["size"],
          f"{json.dumps(grade.get('params'), ensure_ascii=False)} → {inherited['size']}")

    bad = preview("B", "color", "none", grade="不存在的档位")
    check("B3 提交不存在的档位被拦下（并告诉有哪些可选）",
          not bad.get("ok") and "没有档位" in (bad.get("reason") or ""),
          bad.get("reason", ""))

    other = grade_names("B.color.lock")
    check("B4 档位跨组合隔离：同名档位不会出现在别的组合",
          "只改像素" in grade_names("B.color.none") and "只改像素" not in other,
          str(other))

    cases = {
        "空名": {"grades": [{"name": "  ", "params": {}}]},
        "重名": {"grades": [{"name": "双份", "params": {}}, {"name": "双份", "params": {}}]},
        "叫默认": {"grades": [{"name": "默认", "params": {}}]},
        "未知节点": {"grades": [{"name": "乱填", "params": {"999": {"cfg": 2}}}]},
        "未知字段": {"grades": [{"name": "乱填", "params": {"warmth": 3}}]},
        "像素越界": {"grades": [{"name": "太大", "params": {"megapixels": 99}}]},
        "sigma 格式错": {"grades": [{"name": "步数", "params": {"sigmas": "abc"}}]},
    }
    rejects = {}
    for label, body in cases.items():
        res = api(f"/api/sheet/combos/{cid}/grades", "PUT", body)
        rejects[label] = not res.get("ok")
    check("B5 非法档位全部被拒（空名/重名/默认/未知节点/未知字段/越界/格式）",
          all(rejects.values()), str({k: v for k, v in rejects.items() if not v}))

    after = grade_names(cid)
    check("B6 被拒的写入没有落盘", after == ["默认", "只改像素"], str(after))

    raw = (APP / "config.yaml").read_text(encoding="utf-8")
    check("B7 档位写进了 config.yaml 的 sheetGrades", "sheetGrades:" in raw and "只改像素" in raw)

    removed = api(f"/api/sheet/combos/{cid}/grades", "PUT", {"grades": []})
    check("B8 清空后只剩默认、config 里也清干净",
          [g["name"] for g in removed["data"]["grades"]] == ["默认"]
          and "只改像素" not in (APP / "config.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- C. 真实出图 + 同图换档
def section_generate() -> dict[str, Any]:
    print("\n===== C 真实出图：默认 / 快速 / 精细 / 队列换档 =====", flush=True)
    cid = "C.color.lock"
    api(f"/api/sheet/combos/{cid}/grades", "PUT", {"grades": [
        {"name": "快速", "note": "草稿", "params": {"megapixels": 0.6}},
        {"name": "精细", "note": "高清", "params": {"megapixels": 2, "multiple": 20}}]})
    CREATED_GRADE_COMBOS.append(cid)
    ref = upload_ref()

    runs: dict[str, dict[str, Any]] = {}
    for grade in ("默认", "快速", "精细"):
        got = submit_sheet("C", "color", "lock", grade, [ref])
        if not got.get("ok"):
            check(f"C 提交「{grade}」", False, got.get("reason", ""))
            continue
        run = wait_run(got["data"]["job_id"])
        runs[grade] = run
        check(f"C 提交「{grade}」并出图", run.get("state") == "done",
              f"{run.get('state')} {run_size(run)}")

    if len(runs) == 3 and all(r.get("state") == "done" for r in runs.values()):
        sizes = {g: run_size(r) for g, r in runs.items()}
        w = {g: size_wh(s)[0] for g, s in sizes.items()}
        check("C4 三档尺寸不同且递增（快速 < 默认 < 精细）",
              w["快速"] < w["默认"] < w["精细"], str(sizes))
        check("C5 三档工作流相同", len({r.get("wf") for r in runs.values()}) == 1,
              str({r.get("wf") for r in runs.values()}))
        # 提示词来自任务记录（run 上不存 prompt，取 job 的）
        jobs = {g: r.get("_job", {}) for g, r in runs.items()}
        check("C6 三档用的是同一条提示词（组合决定，档位改不动）",
              len({j.get("prompt") for j in jobs.values()}) == 1,
              f"长度 {[len(j.get('prompt') or '') for j in jobs.values()]}")
        check("C7 三档的档位名各自正确",
              {g: (j.get("runs") or [{}])[0].get("grade") for g, j in jobs.items()}
              == {"默认": "默认", "快速": "快速", "精细": "精细"})

        # 加入队列生成：同配方同种子，只换档
        base_job = runs["默认"].get("_job", {})
        wait_idle()
        queued = api(f"/api/tasks/{base_job['id']}/queue", "POST", {"grade": "精细"})
        if queued.get("ok"):
            deadline = time.time() + 240
            qrun = {}
            while time.time() < deadline:
                st = api("/api/state")
                job = next((j for j in st["jobs"] if j["id"] == base_job["id"]), None)
                qrun = (job or {}).get("runs", [{}, {}])[1] if job else {}
                if qrun.get("state") in ("done", "failed", "canceled"):
                    break
                time.sleep(1.5)
            check("C8 加入队列生成（换精细档）出图成功", qrun.get("state") == "done",
                  f"{qrun.get('state')} {run_size(qrun)}")
            check("C9 队列这一档沿用同一张的种子",
                  qrun.get("seed") and qrun.get("seed") == base_job.get("seed"),
                  f"{qrun.get('seed')} vs {base_job.get('seed')}")
            check("C10 队列这一档是精细尺寸（比默认大），参数也确实是精细档的",
                  size_wh(run_size(qrun))[0] > size_wh(sizes.get("默认", ""))[0]
                  and (qrun.get("params") or {}).get("502", {}).get("megapixels") == 2,
                  f"{run_size(qrun)} vs 默认 {sizes.get('默认')}")
        else:
            check("C8 加入队列生成（换精细档）", False, queued.get("reason", ""))

    bad = api(f"/api/tasks/{runs.get('默认', {}).get('_job', {}).get('id', 'x')}/queue",
              "POST", {"grade": "根本没有的档位"})
    check("C11 队列里填一个不存在的档位会被拒", not bad.get("ok"), bad.get("reason", ""))
    return runs


# ---------------------------------------------------------------- CDP 工具
class CDP:
    def __init__(self, url: str) -> None:
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
        self.seq = 0
        self.events: list[dict[str, Any]] = []

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            self.events.append(msg)

    def js(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate",
                           {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise RuntimeError(f"JS 异常: {json.dumps(result['exceptionDetails'], ensure_ascii=False)[:400]}")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 20.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.js(expression):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.35)
        return False

    def pause(self, ms: int = 450) -> None:
        self.js(f"new Promise(r=>setTimeout(()=>r(true),{ms}))")

    def errors(self) -> list[str]:
        out = []
        for event in self.events:
            if event.get("method") == "Runtime.exceptionThrown":
                detail = event["params"].get("exceptionDetails", {})
                out.append(str(detail.get("text")) + " " + str(
                    (detail.get("exception") or {}).get("description", ""))[:200])
        return out

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def click(cdp: CDP, selector: str) -> Any:
    return cdp.js(f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(!e)return false;e.click();return true;}})()")


def start_browser() -> tuple[subprocess.Popen, CDP]:
    PROFILE.mkdir(parents=True, exist_ok=True)
    api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps", "capId": "sheet.character"}})
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--disable-extensions", "--no-first-run", "--disable-background-networking",
        "--remote-allow-origins=*", f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE}", "--window-size=1440,960", BASE + "/#studio",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as resp:
                targets = json.loads(resp.read().decode())
            page = next((t for t in targets if t.get("type") == "page" and "8190" in t.get("url", "")), None)
            if page:
                cdp = CDP(page["webSocketDebuggerUrl"])
                cdp.call("Runtime.enable")
                cdp.call("Page.enable")
                return proc, cdp
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("连不上 Edge 的调试端口")


# ---------------------------------------------------------------- D. 创作台
def section_studio(cdp: CDP) -> None:
    print("\n===== D 创作台：组合切换 / 档位 / 提交 =====", flush=True)
    cdp.call("Page.navigate", {"url": BASE + "/#studio"})
    cdp.wait("typeof UI!=='undefined' && !!S && !!S.capabilities && !!S.sheetCombos", 30)
    cdp.pause(700)
    # 复位到干净状态（上一轮测试会在服务端留草稿）
    cdp.js("""(()=>{ UI.capId='sheet.character'; UI._draftCap='sheet.character'; UI.mode='i2i';
        UI.refs=[]; UI.sheetOpen=false; UI.sheetPreview=null;
        UI.sheetForm={layout:'C',ink:'color',lineStyle:'W3',ref:'lock',grade:'默认',
                      character:'二十五岁中国女子，清冷气质，长直黑发用竹簪高盘',style:'写实摄影',styleText:''};
        render(); return true;})()""")
    cdp.pause(600)

    check("D1 档位行存在且默认停在默认",
          cdp.js("!!document.querySelector('[data-sheet-grade]') && "
                 "document.querySelector('[data-sheet-grade].on').textContent.trim()==='默认'"))
    check("D2 当前组合 = 单张全身 · 彩色 · 锁定同角色（9:16 864×1536）",
          cdp.js("(document.body.innerText||'').includes('出图 864 × 1536')"),
          str(cdp.js("(()=>{const c=sheetCombo();return c&&c.name})()")))

    click(cdp, '[data-sheet-layout="A"]')
    cdp.pause(500)
    check("D3 切版式 A → 出图变成 1536 × 864",
          cdp.js("(document.body.innerText||'').includes('出图 1536 × 864')"),
          str(cdp.js("(()=>{const c=sheetCombo();return c&&c.id})()")))
    check("D4 切版式不会改档位名（版式不再兼职档位）",
          cdp.js("UI.sheetForm.grade==='默认'"))

    click(cdp, '[data-sheet-ink="line"]')
    cdp.pause(500)
    cdp.js("refreshSheetPreview()")
    cdp.pause(900)
    line_txt = cdp.js("(UI.sheetPreview||{}).prompt||''")
    check("D5 切线稿 → 预览里是线稿句、没有彩色风格句",
          "写实人像摄影" not in line_txt and len(line_txt) > 300)

    click(cdp, '[data-sheet-ref="style"]')
    cdp.pause(500)
    cdp.js("refreshSheetPreview()")
    cdp.pause(900)
    style_txt = cdp.js("(UI.sheetPreview||{}).prompt||''")
    check("D6 切「只借画风」→ 预览以 R2 开头",
          style_txt.startswith("图 1 只用来参考画风和笔触"), style_txt[:24])

    # 切到有自建档位的组合：A.line.lock 没有，换 C.line.lock
    api("/api/sheet/combos/C.line.lock/grades", "PUT", {"grades": [
        {"name": "快速", "note": "草稿", "params": {"megapixels": 0.6}},
        {"name": "精细", "note": "高清", "params": {"megapixels": 2, "multiple": 20}}]})
    CREATED_GRADE_COMBOS.append("C.line.lock")
    cdp.js("pull().then(()=>{render();return true;})")
    cdp.pause(900)
    cdp.js("""(()=>{ UI.sheetForm.layout='C'; UI.sheetForm.ink='line'; UI.sheetForm.ref='lock';
        UI.sheetForm.grade='默认'; render(); return true;})()""")
    cdp.pause(600)
    check("D7 这个组合有 3 个档位（默认 + 快速 + 精细）",
          cdp.js("document.querySelectorAll('[data-sheet-grade]').length===3"),
          str(cdp.js("[...document.querySelectorAll('[data-sheet-grade]')].map(e=>e.textContent.trim())")))

    click(cdp, '[data-sheet-grade="精细"]')
    cdp.pause(700)
    check("D8 点「精细」→ 出图尺寸变 1080 × 1940",
          cdp.js("UI.sheetForm.grade==='精细' && (document.body.innerText||'').includes('1080 × 1940')"),
          str(cdp.js("UI.sheetForm.grade")))

    # 换到一个没有自建档位的组合：档位自动回默认
    cdp.js("""(()=>{ UI.sheetForm.layout='B'; UI.sheetForm.grade='精细'; render(); return true;})()""")
    cdp.pause(600)
    check("D9 切到没有该档位的组合 → 自动回到默认且只剩 1 个档位",
          cdp.js("UI.sheetForm.grade==='默认' && document.querySelectorAll('[data-sheet-grade]').length===1"),
          str(cdp.js("UI.sheetForm.grade+'/'+document.querySelectorAll('[data-sheet-grade]').length")))

    # 看完整提示词 = 服务端那条。两处必须对齐：
    #   1) 画法/参考方式拨回线稿+锁定，否则比的是另一条配方；
    #   2) 角色描述换成 SAMPLE_CHAR，否则页面用的是段首那段短描述，长度天然不同。
    cdp.js(f"""(()=>{{ UI.sheetForm.layout='C'; UI.sheetForm.ink='line';
        UI.sheetForm.ref='lock'; UI.sheetForm.grade='默认';
        UI.sheetForm.character={json.dumps(SAMPLE_CHAR)}; render(); return true;}})()""")
    cdp.pause(600)
    click(cdp, "#sheetPvBtn")
    cdp.pause(1200)
    page_prompt = cdp.js("(UI.sheetPreview||{}).prompt||''")
    server_prompt = preview("C", "line", "lock")["data"]["prompt"]
    check("D10 「看完整提示词」与服务端组装逐字一致",
          page_prompt == server_prompt, f"{len(page_prompt)} vs {len(server_prompt)}")

    # 连续乱切 7 次，最终状态必须跟最后一次选择一致
    cdp.js("""(async()=>{
        const seq=[['A','color','lock'],['B','line','style'],['C','color','lock'],['A','line','style'],
                   ['C','line','lock'],['B','color','style'],['C','color','lock']];
        for (const [l,i,r] of seq){ UI.sheetForm.layout=l; UI.sheetForm.ink=i; UI.sheetForm.ref=r; render(); await new Promise(x=>setTimeout(x,60)); }
        return true;})()""")
    cdp.pause(700)
    check("D11 连续乱切 7 次后组合与档位仍然自洽",
          cdp.js("""(()=>{const c=sheetCombo(), g=sheetGrade();
              if(!c||!g) return false;
              const want=UI.sheetForm.layout+'.'+UI.sheetForm.ink+'.'+UI.sheetForm.ref;
              return c.id===want && c.grades.some(x=>x.name===g.name);})()"""),
          str(cdp.js("UI.sheetForm.layout+'.'+UI.sheetForm.ink+'.'+UI.sheetForm.ref+' → '+(sheetCombo()||{}).id")))

    # 提交一次（快速档），验证页面真的把档位名发出去
    api("/api/sheet/combos/C.color.lock/grades", "PUT", {"grades": [
        {"name": "快速", "note": "草稿", "params": {"megapixels": 0.6}},
        {"name": "精细", "note": "高清", "params": {"megapixels": 2, "multiple": 20}}]})
    CREATED_GRADE_COMBOS.append("C.color.lock")
    # 档位是直接从接口写的，页面还拿着旧快照；不拉一次，sheetGrade() 会以为
    # 「快速」不在这个组合里，自动回落默认，提交出去的就是默认档（假失败）。
    cdp.js("(async()=>{ await pull(); return true; })()")
    cdp.wait("(()=>{const c=(S.sheetCombos||[]).find(x=>x.id==='C.color.lock');"
             "return !!c && c.grades.some(g=>g.name==='快速');})()", 20)
    ref = upload_ref()
    cdp.js(f"""(()=>{{ UI.refs=[{{id:{json.dumps(ref)},kind:'image'}}]; UI.mode='i2i';
        UI.sheetForm.layout='C'; UI.sheetForm.ink='color'; UI.sheetForm.ref='lock';
        UI.sheetForm.grade='快速'; render(); return true;}})()""")
    cdp.pause(600)
    wait_idle()
    sent = cdp.js("""(async()=>{
        await doSend();
        const st=await (await fetch('/api/state')).json();
        const job=st.jobs[st.jobs.length-1];
        return {id:job.id, grade:(job.runs[0]||{}).grade, combo:(job.sheet||{}).combo, wf:job.wf,
                size:(job.runs[0]||{}).sizeExpected};
    })()""")
    check("D12 创作台提交时带的是档位名（不是版式名）",
          isinstance(sent, dict) and sent.get("grade") == "快速", str(sent))
    check("D13 提交的任务记下了组合 id", isinstance(sent, dict) and sent.get("combo") == "C.color.lock", str(sent))
    run = wait_run(sent.get("id") or "") if isinstance(sent, dict) else {}
    check("D14 这一张跑完了", run.get("state") == "done", f"{run.get('state')} {run_size(run)}")
    card = cdp.js(f"""(async()=>{{
        await pull(); render();
        const el=document.getElementById({json.dumps(sent.get('id') if isinstance(sent, dict) else '')});
        if(!el) return {{found:false}};
        return {{found:true, head:(el.querySelector('.head')||{{}}).innerText||'',
                 chip:(el.querySelector('.gradechip')||{{}}).textContent||''}};
    }})()""")
    check("D15 卡片头显示组合名 + 档位 chip",
          isinstance(card, dict) and card.get("found")
          and "单张全身" in (card.get("head") or "") and "快速" in (card.get("chip") or ""),
          str(card))


# ---------------------------------------------------------------- E. 管理台
def section_admin(cdp: CDP) -> None:
    """管理台「档位表」按组合组织，页面结构与交互对齐「图片生成」档位表：
       顶部横向分段按钮切组合、左栏 ①②③、右栏 ④ 新增 / ⑤ 已有。"""
    print("\n===== E 管理台：档位表按组合组织（风格对齐图片生成） =====", flush=True)
    api("/api/sheet/combos/C.line.lock/grades", "PUT", {"grades": [
        {"name": "快速", "note": "草稿", "params": {"megapixels": 0.6}},
        {"name": "精细", "note": "高清", "params": {"megapixels": 2, "multiple": 20}}]})
    CREATED_GRADE_COMBOS.append("C.line.lock")
    cdp.call("Page.navigate", {"url": BASE + "/#admin"})
    cdp.wait("typeof UI!=='undefined' && !!S && !!S.sheetCombos && S.sheetCombos.length===18", 30)
    cdp.pause(700)
    # 把「当前在哪一页」写回服务端：只改本地 UI 会被下一轮状态轮询拽回原页
    api("/api/ui", "POST", {"patch": {"route": "admin", "adminPage": "grades",
                                      "capId": "sheet.character"}})
    # 只改 hash 的 navigate 不会重载页面，S 仍是浏览器启动时（档位被 cleanup 清空后）的旧快照，
    # 所以显式 pull 一次再 render，让 ⑤ 拿到刚写入的 快速/精细。
    cdp.js("""(async()=>{UI.route='admin';UI.adminPage='grades';UI.capId='sheet.character';
        UI.comboSel='C.line.lock';UI.gOpen=0;await pull();render();return true;})()""")
    cdp.wait("(comboById('C.line.lock')?.grades||[]).length===3", 10)
    cdp.pause(600)

    # ---- 顶部：组合像创作台那样横着排在按钮上，不再是竖向列表 ----
    check("E1 组合切换是顶部分段按钮（子功能/版式/画法/参考四组）",
          cdp.js("(()=>{const g=['mode','layout','ink','ref'].map(k=>document.querySelectorAll('[data-combo-'+k+']').length);"
                 "return g.every(n=>n>0) && document.querySelectorAll('.segb[data-combo-mode],.segb[data-combo-layout],"
                 ".segb[data-combo-ink],.segb[data-combo-ref]').length>=9;})()"),
          str(cdp.js("['mode','layout','ink','ref'].map(k=>k+':'+document.querySelectorAll('[data-combo-'+k+']').length)")))
    check("E2 顶部按「子功能 / 版式 / 画法 / 参考 / 当前」五行横排，且没有旧的竖向 combo-it 列表",
          cdp.js("document.querySelectorAll('.sheetbar').length===5 && !document.querySelector('.combo-it')"),
          str(cdp.js("document.querySelectorAll('.sheetbar').length")))
    check("E3 当前组合摘要显示组合名 / 子工作流 / 画幅",
          cdp.js("(()=>{const t=document.querySelector('.sheetbar:last-child')?.innerText||'';"
                 "return t.includes('单张全身') && t.includes('设定图-参考生图') && t.includes('864');})()"))

    # ---- 左栏 ①②③：只有指定几个节点能设置，宽高比归到 ③ ----
    check("E4 ② 可设置的参数节点是 3 个（502 / 459_507 / 459_502），不含宽高比",
          cdp.js("(()=>{const a=[...document.querySelectorAll('[data-picknode]')].map(e=>e.dataset.picknode);"
                 "return a.length===3 && !a.some(x=>String(x).includes('aspect')) && "
                 "!document.querySelector('[data-picknode]').innerText.includes('宽高比');})()"),
          str(cdp.js("[...document.querySelectorAll('[data-picknode]')].map(e=>e.dataset.picknode)")))
    check("E5 ③ 不用设的节点里明确列出「宽高比 aspect_ratio · 版式定」",
          cdp.js("[...document.querySelectorAll('.node-list.dim .node-row.no')]"
                 ".some(e=>e.innerText.includes('aspect_ratio') && e.innerText.includes('版式定'))"))
    check("E6 右栏 ④ 新增档位在上、⑤ 已有档位在下（与图片生成同序）",
          cdp.js("(()=>{const a=document.querySelector('.panel.build'),b=document.querySelector('.glist');"
                 "return !!a && !!b && !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);})()"))

    # ---- ⑤ 默认只读 ----
    check("E7 默认行只读（没有删除按钮、名字是文本不是输入框）",
          cdp.js("(()=>{const r=document.querySelector('.grow.def');"
                 "return !!r && !r.querySelector('[data-gdel]') && !r.querySelector('input.gname')"
                 " && !!r.querySelector('.gname-txt');})()"))
    check("E8 ⑤ 已有档位每行显示尺寸与编辑/删除入口（默认 + 快速 + 精细）",
          cdp.js("document.querySelectorAll('.glist .grow').length>=3") and
          cdp.js("[...document.querySelectorAll('.grow-hd')].slice(1).every(e=>e.querySelector('[data-gdel]'))"),
          str(cdp.js("[...document.querySelectorAll('.grow-hd')].map(e=>e.innerText.replace(/\\n/g,' '))")))

    # ---- 顶部按钮真的能切组合，且档位表跟着换 ----
    cdp.js("""document.querySelector('[data-combo-layout="A"]').click(); true""")
    cdp.pause(600)
    check("E9 点「版式 A」切到 A.line.lock（组合由四个按钮推导）",
          cdp.js("UI.comboSel==='A.line.lock'"), str(cdp.js("UI.comboSel")))
    cdp.js("""document.querySelector('[data-combo-mode="t2i"]').click(); true""")
    cdp.pause(600)
    check("E10 切「文生图」得到 A.line.none，参考行自动隐藏",
          cdp.js("UI.comboSel==='A.line.none' && !document.querySelector('[data-combo-ref]')"),
          str(cdp.js("UI.comboSel")))
    cdp.js("(()=>{UI.comboSel='C.line.lock';UI.gOpen=0;render();return true})()")
    cdp.pause(700)

    # ---- ② 勾节点只影响 ④ 表单（不落盘） ----
    picked_before = cdp.js("document.querySelectorAll('.panel.build .node-edit').length")
    cdp.js("""(()=>{const b=document.querySelector('[data-picknode="459_502"] input');
           b.checked=false; b.dispatchEvent(new Event('change',{bubbles:true})); return true;})()""")
    cdp.pause(600)
    picked_after = cdp.js("document.querySelectorAll('.panel.build .node-edit').length")
    check("E11 取消勾选一个节点，④ 表单少一块（未落盘）",
          picked_after == picked_before - 1 and
          cdp.js("document.querySelectorAll('[data-picknode] input:checked').length===2"),
          f"{picked_before} -> {picked_after}")
    cdp.js("""(()=>{const b=document.querySelector('[data-picknode="459_502"] input');
           b.checked=true; b.dispatchEvent(new Event('change',{bubbles:true})); return true;})()""")
    cdp.pause(600)
    # 交互一致性：④ 已填的名字/参数在重绘后不能丢（和图片生成的 UI.draft 同语义）
    cdp.js("""(()=>{const n=document.querySelector('#gfName'), m=document.querySelector('#gfMegapixels');
        n.value='待保存'; n.dispatchEvent(new Event('input',{bubbles:true}));
        m.value='1.4'; m.dispatchEvent(new Event('input',{bubbles:true}));
        render(); return true;})()""")
    cdp.pause(500)
    check("E11b ④ 已填的名字与参数在重绘后保留（输入态不丢）",
          cdp.js("document.querySelector('#gfName')?.value==='待保存' && "
                 "document.querySelector('#gfMegapixels')?.value==='1.4'"),
          str(cdp.js("[document.querySelector('#gfName')?.value, document.querySelector('#gfMegapixels')?.value]")))

    # ---- ④ 新增档位 ----
    made = cdp.js("""(()=>{const n=document.querySelector('#gfName'), m=document.querySelector('#gfMegapixels');
        if(!n||!m) return 'noform'; n.value='测试档'; m.value='1.8';
        const b=document.querySelector('[data-gadd]'); if(!b) return 'nogadd'; b.click(); return 'ok';})()""")
    cdp.pause(1700)
    names = grade_names("C.line.lock")
    check("E12 点「＋ 添加」新建档位「测试档」并落盘",
          made == "ok" and "测试档" in names, f"{made} / {names}")
    check("E13 新档位参数与尺寸正确（1.8MP → 1024 × 1824）",
          "1024" in str([g for g in combo("C.line.lock")["grades"] if g["name"] == "测试档"]),
          str([g for g in combo("C.line.lock")["grades"] if g["name"] == "测试档"]))

    # ---- ⑤ 展开这一档：参数生效核对 ----
    cdp.js("""(()=>{const rows=[...document.querySelectorAll('.grow')];
        const i=rows.findIndex(x=>(x.querySelector('input.gname')||{}).value==='测试档');
        if(i>=0){UI.gOpen=i;render();} return i;})()""")
    cdp.wait("document.querySelector('.grow.open .effbox') && "
             "/参数全部生效/.test(document.querySelector('.grow.open .effbox').innerText)", 12)
    check("E14 展开后逐参数自检显示「✓ 参数全部生效」及配置值→实际值",
          cdp.js("(()=>{const e=document.querySelector('.grow.open .effbox');"
                 "return !!e && e.innerText.includes('参数全部生效') && e.querySelectorAll('.effrow').length>=3;})()"),
          str(cdp.js("(document.querySelector('.grow.open .effbox')||{}).innerText||''").replace(chr(10), ' ')[:160]))

    # ---- 编辑：改名 + 覆盖参数，点右上角「保存」整表回写 ----
    cdp.js("""(()=>{const inp=document.querySelector('.grow.open .gname'); if(inp) inp.value='测试档B';
        const m=document.querySelector('.grow.open [data-cv^="megapixels@"]'); if(m) m.value='0.8';
        const s=document.querySelector('[data-combo-save]'); if(s) s.click(); return !!s;})()""")
    cdp.pause(1700)
    names = grade_names("C.line.lock")
    check("E15 保存后改名并覆盖参数（旧名消失、megapixels=0.8）",
          "测试档B" in names and "测试档" not in names and
          any(g["name"] == "测试档B" and g.get("params", {}).get("megapixels") == 0.8
              for g in combo("C.line.lock")["grades"]), str(names))

    # ---- 空名保存被拦下 ----
    before = grade_names("C.line.lock")
    cdp.js("""(()=>{const rows=[...document.querySelectorAll('.grow')];
        const i=rows.findIndex(x=>(x.querySelector('input.gname')||{}).value==='测试档B');
        if(i>=0){UI.gOpen=i;render();} return i;})()""")
    cdp.pause(500)
    cdp.js("""(()=>{const inp=document.querySelector('.grow.open .gname'); if(inp) inp.value='';
        const s=document.querySelector('[data-combo-save]'); if(s) s.click(); return !!s;})()""")
    cdp.pause(1200)
    check("E16 空档位名保存被拦下、列表没变", grade_names("C.line.lock") == before,
          str(grade_names("C.line.lock")))

    # ---- 删除 ----
    deleted = cdp.js("""(()=>{const b=[...document.querySelectorAll('[data-gdel]')]
        .find(x=>x.dataset.gdel==='测试档B'); if(!b) return 'nodel'; b.click(); return 'ok';})()""")
    cdp.pause(1600)
    check("E17 点 ✕ 删除档位后从 ⑤ 表和接口里一起消失",
          deleted == "ok" and "测试档B" not in grade_names("C.line.lock") and
          cdp.js("![...document.querySelectorAll('input.gname')].some(e=>e.value==='测试档B')"),
          str(grade_names("C.line.lock")))

    # ---- 去创作台试这个组合 ----
    cdp.js("(()=>{UI.comboSel='A.line.none';render();return true;})()")
    cdp.pause(600)
    click(cdp, "[data-combo-studio]")
    cdp.pause(1500)
    check("E18「去创作台试这个组合」把版式/画法/参考方式带到创作台",
          cdp.js("UI.route==='studio' && UI.sheetForm.layout==='A' && UI.sheetForm.ink==='line' "
                 "&& UI.mode==='t2i' && UI.sheetForm.grade==='默认'"),
          str(cdp.js("UI.route+'/'+UI.sheetForm.layout+'/'+UI.sheetForm.ink+'/'+UI.mode")))


# ---------------------------------------------------------------- F. 回归
def section_regression(cdp: CDP) -> None:
    print("\n===== F 回归：图片生成 / 页面健康 =====", flush=True)
    st = api("/api/state")
    cap = next(c for c in st["capabilities"] if c["id"] == "image.generate")
    names = [g["name"] for w in cap["workflows"] for g in (w["grades"] or [])]
    check("F1 图片生成的档位没被动过（默认 + 快速/精细（16:9））",
          "默认" in names and any("快速" in n for n in names) and any("精细" in n for n in names),
          str(names[:6]))

    cdp.js("(()=>{UI.capId='image.generate';UI.mode='t2i';UI.params={grade:'默认'};render();return true;})()")
    cdp.pause(700)
    check("F2 图片生成输入区仍是原样（模式 + 画质档 + 编辑器）",
          cdp.js("!!document.querySelector('[data-mode=\"t2i\"]') && "
                 "!!document.querySelector('[data-grade]') && !!document.querySelector('#promptInput')"))

    check("F3 管理台 5 个页面都在（含设定图模板）",
          cdp.js("(()=>{UI.route='admin';render();return document.querySelectorAll('.admin-nav [data-page]').length===5;})()"))

    check("F4 设定图模板页还能打开且通用块可编辑",
          cdp.js("""(()=>{UI.route='admin';UI.adminPage='sheet';render();
              return !!document.querySelector('.sheet-block, .style-row, .lcard');})()"""))

    errors = cdp.errors()
    check("F5 全程没有未捕获 JS 异常", not errors, str(errors)[:200])
    check("F6 页面没有配置红条", cdp.js("!document.querySelector('.configerr')"))


# ---------------------------------------------------------------- G. 18 组合真实出图（慢）
def section_generate_all() -> None:
    print("\n===== G 18 个组合各出一张（慢，约 10 分钟） =====", flush=True)
    ref = upload_ref()
    ok = 0
    for item in combos():
        cid = item["id"]
        refs = [ref] if item["ref"] != "none" else []
        got = submit_sheet(item["layout"], item["ink"], item["ref"], "默认", refs)
        if not got.get("ok"):
            check(f"G {cid}", False, got.get("reason", ""))
            continue
        run = wait_run(got["data"]["job_id"])
        # run.size 是回图元信息里的紧凑写法（1536×864），这里统一去掉空格再比，
        # 否则 "1536 × 864" in "1536×864" 永远不成立（曾经的假失败）
        size = run_size(run)
        want = LAYOUT_SIZE[item["layout"]]
        hit = run.get("state") == "done" and f"{want[0]}×{want[1]}" in size.replace(" ", "")
        ok += 1 if hit else 0
        check(f"G {cid} 出图且尺寸正确", hit, f"{run.get('state')} {size}")
    check(f"G 18 个组合全部出图成功（{ok}/18）", ok == 18)


# ---------------------------------------------------------------- 收尾
def cleanup() -> None:
    for cid in CREATED_GRADE_COMBOS:
        try:
            api(f"/api/sheet/combos/{cid}/grades", "PUT", {"grades": []})
        except Exception:  # noqa: BLE001
            pass
    for ref in UPLOADED:
        try:
            api(f"/api/uploads/{ref}", "DELETE")
        except Exception:  # noqa: BLE001
            pass
    try:
        api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps",
                                          "capId": "sheet.character"}})
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    args = {a.lower() for a in sys.argv[1:]}
    order = ["a", "b", "c", "d", "e", "f"]
    only = [s for s in order if s in args]
    run_all = not only
    proc = None
    cdp = None
    try:
        if run_all or "a" in only:
            section_api()
        if run_all or "b" in only:
            section_grades()
        if run_all or "c" in only:
            section_generate()
        need_browser = run_all or any(s in only for s in ("d", "e", "f"))
        if need_browser:
            proc, cdp = start_browser()
            cdp.pause(900)
        if run_all or "d" in only:
            section_studio(cdp)
        if run_all or "e" in only:
            section_admin(cdp)
        if run_all or "f" in only:
            section_regression(cdp)
        if "gen18" in args:
            section_generate_all()
    except Exception as exc:  # noqa: BLE001
        check("执行过程中没有抛异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        if cdp:
            cdp.close()
        if proc:
            proc.terminate()
        cleanup()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n================ 结果 ================", flush=True)
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"FAIL  {name}  {detail}", flush=True)
    print(f"通过 {passed}/{total}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
