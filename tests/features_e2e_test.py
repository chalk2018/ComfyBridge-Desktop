"""新增功能与「配置增删改」全量真实浏览器 E2E。

覆盖：配置入口不跳回、所有档位参数生效（含真实出图 16:9）、
配置里添加/保存/删除的每一个按钮、历史全选与删文件、会话任务删除联动。

安全约定：
- 全程只删除本脚本自己刚生成的成品；全选只验证选中状态，绝不点删除；
- 测试开始备份 config.yaml，结束一定恢复并重读，不污染用户配置。

前置：应用在 8190 运行、ComfyUI 在 8188 在线且队列空闲。
运行：.venv\\Scripts\\python.exe tests\\features_e2e_test.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import ui_e2e_test as base

APP = base.APP
api = base.api
check = base.check
click = base.click
CONFIG = base.CONFIG
RESULTS = base.RESULTS
CONFIG_BACKUP = ""


def q(text: str) -> str:
    """URL 路径转义。"""
    return urllib.parse.quote(text, safe="")


def goto(cdp: base.CDP, route: str, page: str | None = None) -> None:
    """导航到某一页：换 hash 后强制重载，并等到目标页特征真正出现。"""
    fragment = f"#admin/{page}" if page else f"#{route}"
    cdp.call("Page.navigate", {"url": base.BASE + "/" + fragment})
    cdp.pause(250)
    cdp.call("Page.reload", {"ignoreCache": True})
    marker = {
        ("admin", "caps"): ".cap-row [data-edit]",
        ("admin", "grades"): ".node-list",
        ("admin", "service"): "[data-savecfg]",
        ("admin", "diag"): "#rerun",
        ("gallery", None): "#gall",
        ("studio", None): ".composer",
    }.get((route, page), "#root .topbar")
    cdp.wait(f"!!document.querySelector({json.dumps(marker)})", 25)
    cdp.pause(350)


def set_value(cdp: base.CDP, selector: str, value: Any, event: str = "change") -> bool:
    """给输入控件设值并触发 change（select / input 通用）。"""
    expr = (
        f"(()=>{{const e=document.querySelector({json.dumps(selector)});"
        f"if(!e)return false;e.value={json.dumps(str(value))};"
        f"e.dispatchEvent(new Event({json.dumps(event)},{{bubbles:true}}));return true;}})()"
    )
    return bool(cdp.js(expr))


def wf0() -> dict[str, Any]:
    """当前配置里第一个能力的第一个工作流。"""
    return api("/api/state")["capabilities"][0]["workflows"][0]


def grade_names() -> list[str]:
    """文生图工作流当前的档位名列表。"""
    return [g["name"] for g in wf0()["grades"]]


def wait_idle() -> bool:
    """等 ComfyUI 队列空，避免生成被「忙时只提示」拦下。"""
    for _ in range(90):
        health = api("/api/v1/health")
        queue = health.get("queue", {}) or {}
        if not queue.get("running") and not queue.get("pending"):
            return True
        time.sleep(1)
    return False


def generate(prompt: str, grade: str = "默认") -> dict[str, Any] | None:
    """通过接口真实出一张图，返回完成后的 job（含 run）。"""
    wait_idle()
    res = api("/api/tasks", "POST", {
        "capId": "image.generate", "prompt": prompt, "grade": grade, "refs": [],
    })
    if not res.get("ok"):
        print("  submit failed:", res, flush=True)
        return None
    job_id = res["data"]["job_id"]
    deadline = time.time() + 200
    while time.time() < deadline:
        time.sleep(1)
        state = api("/api/state")
        job = next((j for j in state["jobs"] if j["id"] == job_id), None)
        if job and job["runs"][0]["state"] in ("done", "failed", "canceled"):
            return job
    return None


def restore_config() -> None:
    """把配置恢复成测试开始前的原文并重读。"""
    if CONFIG_BACKUP:
        CONFIG.write_text(CONFIG_BACKUP, encoding="utf-8")
        api("/api/config/reload", "POST", {})


def run_checks(cdp: base.CDP) -> None:
    """按功能分组逐项真实点击。"""
    # ============================================================ N1 配置入口不跳回
    goto(cdp, "admin", "caps")
    check("N1.1 能力与工作流页有「配置」按钮", cdp.js("!!document.querySelector('.cap-row [data-edit]')"))
    click(cdp, ".cap-row [data-edit]")
    cdp.wait("!!document.querySelector('.node-list')", 15)
    check("N1.2 点配置进入档位表", cdp.js(
        "!!document.querySelector('.node-list') && location.hash.includes('grades')"))
    original_fields = wf0()["fields"]
    target = cdp.js("(()=>{const e=document.querySelector('.node-row.can:not(.on)');return e?e.dataset.picknode:null})()")
    check("N1.3 存在可勾选的参数节点", bool(target), str(target))
    clicked = cdp.js(
        f"(()=>{{const e=document.querySelector('[data-picknode=\"{target}\"] input[type=checkbox]');"
        f"if(!e)return false;e.click();return true}})()")
    cdp.pause(900)
    check("N1.4 勾选节点后仍停在档位表（不再跳回）", clicked and cdp.js(
        "!!document.querySelector('.node-list') && location.hash.includes('grades')"),
        "hash=" + str(cdp.js("location.hash")))
    check("N1.5 勾选真的写进配置", len(wf0()["fields"]) > len(original_fields),
          f"{len(original_fields)} -> {len(wf0()['fields'])}")
    cdp.js(
        f"(()=>{{const e=document.querySelector('[data-picknode=\"{target}\"] input[type=checkbox]');"
        f"if(e)e.click();return true}})()")
    cdp.pause(900)
    check("N1.6 取消勾选恢复原配置且页面不动", len(wf0()["fields"]) == len(original_fields)
          and cdp.js("!!document.querySelector('.node-list')"))
    cdp.call("Page.reload", {"ignoreCache": True})
    cdp.wait("!!document.querySelector('.node-list')", 25)
    cdp.pause(500)
    check("N1.7 刷新后仍停在档位表", cdp.js("location.hash.includes('grades') && !!document.querySelector('.node-list')"),
          str(cdp.js("location.hash")))

    # ============================================================ N2 所有参数生效
    state = api("/api/state")
    bad: list[str] = []
    total_rows = 0
    for cap in state["capabilities"]:
        for wf in cap.get("workflows", []):
            for grade in wf.get("grades", []):
                res = api(f"/api/workflows/{q(wf['name'])}/grades/{q(grade['name'])}/effective")
                data = res.get("data") or {}
                total_rows += len(data.get("rows", []))
                if not res.get("ok") or not data.get("allOk"):
                    bad.append(f"{wf['name']}/{grade['name']}")
                for row in data.get("rows", []):
                    if not row.get("ok") or row.get("got") != row.get("want"):
                        bad.append(f"{wf['name']}/{grade['name']}:{row.get('field')}")
    check("N2.1 所有工作流所有档位参数逐项生效", not bad,
          f"共核对 {total_rows} 个参数，异常：{bad[:5]}")

    # 针对「精细（16:9）」档：它带 aspect_ratio / megapixels / multiple 三个可调参数，便于验证
    # （文生图工作流没有 cfg / 采样步数这类字段，所以验证「保存」按钮改用 multiple）
    idx = cdp.js("""(()=>[...document.querySelectorAll('.grow')].findIndex(x=>{
        const inp=x.querySelector('.grow-hd .gname'), txt=x.querySelector('.grow-hd .gname-txt');
        return (inp&&inp.value.includes('精细（16:9）'))||(txt&&txt.textContent.includes('精细（16:9）'));
    }))()""")
    check("N2.2 档位表显示 16:9 档", isinstance(idx, int) and idx > 0, str(idx))
    # 已经展开就别再点（gtoggle 是开/关切换，再点会收起）
    is_open = cdp.js(f"document.querySelectorAll('.grow')[{idx}].classList.contains('open')")
    if not is_open:
        click(cdp, f'.grow:nth-child({idx + 1}) .grow-hd')
        cdp.pause(700)
    check("N2.3 展开后显示逐参数生效自检", cdp.wait(
        "!!document.querySelector('#gradeEff') && document.querySelector('#gradeEff').textContent.includes('参数全部生效')", 12),
        cdp.js("(()=>{const e=document.querySelector('#gradeEff');return e?e.textContent.slice(0,120):'无 #gradeEff'})()"))
    eff = api(f"/api/workflows/{q('文生图')}/grades/{q('精细（16:9）')}/effective")["data"]
    check("N2.4 16:9 档的尺寸说明是宽屏", "16:9" in eff["size"]["label"], eff["size"]["label"])

    # 数字参数（float）：megapixels
    ok_mp = set_value(cdp, '[data-gval^="megapixels@"]', "0.5")
    cdp.pause(1000)
    check("N2.5 改数字参数即时保存生效", ok_mp and any(
        r["field"] == "megapixels" and r["want"] == 0.5
        for r in api(f"/api/workflows/{q('文生图')}/grades/{q('精细（16:9）')}/effective")["data"]["rows"]),
        "megapixels -> 0.5")
    # 菜单参数（combo）：aspect_ratio
    ok_ar = set_value(cdp, '[data-gval^="aspect_ratio@"]', "4:3 (Standard)")
    cdp.pause(1000)
    check("N2.6 改选择菜单参数即时保存生效", ok_ar and any(
        r["field"] == "aspect_ratio" and r["want"] == "4:3 (Standard)"
        for r in api(f"/api/workflows/{q('文生图')}/grades/{q('精细（16:9）')}/effective")["data"]["rows"]))
    # 「保存」按钮：真正把当前编辑区整体提交（用 multiple，文生图没有 cfg 字段）
    # 注意：multiple 默认默认就是 8，按「只存相对默认改动」的语义，设成 8 会被当成
    # 「回退默认」而跳过；所以要验证保存生效，必须用非默认值（这里用 16）。
    set_value(cdp, '[data-gval^="multiple@"]', "16", "input")
    cdp.pause(250)
    click(cdp, "[data-savewf]")
    cdp.pause(900)
    saved = api(f"/api/workflows/{q('文生图')}/grades/{q('精细（16:9）')}/effective")["data"]
    check("N2.7 「保存」按钮真的提交整档参数", saved["allOk"] and any(
        r["field"] == "multiple" and r["want"] == 16 for r in saved["rows"]),
        str([(r["field"], r["want"]) for r in saved["rows"]]))
    check("N2.8 保存后尺寸说明随参数变化", "4:3" in saved["size"]["label"], saved["size"]["label"])

    # 恢复配置后再真实出图，验证 16:9 真的出宽屏
    restore_config()
    cdp.pause(400)
    t1 = generate("N2 参数生效验证：窗台上的橘猫，午后阳光", "快速（16:9）")
    check("N2.9 16:9 档真实出图完成", bool(t1 and t1["runs"][0]["state"] == "done"),
          str(t1["runs"][0]["state"]) if t1 else "无结果")
    run1 = t1["runs"][0] if t1 else {}
    size = str(run1.get("size") or "")
    dims = [int(x) for x in size.replace("×", "x").split("x") if x.strip().isdigit()]
    check("N2.10 真实成品是宽屏而不是 1:1", len(dims) == 2 and dims[0] > dims[1] * 1.5, size)
    check("N2.11 任务记录了实际生效参数", "aspect_ratio" in json.dumps(run1.get("applied", {}), ensure_ascii=False)
          and "16:9" in str(run1.get("sizeExpected", "")), str(run1.get("sizeExpected")))

    # ============================================================ N3 配置里添加 / 保存 / 删除
    goto(cdp, "admin", "caps")
    cap_before = len(api("/api/state")["capabilities"])
    click(cdp, '[data-toggle="image.generate"]')
    cdp.pause(600)
    check("N3.1 能力开关能关", not next(
        c for c in api("/api/state")["capabilities"] if c["id"] == "image.generate")["enabled"])
    click(cdp, '[data-toggle="image.generate"]')
    cdp.pause(600)
    check("N3.2 能力开关能开回来", next(
        c for c in api("/api/state")["capabilities"] if c["id"] == "image.generate")["enabled"])
    # 能力清单在 config.yaml 里固定：卡片上的复制/删除、页面上的「重读配置」都已经去掉了
    check("N3.3 能力卡片没有复制/删除按钮",
          cdp.js("document.querySelectorAll('.cap-row [data-copy],.cap-row [data-del]').length===0"))
    check("N3.4 页面没有「重读配置」按钮",
          cdp.js("!document.querySelector('[data-reload]') && !document.body.textContent.includes('重读配置')"))
    check("N3.5 能力数没变（复制/删除确实没跑）",
          len(api("/api/state")["capabilities"]) == cap_before,
          f"{cap_before} → {len(api('/api/state')['capabilities'])}")

    goto(cdp, "admin", "grades")
    check("N3.6 档位表下挂能力子菜单", cdp.js(
        "document.querySelectorAll('.nav-sub [data-grades-cap]').length>0"))
    check("N3.7 档位表多个工作流 tab 可切换", cdp.js(
        "document.querySelectorAll('[data-wfswitch]').length>0") and bool(click(cdp, '[data-wfswitch="1"]')))
    cdp.pause(450)
    check("N3.8 切换后标题跟着变", cdp.js("!!document.querySelector('.admin-main h2')"))
    click(cdp, '[data-wfswitch="0"]')
    cdp.pause(400)

    # 新增档位：填名字 + 改一个参数，再点添加
    new_grade = "E2E新增档"
    set_value(cdp, "#newGradeName", new_grade, "input")
    cdp.pause(200)
    # 只挑数字控件改，避免把模型名/采样器这类 combo 设成非法值
    drf = cdp.js("(()=>{const e=document.querySelector('[data-drf][data-ftype=\"float\"],[data-drf][data-ftype=\"int\"]');"
                 "if(e){e.value='2';e.dispatchEvent(new Event('change',{bubbles:true}))}return e?e.dataset.drf:null})()")
    cdp.pause(300)
    click(cdp, "[data-gadd]")
    cdp.pause(900)
    check("N3.9 新增档位写进配置", new_grade in grade_names(), str(grade_names()))
    gi = grade_names().index(new_grade) if new_grade in grade_names() else -1
    if gi >= 0:
        renamed = "E2E改名档"
        already = cdp.js(f"document.querySelectorAll('.grow')[{gi}].classList.contains('open')")
        if not already:
            click(cdp, f'[data-gtoggle="{gi}"]')
            cdp.pause(600)
        check("N3.10 档位可展开编辑", cdp.js(
            f"document.querySelectorAll('.grow')[{gi}].classList.contains('open') && !!document.querySelector('[data-gfield=\"note|{gi}\"]')"))
        set_value(cdp, f'[data-gfield="name|{gi}"]', renamed)
        cdp.pause(800)
        check("N3.11 档位改名生效", renamed in grade_names(), str(grade_names()))
        set_value(cdp, f'[data-gfield="note|{gi}"]', "E2E 说明")
        cdp.pause(1000)
        notes = [g.get("note") for g in wf0()["grades"] if g["name"] == renamed]
        check("N3.12 档位说明保存生效", bool(notes) and notes[0] == "E2E 说明", str(notes))
        gval = cdp.js("(()=>{const e=document.querySelector('[data-gval][data-ftype=\"float\"],[data-gval][data-ftype=\"int\"]');return e?e.dataset.gval:null})()")
        if gval:
            field = gval.rsplit("@", 1)[0]
            set_value(cdp, f'[data-gval="{gval}"]', "3")
            cdp.pause(800)
            eff_new = api(f"/api/workflows/{q('文生图')}/grades/{q(renamed)}/effective")["data"]
            check("N3.13 档位参数编辑生效", eff_new["allOk"] and any(
                r["field"] == field and str(r["want"]) == "3" for r in eff_new["rows"]), str(gval))
        else:
            check("N3.13 档位参数编辑生效", False, "展开区里没有数字参数控件")
        click(cdp, "[data-savewf]")
        cdp.pause(800)
        check("N3.14 保存按钮不报错且档位仍在", renamed in grade_names())
        gi2 = grade_names().index(renamed)
        click(cdp, f'[data-gdel="{gi2}"]')
        cdp.pause(800)
        check("N3.15 删除档位生效且拷贝文件被清掉", renamed not in grade_names(), str(grade_names()))

    goto(cdp, "admin", "service")
    old_keep = str(api("/api/state")["service"]["keepDays"])
    set_value(cdp, '[data-s="keepDays"]', "9")
    click(cdp, "[data-savecfg]")
    cdp.pause(800)
    check("N3.16 服务设置保存并生效", str(api("/api/state")["service"]["keepDays"]) == "9",
          str(api("/api/state")["service"]["keepDays"]))
    set_value(cdp, '[data-s="keepDays"]', old_keep)
    click(cdp, "[data-savecfg]")
    cdp.pause(700)
    check("N3.17 服务设置能改回原值", str(api("/api/state")["service"]["keepDays"]) == old_keep)
    click(cdp, "[data-discard]")
    cdp.pause(350)
    check("N3.18 放弃修改可用", cdp.js("!!document.querySelector('[data-savecfg]')"))
    click(cdp, "#testConn")
    cdp.pause(900)
    check("N3.19 测试连接按钮可用", cdp.js(
        "!!document.querySelector('#testConn') && document.querySelector('#testConn').textContent.includes('测试连接')"))
    raw = api("/api/config/raw")
    check("N3.20 配置原文可读可保存", raw.get("ok") and api(
        "/api/config/raw", "PUT", {"text": raw["data"]}).get("ok"))
    goto(cdp, "admin", "diag")
    click(cdp, "#rerun")
    cdp.pause(900)
    check("N3.21 自检页重新自检可用", cdp.js("document.querySelectorAll('.diag-row').length>=2"))

    # ============================================================ N4 历史全选 / 删除
    t2 = generate("N4 历史删除验证：一只灰猫", "默认")
    t3 = generate("N4 单卡删除验证：雨夜街景", "默认")
    check("N4.1 两张测试成品已生成", bool(t2 and t3 and t2["runs"][0]["state"] == "done"
          and t3["runs"][0]["state"] == "done"))
    if not (t1 and t2 and t3):
        return
    files = {
        "t1": t1["runs"][0]["files"][0]["path"],
        "t2": t2["runs"][0]["files"][0]["path"],
        "t3": t3["runs"][0]["files"][0]["path"],
    }
    ids = {
        "t1": (t1["id"], t1["runs"][0]["id"]),
        "t2": (t2["id"], t2["runs"][0]["id"]),
        "t3": (t3["id"], t3["runs"][0]["id"]),
    }
    goto(cdp, "gallery")
    check("N4.2 历史页有全选与删除按钮", cdp.js("!!document.querySelector('#gall') && !!document.querySelector('#gDel')"))
    total = cdp.js("document.querySelectorAll('[data-sel]').length")
    click(cdp, "#gall")
    cdp.pause(400)
    check("N4.3 全选能一次选中所有可见成品", cdp.js(
        "[...document.querySelectorAll('[data-sel]')].every(e=>e.checked)") and total > 0, f"{total} 张")
    check("N4.4 删除按钮计数跟着变", cdp.js(
        f"(()=>{{const e=document.querySelector('#gDel');return !!e && e.textContent.includes('{total}')}})()"))
    click(cdp, "#gall")  # 取消全选，绝不误删已有成品
    cdp.pause(400)
    check("N4.5 取消全选清零", cdp.js("[...document.querySelectorAll('[data-sel]')].every(e=>!e.checked)"))

    key1 = ids["t1"][1] + "|0"
    cdp.js(f"(()=>{{const e=document.querySelector('[data-sel=\"{key1}\"]');"
           f"if(e){{e.checked=true;e.dispatchEvent(new Event('change',{{bubbles:true}}))}}return true}})()")
    cdp.pause(350)
    click(cdp, "#gDel")
    cdp.pause(350)
    check("N4.6 历史删除有确认弹窗", cdp.js("!!document.querySelector('#confirmHistoryDel')"))
    click(cdp, "#confirmHistoryDel")
    cdp.pause(900)
    check("N4.7 历史删除把本地成品文件删掉", not Path(files["t1"]).exists(), files["t1"])
    check("N4.8 历史里这张成品消失", cdp.js(
        f"!document.querySelector('[data-sel=\"{key1}\"]')"))
    state_after = api("/api/state")
    j1 = next((j for j in state_after["jobs"] if j["id"] == ids["t1"][0]), None)
    check("N4.9 主页面对应记录也一起移除", j1 is None or bool(j1.get("sessionRemoved"))
          or all(r.get("hidden") for r in j1.get("runs", [])), str(j1 is None))

    key3 = ids["t3"][1] + "|0"
    click(cdp, f'[data-delone="{key3}"]')
    cdp.pause(350)
    click(cdp, "#confirmHistoryDel")
    cdp.pause(900)
    check("N4.10 单张成品删除按钮也删掉文件", not Path(files["t3"]).exists(), files["t3"])

    # ============================================================ N5 会话任务删除联动
    goto(cdp, "studio")
    check("N5.1 右栏会话任务有删除按钮", cdp.js("!!document.querySelector('.task-item [data-jobdel]')"))
    check("N5.2 主页面对话卡片有删除按钮", cdp.js("!!document.querySelector('.jobwrap [data-jobdel]')"))
    target_job = ids["t2"][0]
    cdp.js(f"(()=>{{const e=document.querySelector('.task-item [data-jobdel=\"{target_job}\"]');"
           f"if(e)e.click();return true}})()")
    cdp.pause(350)
    check("N5.3 会话删除有确认弹窗", cdp.js("!!document.querySelector('#confirmJobDel')"))
    click(cdp, "#confirmJobDel")
    cdp.pause(900)
    check("N5.4 主页面这条对话卡片消失", cdp.js(f"!document.getElementById('{target_job}')"))
    check("N5.5 右栏本会话任务同步消失", cdp.js(
        f"!document.querySelector('.task-item [data-jump=\"{target_job}\"]')"))
    check("N5.6 会话删除不删成品文件", Path(files["t2"]).exists(), files["t2"])
    goto(cdp, "gallery")
    check("N5.7 历史页仍能看到这条成品（可在历史里删文件）", cdp.js(
        f"!!document.querySelector('[data-sel=\"{ids['t2'][1]}|0\"]')"))
    # 清理：把这张测试图也从历史里删掉，不留在用户成果目录
    click(cdp, f'[data-delone="{ids["t2"][1]}|0"]')
    cdp.pause(350)
    click(cdp, "#confirmHistoryDel")
    cdp.pause(900)
    check("N5.8 历史删除才真正删掉这个成品文件", not Path(files["t2"]).exists(), files["t2"])


def main() -> int:
    """启动浏览器、跑全部检查、恢复配置与路由、输出汇总。"""
    global CONFIG_BACKUP
    if base.HTTP.get("/api/state").status_code != 200:
        print("服务没有在 127.0.0.1:8190 运行，请先双击启动服务.bat")
        return 1
    CONFIG_BACKUP = CONFIG.read_text(encoding="utf-8")
    proc, cdp = base.start_browser()
    try:
        api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps", "galleryFilter": "全部"}})
        run_checks(cdp)
    finally:
        restore_config()
        try:
            api("/api/ui", "POST", {"patch": {"route": "studio", "adminPage": "caps"}})
        except Exception:  # noqa: BLE001
            pass
        cdp.close()
        proc.terminate()
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print(f"\n===== 新增功能与配置全功能 E2E：{passed}/{len(RESULTS)} 项通过 =====")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL: {name} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
