"""1.03 宫格分镜故事板：接口 + 真实页面回归。

默认不生成图片（几十秒跑完），只验证提示词拼装、组合档位、创作台控件、
管理台档位表与「基于上一条」。加 `--gen` 会真发一张四宫格并核对出图尺寸。

用法：
    .venv\\Scripts\\python.exe tests\\board_e2e_test.py
    .venv\\Scripts\\python.exe tests\\board_e2e_test.py --gen
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ext_sheet_e2e import CDP, click, q, start_browser  # noqa: E402

BASE = "http://127.0.0.1:8190"
C = httpx.Client(base_url=BASE, timeout=120)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {name} — {detail}")


def api(path: str, body: dict | None = None, method: str = "GET") -> dict:
    if method == "GET":
        r = C.get(path)
    else:
        r = C.put(path, json=body) if method == "PUT" else C.post(path, json=body)
    return r.json()


# ---------------------------------------------------------------- 接口层
def test_api() -> None:
    st = api("/api/state")
    lib = st.get("boardLib") or {}
    combos = st.get("boardCombos") or []
    check("状态里有故事板提示词库", len(lib.get("layouts") or []) == 3, str([l["id"] for l in lib.get("layouts") or []]))
    check("状态里有 6 个组合", len(combos) == 6, str([c["id"] for c in combos]))
    check("每个组合都有档位", all(len(c.get("grades") or []) >= 1 for c in combos))

    byid = {c["id"]: c for c in combos}
    check("四宫格 16:9 · 每格 16:9", "1536" in str(byid["G4.color"]["size"]) and "864" in str(byid["G4.color"]["size"]),
          str(byid["G4.color"]["size"]))
    check("六宫格宽幅 8:3", "2304" in str(byid["G6.color"]["size"]) and "864" in str(byid["G6.color"]["size"]),
          str(byid["G6.color"]["size"]))
    check("六宫格走宽幅专用工作流", byid["G6.color"]["wf"] == "故事板-参考生图-宽幅6格", str(byid["G6.color"]["wf"]))
    check("四/九宫格走参考生图", byid["G4.color"]["wf"] == byid["G9.line"]["wf"] == "故事板-参考生图")

    cells4 = ["清晨的站台，短发女孩回头看向镜头，中景", "她穿过雨中的街道，全景", "", ""]
    p = api("/api/board/preview", {"layout": "G4", "ink": "color", "lineStyle": None,
                                   "grade": "默认", "cells": cells4, "refs": []}, "POST")
    prompt = (p.get("data") or {}).get("prompt") or ""
    check("预览接口可用", p.get("ok") is True and len(prompt) > 300, str(p.get("reason") or len(prompt)))
    check("提示词写明 2 行 2 列 4 格", "2 行 2 列" in prompt and "4 个等大的长方形格子" in prompt)
    check("空白格写成刻意的纯白", prompt.count("这一格刻意完全留空") == 2)
    check("彩色收尾", "彩色成片" in prompt)
    check("不画矩形边框", "绝对不要给任何一格画黑色矩形边框" in prompt)
    check("返回组合与工作流", (p["data"].get("combo") or {}).get("id") == "G4.color")

    pl = api("/api/board/preview", {"layout": "G9", "ink": "line", "lineStyle": "W3",
                                    "grade": "默认", "cells": ["甲", "乙", "丙"], "refs": []}, "POST")
    lprompt = (pl.get("data") or {}).get("prompt") or ""
    check("线稿首句强制黑白", lprompt.startswith("这是一张纯黑白线稿"))
    check("线稿收尾再查一遍颜色", "不允许出现任何颜色" in lprompt)
    check("九宫格 3 行 3 列", "3 行 3 列" in lprompt and "共 9 个" in lprompt)

    pr = api("/api/board/preview", {"layout": "G4", "ink": "color", "grade": "默认",
                                    "cells": ["图1 的女孩站在站台", "", "", ""], "refs": ["ref_x"]}, "POST")
    rprompt = (pr.get("data") or {}).get("prompt") or ""
    check("引用图号时锁身份", "以图 1 中的角色或主体为身份基准" in rprompt)
    check("带参考图时声明别照搬", "严禁把任何参考图整张复制" in rprompt)

    empty = api("/api/board/preview", {"layout": "G4", "ink": "color", "grade": "默认",
                                       "cells": ["", "", "", ""], "refs": []}, "POST")
    check("一格都没写要拒绝", empty.get("ok") is False and "至少写一格" in str(empty.get("reason")),
          str(empty.get("reason")))

    bad = api("/api/board/preview", {"layout": "G4", "ink": "color", "grade": "不存在",
                                     "cells": ["甲"], "refs": []}, "POST")
    check("档位不存在要拒绝", bad.get("ok") is False and "没有档位" in str(bad.get("reason")),
          str(bad.get("reason")))

    # 档位保存 → 出现 → 复原
    g = byid["G4.color"]["grades"]
    keep = [{"name": x["name"], "note": x.get("note", ""), "params": x.get("params", {})}
            for x in g if not x.get("locked")]
    trial = keep + [{"name": "测试档", "note": "回归测试", "params": {"502": {"megapixels": 0.6}}}]
    saved = api("/api/board/combos/G4.color/grades", {"grades": trial}, "PUT")
    names = [x["name"] for x in (api("/api/state").get("boardCombos") or [])[0]["grades"]]
    check("保存新档位", saved.get("ok") is True and "测试档" in names, str(names))
    trial2 = [x for x in trial if x["name"] != "测试档"]
    api("/api/board/combos/G4.color/grades", {"grades": trial2}, "PUT")
    names2 = [x["name"] for x in (api("/api/state").get("boardCombos") or [])[0]["grades"]]
    check("删除测试档位", "测试档" not in names2, str(names2))


# ---------------------------------------------------------------- 页面层
def test_ui() -> None:
    proc, cdp = start_browser()
    try:
        assert cdp.wait("typeof UI!=='undefined' && !!S && (S.boardCombos||[]).length===6", 30)
        # 上一轮测试可能停在管理台：先回创作台再点左栏
        cdp.js("location.hash='#studio'")
        cdp.pause(600)
        cdp.js("(()=>{UI._userSwitched=false; render(); return true})()")
        cdp.pause(300)
        click(cdp, '[data-cap="board.story"]')
        assert cdp.wait("UI.capId==='board.story' && !!document.querySelector('#boardCellInput')", 20)
        # 上一轮测试会在服务端留下草稿（版式/画法/各格文字），先复位成初始值再断言
        cdp.js("""(()=>{UI._draftCap=UI.capId; UI.boardOpen=false; UI.boardPreview=null;
          UI.boardForm={layout:'G4',ink:'color',lineStyle:'',grade:'默认',sel:0,cells:[]};
          render(); return true})()""")
        cdp.pause(500)

        ui = cdp.js("""(()=>({layout:UI.boardForm.layout, ink:UI.boardForm.ink,
          layouts:[...document.querySelectorAll('[data-board-layout]')].map(b=>b.dataset.boardLayout),
          inks:[...document.querySelectorAll('[data-board-ink]')].map(b=>b.dataset.boardInk),
          cells:[...document.querySelectorAll('[data-board-sel]')].length,
          grades:[...document.querySelectorAll('[data-board-grade]')].map(b=>b.dataset.boardGrade),
          send:document.querySelector('#sendBtn').disabled}))()""")
        check("创作台默认四宫格", ui["layout"] == "G4" and ui["cells"] == 4, str(ui["cells"]))
        check("三种版式可选", ui["layouts"] == ["G4", "G6", "G9"], str(ui["layouts"]))
        check("彩色/线稿两档", ui["inks"] == ["color", "line"], str(ui["inks"]))
        check("档位按钮齐", ui["grades"] == ["默认", "快速", "精细"], str(ui["grades"]))
        check("没写字时发送不可点", ui["send"] is True)

        click(cdp, '[data-board-layout="G6"]')
        cdp.wait("UI.boardForm.layout==='G6'", 10)
        check("切六宫格变 6 格", cdp.js("document.querySelectorAll('[data-board-sel]').length") == 6)

        click(cdp, '[data-board-ink="line"]')
        cdp.wait("UI.boardForm.ink==='line'", 10)
        check("切线稿出现风格下拉", cdp.js("!!document.querySelector('#boardLine')") is True)

        click(cdp, '[data-board-layout="G4"]')
        cdp.wait("UI.boardForm.layout==='G4'", 10)
        cdp.js("""(()=>{const t=document.querySelector('#boardCellInput');
          t.value='清晨的站台，短发女孩回头看向镜头，中景';
          t.dispatchEvent(new Event('input',{bubbles:true}));return true})()""")
        cdp.pause(300)
        typed = cdp.js("""(()=>({cell0:String((UI.boardForm.cells||[])[0]||''),
          send:document.querySelector('#sendBtn').disabled}))()""")
        check("第 1 格输入写进草稿", "站台" in typed["cell0"], typed["cell0"])
        check("写完后发送可点", typed["send"] is False)

        click(cdp, '[data-board-sel="2"]')
        cdp.wait("UI.boardForm.sel===2", 10)
        cdp.js("""(()=>{const t=document.querySelector('#boardCellInput');
          t.value='她穿过雨中的街道，脚下积水倒映霓虹，全景';
          t.dispatchEvent(new Event('input',{bubbles:true}));return true})()""")
        cdp.pause(300)
        cells = cdp.js("UI.boardForm.cells")
        check("第 3 格独立保存", "积水倒映霓虹" in str(cells[2]) and "站台" in str(cells[0]), str(cells))

        click(cdp, "#boardPvBtn")
        cdp.wait("UI.boardOpen===true && !!document.querySelector('#boardPv pre')", 25)
        pv = cdp.js("""(()=>({text:document.querySelector('#boardPv pre').textContent,
          size:(UI.boardPreview||{}).size, wf:(UI.boardPreview||{}).wf}))()""")
        check("看完整提示词面板", "逐格画面" in pv["text"] and "第 一 格" in pv["text"])
        check("面板标注出图尺寸", "1536" in str(pv["size"]), str(pv["size"]))

        # 管理台档位表
        click(cdp, '.topbar [data-nav="admin"]')
        assert cdp.wait("UI.route==='admin'", 20)
        click(cdp, '[data-page="grades"]')
        cdp.wait("UI.adminPage==='grades'", 10)
        click(cdp, '[data-grades-cap="board.story"]')
        ready = cdp.wait("UI.capId==='board.story' && !!document.querySelector('[data-combo-mode]')", 25)
        check("进到故事板档位表", ready, str(cdp.js(
            "(()=>({page:UI.adminPage,cap:UI.capId,h2:document.querySelector('.admin-main h2')?.textContent}))()")))
        admin = cdp.js("""(()=>({modes:[...document.querySelectorAll('[data-combo-mode]')].map(b=>b.dataset.comboMode),
          layouts:[...document.querySelectorAll('[data-combo-layout]')].map(b=>b.dataset.comboLayout),
          eff:[...document.querySelectorAll('.effhd')].map(x=>x.className)}))()""")
        check("档位表按画法分组", admin["modes"] == ["color", "line"], str(admin["modes"]))
        check("档位表按版式分组", admin["layouts"] == ["G4", "G6", "G9"], str(admin["layouts"]))
        cdp.wait("!!(UI.comboDetail && (UI.comboDetail.rows||[]).length)", 25)
        eff = cdp.js("(UI.comboDetail.rows||[]).map(r=>[r.name,r.allOk])")
        check("档位带参数生效核对", all(x[1] for x in eff), str(eff))

        click(cdp, '[data-combo-layout="G9"]')
        cdp.wait("UI.comboSel==='G9.color'", 15)
        click(cdp, '[data-combo-mode="line"]')
        cdp.wait("UI.comboSel==='G9.line'", 15)
        check("画法×版式可切到九宫格线稿", cdp.js("UI.comboSel") == "G9.line")

        click(cdp, "[data-combo-studio]")
        cdp.wait("UI.route==='studio' && UI.capId==='board.story'", 20)
        back = cdp.js("(()=>({layout:UI.boardForm.layout,ink:UI.boardForm.ink,cells:UI.boardForm.cells.length}))()")
        check("去创作台带回版式与画法",
              back["layout"] == "G9" and back["ink"] == "line" and back["cells"] == 9, str(back))
    finally:
        cdp.close()
        proc.terminate()


# ---------------------------------------------------------------- 真出图
def test_gen() -> None:
    body = {"capId": "board.story", "grade": "默认", "wf": "故事板-参考生图", "prompt": "测试",
            "refs": [],
            "board": {"layout": "G4", "ink": "color", "lineStyle": None, "grade": "默认", "refs": [],
                      "cells": ["清晨的站台，短发女孩回头看向镜头，中景",
                                "她穿过雨中的街道，脚下积水倒映霓虹，全景", "", ""]}}
    res = api("/api/tasks", body, "POST")
    check("提交故事板任务", res.get("ok") is True, str(res.get("reason")))
    jid = (res.get("data") or {}).get("job_id")
    if not jid:
        return
    for _ in range(120):
        st = api("/api/state")
        job = next((x for x in st["jobs"] if x["id"] == jid), None)
        run = (job or {}).get("runs", [{}])[-1]
        if run.get("state") in ("done", "failed", "canceled"):
            break
        time.sleep(3)
    check("出图成功", run.get("state") == "done", json.dumps(run.get("err") or {}, ensure_ascii=False)[:200])
    files = run.get("files") or []
    check("成品尺寸 1536×864", bool(files) and files[0].get("width") == 1536 and files[0].get("height") == 864,
          str((files[0] if files else {}).get("width")))
    check("归档到能力目录", "宫格分镜故事板" in str((files[0] if files else {}).get("path")))


def main() -> int:
    print("=== 接口 ===")
    test_api()
    print("=== 页面 ===")
    test_ui()
    if "--gen" in sys.argv:
        print("=== 真出图 ===")
        test_gen()
    print(f"\n===== {PASS}/{PASS + FAIL} 项通过 =====")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
