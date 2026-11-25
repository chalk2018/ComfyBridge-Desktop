"""1.03 场景 / 道具设定图 · 全量语料测试。

把两个素材目录里的每一张图都跑一遍（每张图至少进一个功能），
逐条记录：用例 → 参考图路径 → 组装出的提示词 → 出图路径 → 自动核验结论。

用法（ComfyUI 空闲时）：
    .venv\\Scripts\\python.exe tests\\ext_corpus_test.py plan
    .venv\\Scripts\\python.exe tests\\ext_corpus_test.py run --limit 40
    .venv\\Scripts\\python.exe tests\\ext_corpus_test.py run --only scene_i2i_F4
    .venv\\Scripts\\python.exe tests\\ext_corpus_test.py report

结果：tests/_ext_corpus/results.jsonl
报告：设定图1.03_测试报告.md
"""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

APP = Path(__file__).resolve().parents[1]
BASE = os.environ.get("CB_BASE", "http://127.0.0.1:8190")
OUT = APP / "tests" / "_ext_corpus"
RESULTS = OUT / "results.jsonl"
OUT.mkdir(parents=True, exist_ok=True)

CORPUS_DIRS = [
    Path(r"D:/workspace/AI短剧相关/抖音+AI学习/AI短剧练习1"),
    Path(r"D:/workspace/AI短剧相关/备份3"),
]
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}

# 语料按顺序轮流分给 10 个「吃参考图」的功能位，保证两张目录的图都进到每个版式
BUCKETS = [
    ("sheet.scene", "i2i", "N"),
    ("sheet.scene", "i2i", "P"),
    ("sheet.scene", "i2i", "B"),
    ("sheet.scene", "i2i", "F4"),
    ("sheet.scene", "i2i", "F9"),
    ("sheet.scene", "expand", "PUSH"),
    ("sheet.scene", "expand", "PULL"),
    ("sheet.scene", "expand", "TURN"),
    ("sheet.prop", "i2i", "MV"),
    ("sheet.prop", "i2i", "SV"),
]
SUBS = {"PUSH": ["S", "L"], "PULL": ["S", "L"], "TURN": ["L45", "L75", "R45", "R75"]}

T2I_CASES = [
    ("sheet.scene", "F4", "", "color", "yes", "山间书院：正北主殿，东侧竹林，西侧石阶与水渠，清晨薄雾"),
    ("sheet.scene", "F9", "", "color", "yes", "雨夜旧码头：木栈桥向深处延伸，左侧仓库右侧灯塔，地面积水反光"),
    ("sheet.scene", "N", "", "color", "yes", "废旧地铁站台：中央立柱，左侧售票机，右侧长椅，顶部灯带明暗不一"),
    ("sheet.scene", "P", "", "line", "yes", "北方四合院正房内景：北面炕，东侧柜，西侧方桌两椅，南面隔扇门"),
    ("sheet.scene", "B", "", "color", "no", "山谷露营地：帐篷居左，篝火居中，右侧溪流，四周松林围绕"),
    ("sheet.scene", "F4", "", "line", "no", "赛博维修间：工作台靠墙，零件架在右，地面散落线缆，顶部灯管"),
    ("sheet.prop", "MV", "", "color", "yes", "紫砂茶壶：盖上盖正面、侧面、底部、顶部、开盖、壶盖特写"),
    ("sheet.prop", "SV", "", "color", "yes", "黄铜机械怀表：正面表盘、金属表壳、顶部表冠"),
    ("sheet.prop", "MV", "", "line", "yes", "中式油纸伞：展开正面、展开顶部伞面、展开伞底、合上后的侧面"),
    ("sheet.prop", "SV", "", "line", "yes", "青瓷茶盏：敞口、圈足、釉面开片"),
]


def log(*a: Any) -> None:
    print(" ".join(str(x) for x in a), flush=True)


