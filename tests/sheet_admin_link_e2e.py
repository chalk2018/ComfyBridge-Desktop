"""管理台 ↔ 创作台 联动的页面操作验证（真实点击，不直接调接口）。

验证四件事：
  1. 管理台「设定图模板」改风格预设 → 创作台「看完整提示词」立刻跟着变；
  2. 管理台改线稿风格句 → 创作台线稿预览立刻跟着变；
  3. 管理台「填入创作台」/「去创作台试这个组合」把组合带回创作台表单；
  4. 管理台保存后，提示词库里新加的字段不会丢（保存是 merge，不是覆盖）。
改完的文案都会改回原值并保存，测试结束不留痕。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from sheet_full_e2e import (  # noqa: E402
    APP, CDP, click, clean_draft, configure, ensure_sheet, log, open_preview,
    q, read_state, set_sel, set_text, start_browser,
)

OUT = APP / "tests" / "_sheet_full_out"
REPORT = OUT / "admin_link.json"
ROWS: list[dict] = []


def rec(name: str, ok: bool, detail: str = "", prompt: str = "") -> None:
    ROWS.append({"item": name, "ok": bool(ok), "detail": detail, "prompt": prompt})
    log(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def goto_admin(cdp: CDP, page: str) -> bool:
    """切到管理台某个分页：导航是异步的（await pull() 之后才 render），必须等 DOM 真出现。"""
    if not cdp.js("UI.route==='admin'"):
        cdp.wait("!!document.querySelector('[data-nav=\"admin\"]')", 15)
        click(cdp, '[data-nav="admin"]')
        cdp.wait("UI.route==='admin'", 12)
    page_sel = '[data-page="' + page + '"]'
    cdp.wait(f"!!document.querySelector({q(page_sel)})", 15)
    if not cdp.js(f"UI.adminPage==={q(page)}"):
        click(cdp, page_sel)
        cdp.wait(f"UI.adminPage==={q(page)}", 15)
    # 分页自己的内容也要渲染完，不能只信 UI.adminPage
    marker = "[data-sheetlib-save]" if page == "sheet" else "[data-combo-studio]"
    cdp.wait(f"!!document.querySelector({q(marker)})", 15)
    cdp.pause(350)
    return bool(cdp.js(f"UI.route==='admin' && UI.adminPage==={q(page)}"))


def goto_studio(cdp: CDP) -> bool:
    if not cdp.js("UI.route==='studio'"):
        cdp.wait("!!document.querySelector('[data-nav=\"studio\"]')", 15)
        click(cdp, '[data-nav="studio"]')
    return bool(cdp.wait("UI.route==='studio' && !!document.querySelector('#sheetChar')", 15))


def settle_studio(cdp: CDP) -> bool:
    """「填入创作台」这类按钮内部是 await saveDraft() → pull() → render()，等页面真重绘完。"""
    return bool(cdp.wait("UI.route==='studio' && !!document.querySelector('#sheetChar')", 15))


def save_lib(cdp: CDP) -> bool:
    cdp.wait("!!document.querySelector('[data-sheetlib-save]')", 15)
    if not click(cdp, "[data-sheetlib-save]"):
        return False
    cdp.pause(1100)
    return True


def lib_file() -> dict:
    return json.loads((APP / "prompts" / "character_sheet.json").read_text(encoding="utf-8"))


def preview_with(cdp: CDP, needle: str, timeout: int = 20) -> str:
    """点「看完整提示词」，等到服务端返回的这条真的含 needle 再读。

    面板收起时改表单不会立刻重算预览，展开按钮里是一次异步请求；
    只等 `UI.sheetPreview.prompt` 存在会读到上一次的旧结果。
    """
    click(cdp, "#sheetPvBtn")
    cdp.wait(f"UI.sheetPreview && UI.sheetPreview.prompt && "
             f"UI.sheetPreview.prompt.includes({q(needle)})", timeout)
    cdp.pause(200)
    text = cdp.js("(UI.sheetPreview && UI.sheetPreview.prompt)||''") or ""
    cdp.js("(()=>{const b=document.querySelector('#sheetPvBtn'); "
           "if(b && b.textContent.includes('收起')) b.click(); return true;})()")
    cdp.pause(200)
    return text


def main() -> None:
    proc, cdp = start_browser()
    try:
        # 服务端 UI 状态是共享的：上一轮可能停在管理台，先回创作台再进设定图
        if not cdp.js("UI.route==='studio'"):
            click(cdp, '[data-nav="studio"]')
            cdp.wait("UI.route==='studio'", 12)
        if not ensure_sheet(cdp):
            log("FATAL 进不了角色设定图页面")
            return
        before_file = lib_file()

        # ---------- 1. 风格预设联动 ----------
        ok = goto_admin(cdp, "sheet")
        rec("进入管理台 · 设定图模板页", ok)
        st_sel = '[data-stt="0"]'
        old_style = cdp.js(f"(document.querySelector({q(st_sel)})||{{}}).value||''")
        style_name = cdp.js("(document.querySelector('[data-stn=\"0\"]')||{}).value||''")
        mark = "｜管理台联动标记K9"
        rec("读到第 1 类风格预设", bool(old_style and style_name), f"{style_name}: {old_style[:30]}")
        set_text(cdp, st_sel, old_style + mark)
        rec("管理台点「保存提示词库」", save_lib(cdp))
        synced = cdp.wait(f"S.sheetLib.styles[0].text.includes({q(mark)})", 12)
        rec("页面状态里的风格预设已更新", bool(synced))
        goto_studio(cdp)
        clean_draft(cdp)
        case = {"mode": "t2i", "layout": "A", "ink": "color", "lineStyle": "W1",
                "ref": "none", "grade": "默认", "style": style_name, "styleText": "",
                "character": "三十岁的城市测绘员，长脸细眉，短发向后梳，穿浅色立领衬衫、深色直筒长裤、哑光短靴，胸前别一枚黄铜指南针，袖口压一道细缝线"}
        configure(cdp, case)
        pv = preview_with(cdp, mark)
        rec("创作台彩色预览带上管理台改过的风格句", mark in pv, f"预览 {len(pv)} 字", pv)
        # 线稿：同一风格名，修复后应出现造型气质行
        w4_text = next(s["text"] for s in lib_file()["line_styles"] if s["id"] == "W4")
        click(cdp, '[data-sheet-ink="line"]')
        cdp.pause(350)
        set_text(cdp, "#sheetChar", case["character"])
        pv_line = preview_with(cdp, w4_text)
        rec("创作台线稿预览仍以线稿句为风格行", f"风格：{w4_text}" in pv_line,
            "记录修复后的线稿提示词", pv_line)
        rec("线稿提示词带上所选画风的造型气质",
            bool(style_name) and "造型气质：" in pv_line and style_name in pv_line,
            f"风格={style_name}", pv_line)

        # 恢复风格预设
        goto_admin(cdp, "sheet")
        set_text(cdp, st_sel, old_style)
        save_lib(cdp)
        rec("风格预设已改回原值", cdp.wait(f"!S.sheetLib.styles[0].text.includes({q(mark)})", 12))

        # ---------- 2. 线稿句联动 ----------
        ls_sel = '[data-ls="W4"]'
        old_ls = cdp.js(f"(document.querySelector({q(ls_sel)})||{{}}).value||''")
        lmark = "｜管理台联动标记L7"
        set_text(cdp, ls_sel, old_ls + lmark)
        save_lib(cdp)
        rec("管理台改线稿句后页面状态同步",
            bool(cdp.wait(f"S.sheetLib.line_styles[3].text.includes({q(lmark)})", 12)))
        goto_studio(cdp)
        clean_draft(cdp)
        case2 = dict(case, ink="line", lineStyle="W4")
        configure(cdp, case2)
        pv2 = preview_with(cdp, lmark)
        rec("创作台线稿预览带上管理台改过的线稿句", lmark in pv2, f"预览 {len(pv2)} 字", pv2)
        goto_admin(cdp, "sheet")
        set_text(cdp, ls_sel, old_ls)
        save_lib(cdp)
        rec("线稿句已改回原值", cdp.wait(f"!S.sheetLib.line_styles[3].text.includes({q(lmark)})", 12))

        # ---------- 3. 管理台 → 创作台 组合回填 ----------
        goto_admin(cdp, "sheet")
        cdp.wait("!!document.querySelector('[data-sheet-use=\"B|line|none\"]')", 15)
        click(cdp, '[data-sheet-use="B|line|none"]')
        settle_studio(cdp)
        cdp.pause(350)
        form = read_state(cdp, "return JSON.stringify(UI.sheetForm||{});")
        f = json.loads(form) if form else {}
        rec("「填入创作台」带回版式/画法/参考方式",
            f.get("layout") == "B" and f.get("ink") == "line" and UI_ok(cdp, "t2i"), str(f)[:220])

        # 档位表 → 去创作台试这个组合
        goto_admin(cdp, "grades")
        cdp.wait("!!document.querySelector('[data-combo-studio]') && !!UI.comboSel", 15)
        picked = cdp.js("UI.comboSel || ''")
        rec("进入管理台 · 档位表并选中一个组合", bool(picked), f"选中 {picked}")
        click(cdp, "[data-combo-studio]")
        settle_studio(cdp)
        cdp.pause(350)
        form2 = json.loads(read_state(cdp, "return JSON.stringify(UI.sheetForm||{});") or "{}")
        rec("档位表「去创作台试这个组合」带回组合", bool(picked) and form2.get("layout") == picked.split(".")[0],
            f"选中组合 {picked} → 表单 {str(form2)[:160]}")

        # ---------- 3b. 风格下拉「自定义… → 预设」切换（P2 复测） ----------
        goto_studio(cdp)
        clean_draft(cdp)
        click(cdp, '[data-sheet-ink="color"]')
        cdp.pause(300)
        set_text(cdp, "#sheetChar",
                 "二十九岁的植物标本师，长脸细眉，低束长发，穿浅色立领衬衫、深色长裤、布鞋，胸前别一枚铜制叶片胸针")
        custom_text = "水彩速写风，干笔触，边缘留白，低饱和暖灰"
        set_sel(cdp, "#sheetStyle", "")
        cdp.pause(350)
        set_text(cdp, "#sheetStyleText", custom_text)
        cdp.pause(300)
        custom_ui = json.loads(cdp.js("""JSON.stringify({
            value:(document.querySelector('#sheetStyle')||{}).value||'',
            custom:!!UI.sheetForm.styleCustom,
            shown:(()=>{const e=document.querySelector('#sheetStyleText');
                         return !!e && getComputedStyle(e).display!=='none';})()})""") or "{}")
        pv_custom = preview_with(cdp, custom_text)
        rec("选「自定义…」后下拉停在自定义态、输入框可见",
            custom_ui.get("custom") and custom_ui.get("value") == "" and custom_ui.get("shown"),
            str(custom_ui), pv_custom)
        rec("自定义风格句进入预览提示词", custom_text in pv_custom, f"预览 {len(pv_custom)} 字", pv_custom)
        set_sel(cdp, "#sheetStyle", "3D 动画")
        cdp.pause(350)
        preset_ui = json.loads(cdp.js("""JSON.stringify({
            value:(document.querySelector('#sheetStyle')||{}).value||'',
            custom:!!UI.sheetForm.styleCustom,
            styleText:UI.sheetForm.styleText||''})""") or "{}")
        pv_preset = preview_with(cdp, "皮克斯三维动画电影风格")
        rec("切回预设后下拉显示预设、自定义标记复位",
            preset_ui.get("value") == "3D 动画" and not preset_ui.get("custom")
            and not preset_ui.get("styleText"), str(preset_ui), pv_preset)
        rec("切回预设后预览用预设句、旧自定义句不再残留",
            "皮克斯三维动画电影风格" in pv_preset and custom_text not in pv_preset,
            "P2 复测", pv_preset)

        # ---------- 4. 保存是 merge：新字段不丢 ----------
        after_file = lib_file()
        new_keys = [k for k in before_file if k not in after_file]
        rec("管理台保存后提示词库顶层字段没有丢失", not new_keys,
            f"丢失字段 {new_keys}" if new_keys else f"共 {len(after_file)} 个顶层字段")
        for key in ("line_style_profiles", "line_style_overlay"):
            if key in before_file:
                rec(f"新增字段 {key} 在管理台保存后保留", after_file.get(key) == before_file.get(key))

        REPORT.write_text(json.dumps(ROWS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"汇总：{sum(1 for r in ROWS if r['ok'])}/{len(ROWS)} 通过 → {REPORT}")
    finally:
        cdp.close()
        proc.terminate()


def UI_ok(cdp: CDP, mode: str) -> bool:
    return bool(cdp.js(f"UI.mode==={q(mode)}"))


if __name__ == "__main__":
    main()