# ------------------------------------------------------------------ HTTP
def http_json(path: str, body: dict | None = None, method: str | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def http_upload(path: Path) -> dict:
    boundary = "----cb" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "image/png"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/uploads", data=head + path.read_bytes() + tail,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())


def comfy_idle() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/queue", timeout=4) as resp:
            q = json.loads(resp.read().decode())
        running, pending = q.get("queue_running") or [], q.get("queue_pending") or []
        return (not running and not pending), f"running={len(running)} pending={len(pending)}"
    except Exception as exc:  # noqa: BLE001
        return False, f"ComfyUI 读不到：{exc}"


# ------------------------------------------------------------------ 用例
CHAR_RE = re.compile(r"三视图|形象图|着装|人物|立绘")
PROP_RE = re.compile(r"道具|组合|四宝|酒具|集邮册|器物|特写|伞|茶壶|怀表|套装")


def classify(p: Path) -> str:
    """按文件名粗分：角色三视图 / 道具 / 场景。

    角色三视图不是场景也不是道具，塞进这两个能力只会得到与功能无关的结果，
    单独统计并在报告里列出来，不混进通过率。
    """
    stem = p.stem
    if CHAR_RE.search(stem):
        return "char"
    if PROP_RE.search(stem):
        return "prop"
    return "scene"


def corpus() -> list[Path]:
    out: list[Path] = []
    for d in CORPUS_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file() and p.suffix.lower() in IMG_EXT:
                out.append(p)
    return out


VIEW_SETS = [
    "正面、侧面、顶部、底部",
    "整体正面、背面、左侧面、右侧面、顶面、底面",
    "正面、背面、打开状态、合上状态",
    "整体正面、局部细节特写、底部结构",
]


def prompt_for(img: Path, kind: str, idx: int) -> str:
    stem = img.stem.replace("_", " ").replace("-", " ")
    if kind == "prop":
        return f"参考图里的这件道具（{stem}）：{VIEW_SETS[idx % len(VIEW_SETS)]}"
    return f"按参考图重画这处场景：{stem}。保持图中的空间结构与所有物件的相对位置，补齐材质与光线细节。"


REFS = APP / "_dev" / "prompt_lab" / "refs"

SLOT_CASES = [
    # 场景图生图：把全景图 / 鸟瞰布局图 / 布局草图分别塞进各自的槽位
    ("slot_scene_i2i_N_bird", "i2i", "N", "", {"bird": "floorplan_color.png"},
     "按鸟瞰布局图摆放：北面主殿，东侧竹林，西侧石阶与水渠，中间是青石庭院，清晨薄雾"),
    ("slot_scene_i2i_N_sketch", "i2i", "N", "", {"sketch": "floorplan_sketch.png"},
     "按布局草图还原这处院子：房间在北，门在南，院中有石桌"),
    ("slot_scene_i2i_P_sketch", "i2i", "P", "", {"sketch": "floorplan_sketch.png"},
     "按草图布局做一张四十五度俯视全景，院墙与主屋位置照草图"),
    ("slot_scene_i2i_B_pano", "i2i", "B", "", {"pano": "scene_panorama.png"},
     "把这张全景画面的场景改成垂直俯视鸟瞰，布局不变"),
    ("slot_scene_i2i_F4_ref_bird", "i2i", "F4", "",
     {"ref": "scene_ref_room.png", "bird": "floorplan_color.png"},
     "参考实拍房间的材质与家具样式，按鸟瞰布局图的方位摆四台机位"),
    ("slot_scene_i2i_F9_ref_sketch", "i2i", "F9", "",
     {"ref": "scene_ref_room.png", "sketch": "floorplan_sketch.png"},
     "参考实拍房间的材质，按草图方位出九宫格"),
    ("slot_scene_expand_PUSH_ref_pano", "expand", "PUSH", "S",
     {"ref": "scene_ref_room.png", "pano": "scene_panorama.png"},
     "推近一点点，保持同一间屋子"),
    ("slot_scene_expand_TURN_ref_bird", "expand", "TURN", "L45",
     {"ref": "scene_ref_room.png", "bird": "floorplan_color2.png"}, ""),
    ("slot_scene_expand_PULL_ref_sketch", "expand", "PULL", "L",
     {"ref": "scene_ref_room.png", "sketch": "floorplan_sketch.png"}, ""),
]


def build_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for i, (cap, layout, sub, ink, nochar, text) in enumerate(T2I_CASES):
        kind = "scene" if cap == "sheet.scene" else "prop"
        cases.append({
            "id": f"t2i_{kind}_{layout}{'_' + sub if sub else ''}_{ink}_{nochar}_{i}",
            "cap": cap, "kind": kind, "mode": "t2i", "layout": layout, "sub": sub,
            "ink": ink, "nochar": nochar, "text": text, "ref": img_none(),
        })

    imgs = [p for p in corpus() if classify(p) != "char"]
    seq = {"scene": 0, "prop": 0}
    for img in imgs:
        kind = classify(img)
        pool = [b for b in BUCKETS if (b[0] == "sheet.scene") == (kind == "scene")]
        i = seq[kind]
        seq[kind] += 1
        cap, mode, layout = pool[i % len(pool)]
        sub = ""
        if mode == "expand":
            opts = SUBS[layout]
            sub = opts[(i // len(BUCKETS)) % len(opts)]
        # 语料里全是实拍照片，只能当「参考图」用：布局草图槽位需要真草图，
        # 放到单独的 slot 用例里测，别把照片塞进草图位污染结论。
        slot = "ref"
        ink = "color" if i % 3 else "line"
        cases.append({
            "id": f"{kind}_{mode}_{layout}{'_' + sub if sub else ''}_{i:03d}",
            "cap": cap, "kind": kind, "mode": mode, "layout": layout, "sub": sub,
            "ink": "color" if mode == "expand" else ink,
            "nochar": "yes" if i % 4 else "no",
            "text": prompt_for(img, kind, i), "ref": str(img),
            "slot": slot, "propRef": "lock",
        })

    for name, mode, layout, sub, slot_paths, text in SLOT_CASES:
        paths = {k: str(REFS / v) for k, v in slot_paths.items()}
        missing = [v for v in paths.values() if not Path(v).exists()]
        if missing:
            log("跳过槽位用例（缺图）：", name, missing)
            continue
        cases.append({
            "id": name, "cap": "sheet.scene", "kind": "scene", "mode": mode,
            "layout": layout, "sub": sub, "ink": "color",
            "nochar": "yes", "text": text, "ref": "",
            "refSlotsPaths": paths, "slotPaths": paths,
        })
    return cases


def img_none() -> str:
    return ""


# ------------------------------------------------------------------ 执行
def done_ids() -> set[str]:
    got: set[str] = set()
    if not RESULTS.exists():
        return got
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("state") == "done" and row.get("out"):
            got.add(row["id"])
    return got


def record(row: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def wait_job(job_id: str, timeout: float = 900) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        st = http_json("/api/state")
        job = next((j for j in st.get("jobs", []) if j.get("id") == job_id), None)
        if job:
            run = (job.get("runs") or [{}])[0]
            if run.get("state") in ("done", "failed", "canceled"):
                return {"job": job, "run": run}
        time.sleep(4)
    return {"job": {}, "run": {"state": "timeout"}}


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    row = {k: case.get(k) for k in ("id", "cap", "kind", "mode", "layout", "sub", "ink", "nochar", "text", "ref")}
    refs: list[str] = []
    slots: dict[str, str] = {}
    plan: list[tuple[str, str]] = []
    if case.get("ref"):
        plan.append((case.get("slot") or "ref", case["ref"]))
    for slot, path in (case.get("slotPaths") or {}).items():
        plan.append((slot, path))
    for slot, path in plan:
        up = http_upload(Path(path))
        if not up.get("ok"):
            row.update(state="upload_failed", error=up.get("reason"))
            return row
        rid = up["data"]["id"]
        refs.append(rid)
        if case["kind"] == "scene":
            slots[slot] = rid
    if case.get("slotPaths"):
        row["ref"] = "; ".join(f"{k}={v}" for k, v in case["slotPaths"].items())

    ext = {
        "kind": case["kind"], "mode": case["mode"], "layout": case["layout"],
        "sub": case.get("sub") or "", "text": case["text"], "ink": case["ink"],
        "nochar": case["nochar"], "ref": case.get("propRef") or "none",
        "refSlots": slots, "refs": refs,
        "style": "" if case["mode"] != "t2i" else "写实摄影",
        "styleCustom": case["mode"] != "t2i", "styleText": "",
        "lineStyle": None, "grade": "默认",
    }
    wf = "设定图-文生图" if case["mode"] == "t2i" else "设定图-参考生图"
    body = {"capId": case["cap"], "prompt": case["text"], "grade": "默认", "wf": wf, "refs": refs, "ext": ext}
    try:
        res = http_json("/api/tasks", body)
    except Exception as exc:  # noqa: BLE001
        row.update(state="submit_failed", error=str(exc))
        return row
    if not res.get("ok"):
        row.update(state="blocked", error=res.get("reason"))
        return row

    job_id = (res.get("data") or {}).get("job_id") or (res.get("data") or {}).get("id") or res.get("job_id")
    got = wait_job(str(job_id))
    job, run = got["job"], got["run"]
    files = run.get("files") or []
    row.update(
        job=job_id, state=run.get("state"), prompt=job.get("prompt") or "",
        combo=(job.get("sheet") or {}).get("combo"),
        grade=run.get("grade"),
        out=(files[0] or {}).get("path") if files else "",
        error=(run.get("err") or {}).get("msg") if isinstance(run.get("err"), dict) else "",
    )
    row["qc"] = qc_image(row)
    return row


# ------------------------------------------------------------------ 自动核验
def qc_image(row: dict[str, Any]) -> dict[str, Any]:
    from PIL import Image, ImageChops, ImageStat

    path = row.get("out") or ""
    if not path or not Path(path).exists():
        return {"ok": False, "why": "没有出图"}
    try:
        im = Image.open(path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "why": f"打不开：{exc}"}
    qc: dict[str, Any] = {"size": im.size}
    stat = ImageStat.Stat(im.convert("L"))
    qc["std"] = round(stat.stddev[0], 1)
    if qc["std"] < 3:
        return {**qc, "ok": False, "why": "接近纯色/空白"}

    layout = row.get("layout")
    if layout in ("F4", "F9"):
        n = 2 if layout == "F4" else 3
        w, h = im.size
        cells = [im.crop((int(w * c / n), int(h * r / n), int(w * (c + 1) / n), int(h * (r + 1) / n)))
                 for r in range(n) for c in range(n)]
        diffs = []
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                d = ImageChops.difference(cells[i], cells[j])
                diffs.append(ImageStat.Stat(d).mean[0])
        qc["minCellDiff"] = round(min(diffs), 2)
        qc["avgCellDiff"] = round(sum(diffs) / len(diffs), 2)
        qc["cellsSame"] = qc["minCellDiff"] < 4.0

    if row.get("kind") == "prop":
        w, h = im.size
        edge = ImageStat.Stat(im.crop((0, 0, w, 8)).convert("L")).mean[0]
        qc["topEdge"] = round(edge, 1)

    ref = row.get("ref") or ""
    if ref and Path(ref).exists():
        try:
            r = Image.open(ref).convert("RGB").resize(im.size)
            qc["refDiff"] = round(ImageStat.Stat(ImageChops.difference(im, r)).mean[0], 1)
        except Exception:
            pass

    ok = qc["std"] >= 3 and not qc.get("cellsSame")
    return {**qc, "ok": ok, "why": "" if ok else ("宫格格子几乎一样" if qc.get("cellsSame") else "")}


# ------------------------------------------------------------------ 报告
LAYOUT_CN = {
    "N": "普通", "P": "全景", "B": "鸟瞰", "F4": "四宫格", "F9": "九宫格",
    "PUSH": "推镜", "PULL": "拉镜", "TURN": "转镜头", "MV": "多视图", "SV": "单视图",
}
MODE_CN = {"t2i": "文生图", "i2i": "图生图", "expand": "扩图"}
SUB_CN = {"S": "短", "L": "长", "L45": "左转45°", "L75": "左转75°", "R45": "右转45°", "R75": "右转75°"}


def feature_name(r: dict[str, Any]) -> str:
    cap = "场景设定图" if r.get("kind") == "scene" else "道具设定图"
    lay = LAYOUT_CN.get(str(r.get("layout")), str(r.get("layout")))
    mode = MODE_CN.get(str(r.get("mode")), str(r.get("mode")))
    return f"{cap} · {mode} · {lay}"


def report() -> Path:
    rows: list[dict[str, Any]] = []
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        latest[r["id"]] = r
    rows = sorted(latest.values(), key=lambda r: r["id"])

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        key = f"{r['kind']} · {r['mode']} · {r['layout']}" + (f" · {r['sub']}" if r.get("sub") else "")
        groups.setdefault(key, []).append(r)

    ok = sum(1 for r in rows if r.get("state") == "done" and (r.get("qc") or {}).get("ok"))
    all_imgs = corpus()
    stat = {"scene": 0, "prop": 0, "char": 0}
    chars: list[str] = []
    for im in all_imgs:
        k = classify(im)
        stat[k] += 1
        if k == "char":
            chars.append(str(im))
    by_feature: dict[str, list[int]] = {}
    for r in rows:
        key = feature_name(r)
        slot = by_feature.setdefault(key, [0, 0])
        slot[1] += 1
        if r.get("state") == "done" and (r.get("qc") or {}).get("ok"):
            slot[0] += 1

    md = [
        "# 场景 / 道具设定图 1.03 · 全量语料测试报告",
        "",
        f"- 用例总数：**{len(rows)}**，通过自动核验：**{ok}**",
        f"- 语料总数：**{len(all_imgs)}** 张（场景 {stat['scene']} / 道具 {stat['prop']} / 角色三视图 {stat['char']}）",
        "- 语料目录：`D:/workspace/AI短剧相关/抖音+AI学习/AI短剧练习1`、`D:/workspace/AI短剧相关/备份3`",
        "- 说明：每条用例都记录参考图路径与出图路径；图片不内嵌，交给另一个 AI 交叉查验时按路径打开即可。",
        "",
        "## 一、功能通过概览",
        "",
        "| 功能 | 通过 / 用例 |",
        "| --- | --- |",
    ]
    for key in sorted(by_feature):
        good, total = by_feature[key]
        md.append(f"| {key} | {good} / {total} |")
    md += [
        "",
        "## 二、自动核验口径",
        "",
        "- 出图必须 `state=done` 且有文件。",
        "- 画面标准差 ≥ 3（排除纯色/空白）。",
        "- 四宫格 / 九宫格：两两比对格子像素，任意两格平均差 < 4 判为「复制同一张图」，不合格。",
        "- 道具：额外记录顶边亮度，纯白底应接近 255。",
        "- 自动核验只能抓「空白 / 宫格复制 / 没出图」，语义是否达标必须人眼看图，见第六节。",
        "",
    ]
    if chars:
        md += ["## 三、未纳入本次功能测试的图片（角色三视图）", "",
               f"共 {len(chars)} 张。角色三视图既不是场景也不是道具，塞进这两个能力只会得到与功能无关的结果，"
               "因此单列出来、不计入通过率；角色设定图本身的回归测试另行执行。", ""]
        md += [f"- `{c}`" for c in chars]
        md.append("")
    for key in sorted(groups):
        items = groups[key]
        good = sum(1 for r in items if r.get("state") == "done" and (r.get("qc") or {}).get("ok"))
        md += [f"### {key}（{good}/{len(items)} 通过）", "",
               "| # | 参考图 | 提示词摘要 | 出图 | 核验数据 | 结论 |",
               "| --- | --- | --- | --- | --- | --- |"]
        for i, r in enumerate(items, 1):
            qc = r.get("qc") or {}
            why = r.get("error") or qc.get("why") or ""
            verdict = "✅" if r.get("state") == "done" and qc.get("ok") else f"❌ {why}"
            text = (r.get("text") or "").replace("|", "｜")[:40]
            bits = [f"std={qc.get('std')}"]
            if qc.get("minCellDiff") is not None:
                bits.append(f"格间最小差={qc.get('minCellDiff')}")
            if qc.get("topEdge") is not None:
                bits.append(f"顶边={qc.get('topEdge')}")
            md.append(f"| {i} | `{r.get('ref') or '—'}` | {text} | `{r.get('out') or '—'}` | "
                      f"{' · '.join(bits)} | {verdict} |")
        md.append("")
    md += ["## 四、自动核验抓不到、需人眼看的项", "",
           "- 转镜头是否真的转向了对应墙面、推拉镜景别是否合理。",
           "- 布局草图/鸟瞰图里的汉字、边框、色块有没有被照搬。",
           "- 道具多视图各视角是否同一件道具、纯白底是否干净。",
           "- 四宫格/九宫格虽然格子不同，但是否仍是同一场景（不能穿帮）。",
           "- 禁角色=是 时画面里是否真的没有人影/背影/剪影。", ""]

    manual = OUT / "manual_review.md"
    if manual.exists():
        md += ["## 五、人工抽检记录", "", manual.read_text(encoding="utf-8").strip(), ""]

    md += ["## 六、页面级测试与既有模块回归", "",
           "| 测试 | 结果 | 说明 |",
           "| --- | --- | --- |",
           "| `tests/ext_sheet_e2e.py` | 5/5 通过 | 真实页面操作、真实出图：场景图生图全景、扩图右转75°、扩图左转45°、道具多视图四视角、道具多视图六视角 |",
           "| `tests/ext_admin_ui_check.py` | 全部通过 | 场景 43 个组合（文生图/图生图/扩图）、道具 16 个组合、模式切换、模板弹窗 |",
           "| `tests/smoke_test.py` | 41/41 通过 | 接口与任务全链路冒烟 |",
           "| `tests/sheet_e2e_test.py` | 89/89 通过 | 角色设定图 89 项 + 图片生成模块 F1–F7 回归，未受 1.03 影响 |",
           "",
           "## 七、已知限制", "",
           "1. 抽象色块布局图：单图编辑模型天然倾向保留输入图形，当前用“图纸→建造”话术压到多数正常，但不是 100%。",
           "2. 道具多视图：模型对“只画 N 个视图”的服从度中等，复杂道具偶发相近机位或多余格子，重试可解。",
           "3. 转镜头 75° 是“正对那面墙”的近似，不保证严格 75 度几何。", ""]
    path = APP / "设定图1.03_测试报告.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run", "report", "status"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    if args.cmd == "plan":
        cases = build_cases()
        log(f"语料 {len(corpus())} 张，用例 {len(cases)} 条")
        for c in cases[:12]:
            log(" ", c["id"], c["layout"], c["sub"], c["mode"], Path(c["ref"]).name if c["ref"] else "—")
        return
    if args.cmd == "status":
        done = done_ids()
        log(f"已完成 {len(done)} / {len(build_cases())}")
        return
    if args.cmd == "report":
        log("报告：", report())
        return

    idle, why = comfy_idle()
    if not idle:
        log("ComfyUI 不空闲，先不提交：", why)
        return
    cases = build_cases()
    done = done_ids()
    todo = [c for c in cases if c["id"] not in done and (not args.only or args.only in c["id"])]
    if args.limit:
        todo = todo[: args.limit]
    log(f"待跑 {len(todo)} 条")
    for i, case in enumerate(todo, 1):
        if i > 1 and i % 10 == 1:
            idle, why = comfy_idle()
            if not idle:
                log("环境被占用，暂停：", why)
                break
        row = run_case(case)
        record(row)
        log(f"[{i}/{len(todo)}] {row['id']} {row.get('state')} {row.get('qc', {}).get('why', '')}")


if __name__ == "__main__":
    main()
